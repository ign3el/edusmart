"""Credit ledger invariants - the money path.

Runs against the live MySQL (see conftest) but only ever against a throwaway
user it creates and deletes itself. It never reads, mutates, or asserts on a
real account.
"""
import uuid

import pytest
from fastapi import HTTPException

from routers.billing import check_and_reserve_credit, refund_credit


def _balance(db, uid):
    with db() as cur:
        cur.execute("SELECT credits_balance FROM users WHERE id = %s", (uid,))
        return cur.fetchone()["credits_balance"]


def _ledger_count(db, uid, reason=None):
    with db() as cur:
        if reason:
            cur.execute(
                "SELECT COUNT(*) c FROM credit_transactions WHERE user_id = %s AND reason = %s",
                (uid, reason),
            )
        else:
            cur.execute("SELECT COUNT(*) c FROM credit_transactions WHERE user_id = %s", (uid,))
        return cur.fetchone()["c"]


class TestDebit:
    def test_debit_reduces_balance_and_writes_ledger(self, db, temp_user):
        start = _balance(db, temp_user)
        check_and_reserve_credit(temp_user)
        assert _balance(db, temp_user) == start - 1
        assert _ledger_count(db, temp_user, "story_generated") == 1

    def test_blocks_at_zero_with_402(self, db, temp_user):
        with db(commit=True) as cur:
            cur.execute("UPDATE users SET credits_balance = 0 WHERE id = %s", (temp_user,))

        with pytest.raises(HTTPException) as exc:
            check_and_reserve_credit(temp_user)
        assert exc.value.status_code == 402
        assert _balance(db, temp_user) == 0, "balance must never go negative"

    def test_cannot_be_driven_negative_by_repeated_calls(self, db, temp_user):
        with db(commit=True) as cur:
            cur.execute("UPDATE users SET credits_balance = 2 WHERE id = %s", (temp_user,))

        succeeded = 0
        for _ in range(6):
            try:
                check_and_reserve_credit(temp_user)
                succeeded += 1
            except HTTPException:
                pass

        assert succeeded == 2, "debited more times than there were credits"
        assert _balance(db, temp_user) == 0


class TestSuspendedSubscription:
    def test_suspended_subscriber_cannot_spend(self, db, temp_user):
        """A subscriber whose payment failed keeps their balance on the books
        but must not be able to spend it."""
        with db(commit=True) as cur:
            cur.execute(
                "UPDATE users SET subscription_tier = 'pro', subscription_status = 'past_due' "
                "WHERE id = %s",
                (temp_user,),
            )

        with pytest.raises(HTTPException) as exc:
            check_and_reserve_credit(temp_user)
        assert exc.value.status_code == 402
        assert _balance(db, temp_user) == 5, "balance must be untouched by a blocked spend"

    def test_free_tier_is_unaffected_by_subscription_status(self, db, temp_user):
        with db(commit=True) as cur:
            cur.execute(
                "UPDATE users SET subscription_tier = 'free', subscription_status = 'past_due' "
                "WHERE id = %s",
                (temp_user,),
            )
        check_and_reserve_credit(temp_user)  # must not raise
        assert _balance(db, temp_user) == 4


class TestAdminIsUnmetered:
    def test_admin_is_neither_debited_nor_refunded(self, db, temp_user):
        with db(commit=True) as cur:
            cur.execute("UPDATE users SET is_admin = 1 WHERE id = %s", (temp_user,))

        check_and_reserve_credit(temp_user)
        assert _balance(db, temp_user) == 5, "admin must not be debited"

        refund_credit(temp_user, f"story-{uuid.uuid4()}")
        assert _balance(db, temp_user) == 5, (
            "admin must not be refunded either - never debited, so a refund "
            "would mint a free credit on every failed job"
        )


class TestRefundIdempotency:
    """Regression: refund_credit had no DB-level guard. Only careful call
    ordering at startup prevented a double refund, and a convention is not an
    invariant. A double refund mints a credit the user never paid for."""

    def test_repeated_refund_for_same_story_credits_once(self, db, temp_user):
        story_id = f"pytest-story-{uuid.uuid4()}"
        start = _balance(db, temp_user)

        refund_credit(temp_user, story_id)
        refund_credit(temp_user, story_id)
        refund_credit(temp_user, story_id)

        assert _balance(db, temp_user) == start + 1, "duplicate refunds minted credits"
        assert _ledger_count(db, temp_user, "generation_failed_refund") == 1

    def test_distinct_stories_each_refund(self, db, temp_user):
        start = _balance(db, temp_user)
        refund_credit(temp_user, f"pytest-story-{uuid.uuid4()}")
        refund_credit(temp_user, f"pytest-story-{uuid.uuid4()}")
        assert _balance(db, temp_user) == start + 2

    def test_ledger_row_precedes_the_credit(self, db, temp_user):
        """The insert must be what gates the refund. If the balance were updated
        first, two concurrent refunds could both credit before either failed
        the uniqueness check."""
        story_id = f"pytest-story-{uuid.uuid4()}"
        refund_credit(temp_user, story_id)
        with db() as cur:
            cur.execute(
                "SELECT delta, story_id FROM credit_transactions "
                "WHERE user_id = %s AND story_id = %s",
                (temp_user, story_id),
            )
            row = cur.fetchone()
        assert row and row["delta"] == 1
