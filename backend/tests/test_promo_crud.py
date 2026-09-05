"""Promo code admin CRUD: create/edit/reset-redemptions/delete.

Runs against the live MySQL (see conftest) but only ever against throwaway
promo codes and users it creates and deletes itself. Endpoint functions are
called directly (not via TestClient) - this bypasses the get_admin_user
Depends() gate the same way test_credits.py bypasses get_current_user, since
that gate is already covered separately by the hostile-E2E authz pass. This
suite is only checking the CRUD logic itself.
"""
import asyncio
import uuid

import pytest
from fastapi import HTTPException

from routers.admin import (
    admin_create_promo_code,
    admin_update_promo_code,
    admin_delete_promo_code,
    admin_reset_promo_redemptions,
    PromoCodeCreateRequest,
    PromoCodeUpdateRequest,
)
from routers.billing import _get_promo, _validate_promo

ADMIN = {"id": 0, "is_admin": True}


@pytest.fixture
def promo_code(db):
    code = f"PYTESTPROMO{uuid.uuid4().hex[:8].upper()}"
    yield code
    with db(commit=True) as cur:
        cur.execute("DELETE FROM promo_redemptions WHERE code = %s", (code,))
        cur.execute("DELETE FROM promo_codes WHERE code = %s", (code,))


class TestPromoCrud:
    def test_create_then_edit_then_delete(self, db, promo_code):
        payload = PromoCodeCreateRequest(
            code=promo_code, discount_type="free_credits", discount_value=3,
            max_redemptions=5, max_redemptions_per_user=1,
        )
        result = asyncio.run(admin_create_promo_code(payload, ADMIN))
        assert result["success"] is True

        with db() as cur:
            cur.execute("SELECT * FROM promo_codes WHERE code = %s", (promo_code,))
            row = cur.fetchone()
        assert row["discount_value"] == 3

        update = PromoCodeUpdateRequest(discount_value=7, max_redemptions=10)
        asyncio.run(admin_update_promo_code(promo_code, update, ADMIN))
        with db() as cur:
            cur.execute("SELECT * FROM promo_codes WHERE code = %s", (promo_code,))
            row = cur.fetchone()
        assert row["discount_value"] == 7
        assert row["max_redemptions"] == 10

        asyncio.run(admin_delete_promo_code(promo_code, ADMIN))
        with db() as cur:
            cur.execute("SELECT * FROM promo_codes WHERE code = %s", (promo_code,))
            assert cur.fetchone() is None

    def test_delete_missing_code_404s(self, db):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(admin_delete_promo_code("PYTESTPROMO-DOES-NOT-EXIST", ADMIN))
        assert exc.value.status_code == 404

    def test_reset_missing_code_404s(self, db):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(admin_reset_promo_redemptions("PYTESTPROMO-DOES-NOT-EXIST", ADMIN))
        assert exc.value.status_code == 404

    def test_reset_redemptions_clears_history_and_allows_reuse(self, db, promo_code, temp_user):
        payload = PromoCodeCreateRequest(
            code=promo_code, discount_type="free_credits", discount_value=2,
            max_redemptions=None, max_redemptions_per_user=1,
        )
        asyncio.run(admin_create_promo_code(payload, ADMIN))

        with db(commit=True) as cur:
            cur.execute(
                "INSERT INTO promo_redemptions (code, user_id, credits_granted) VALUES (%s, %s, %s)",
                (promo_code, temp_user, 2),
            )
            cur.execute("UPDATE promo_codes SET times_redeemed = 1 WHERE code = %s", (promo_code,))

        # Before reset: a user who already redeemed is blocked, matching prod behavior.
        promo = _get_promo(promo_code)
        with db() as cur, pytest.raises(HTTPException):
            _validate_promo(cur, promo, temp_user)

        result = asyncio.run(admin_reset_promo_redemptions(promo_code, ADMIN))
        assert result["redemptions_cleared"] == 1

        with db() as cur:
            cur.execute("SELECT COUNT(*) c FROM promo_redemptions WHERE code = %s", (promo_code,))
            assert cur.fetchone()["c"] == 0
            cur.execute("SELECT times_redeemed FROM promo_codes WHERE code = %s", (promo_code,))
            assert cur.fetchone()["times_redeemed"] == 0

        # After reset: the same user can be validated for redemption again.
        promo = _get_promo(promo_code)
        with db() as cur:
            _validate_promo(cur, promo, temp_user)  # should not raise

    def test_delete_cascades_redemptions(self, db, promo_code, temp_user):
        payload = PromoCodeCreateRequest(
            code=promo_code, discount_type="free_credits", discount_value=1,
            max_redemptions=None, max_redemptions_per_user=1,
        )
        asyncio.run(admin_create_promo_code(payload, ADMIN))
        with db(commit=True) as cur:
            cur.execute(
                "INSERT INTO promo_redemptions (code, user_id, credits_granted) VALUES (%s, %s, %s)",
                (promo_code, temp_user, 1),
            )

        asyncio.run(admin_delete_promo_code(promo_code, ADMIN))

        with db() as cur:
            cur.execute("SELECT COUNT(*) c FROM promo_redemptions WHERE code = %s", (promo_code,))
            assert cur.fetchone()["c"] == 0, "ON DELETE CASCADE should have removed redemption rows"
