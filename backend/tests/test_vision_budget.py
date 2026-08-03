"""Daily vision-call budget: per-user and global ceilings.

The page cap bounds ONE document; this bounds the day. Without it a user can
upload twenty capped documents and still drain a shared daily API quota that
every other user depends on.
"""
import importlib
import json
import os

import pytest


@pytest.fixture
def budget(tmp_path, monkeypatch):
    """A fresh budget module pointed at a temp counter file.

    The real counter lives in db_data/ and is shared with the running app;
    tests must never touch it or they would consume production quota headroom.

    Teardown restores the module's original values. importlib.reload mutates
    the module object IN PLACE, and story_service holds a reference to that
    same object (`from services import vision_budget`) - so without a restore,
    every test running after this one inherits a cap of 10 and a stale temp
    counter file. That is exactly what happened on first run: five extraction
    tests passed in isolation and failed in the full suite.
    """
    import services.vision_budget as vb

    original = (vb.VISION_DAILY_CAP, vb.VISION_DAILY_CAP_PER_USER, vb._USAGE_FILE)

    monkeypatch.setenv("VISION_DAILY_CAP", "10")
    monkeypatch.setenv("VISION_DAILY_CAP_PER_USER", "4")
    importlib.reload(vb)
    vb._USAGE_FILE = str(tmp_path / "vision_usage.json")

    yield vb

    vb.VISION_DAILY_CAP, vb.VISION_DAILY_CAP_PER_USER, vb._USAGE_FILE = original


class TestPerUserCap:
    def test_grants_up_to_the_user_limit(self, budget):
        assert budget.reserve(3, user_id=1) == (3, "")

    def test_partial_grant_when_user_limit_is_close(self, budget):
        budget.reserve(3, user_id=1)
        granted, reason = budget.reserve(5, user_id=1)
        assert granted == 1, "should grant the 1 remaining, not all 5 and not 0"
        assert "per-user" in reason

    def test_zero_once_user_limit_is_spent(self, budget):
        budget.reserve(4, user_id=1)
        granted, reason = budget.reserve(2, user_id=1)
        assert granted == 0
        assert "per-user" in reason

    def test_users_have_independent_budgets(self, budget):
        budget.reserve(4, user_id=1)
        granted, _ = budget.reserve(4, user_id=2)
        assert granted == 4, "one user exhausting their quota must not block another"


class TestGlobalCap:
    def test_global_cap_stops_everyone(self, budget):
        # cap 10 global, 4 per user -> three users can take 4+4+2
        assert budget.reserve(4, user_id=1)[0] == 4
        assert budget.reserve(4, user_id=2)[0] == 4
        granted, reason = budget.reserve(4, user_id=3)
        assert granted == 2, "third user should get only the 2 remaining globally"
        assert "global" in reason

        granted, reason = budget.reserve(1, user_id=4)
        assert granted == 0
        assert "global" in reason


class TestPersistence:
    def test_counter_survives_a_reload(self, budget):
        budget.reserve(3, user_id=1)
        data = json.load(open(budget._USAGE_FILE))
        assert data["total"] == 3
        assert data["per_user"]["1"] == 3

    def test_reservation_is_written_before_the_call_not_after(self, budget):
        """Reserve-before-spend: the count must already be persisted when
        reserve() returns, or concurrent callers all read a stale value and
        all pass the check."""
        budget.reserve(2, user_id=9)
        assert json.load(open(budget._USAGE_FILE))["total"] == 2

    def test_new_day_resets(self, budget, monkeypatch):
        budget.reserve(4, user_id=1)
        # Simulate the stored counter being from yesterday.
        data = json.load(open(budget._USAGE_FILE))
        data["day"] = "1999-01-01"
        json.dump(data, open(budget._USAGE_FILE, "w"))
        assert budget.reserve(4, user_id=1) == (4, ""), "a new day must start clean"


class TestDefensiveBehaviour:
    def test_zero_request_is_a_no_op(self, budget):
        assert budget.reserve(0, user_id=1) == (0, "")
        assert not os.path.exists(budget._USAGE_FILE), "no reservation, no write"

    def test_corrupt_counter_file_does_not_crash(self, budget):
        open(budget._USAGE_FILE, "w").write("{not json")
        granted, _ = budget.reserve(2, user_id=1)
        assert granted == 2, "a corrupt counter should fail open, not block all users"

    def test_inline_comment_in_env_value_is_tolerated(self, monkeypatch):
        """A .env written as `VISION_DAILY_CAP=400  # per day` must not turn a
        cap into an import-time crash - the same class of bug that took
        config.CACHE_TTL down under `docker run --env-file`."""
        import services.vision_budget as vb

        original = (vb.VISION_DAILY_CAP, vb.VISION_DAILY_CAP_PER_USER, vb._USAGE_FILE)
        try:
            monkeypatch.setenv("VISION_DAILY_CAP", "250  # per day")
            importlib.reload(vb)
            assert vb.VISION_DAILY_CAP == 250
        finally:
            # Same in-place-mutation hazard as the `budget` fixture above.
            vb.VISION_DAILY_CAP, vb.VISION_DAILY_CAP_PER_USER, vb._USAGE_FILE = original

    def test_snapshot_reports_usage(self, budget):
        budget.reserve(3, user_id=7)
        snap = budget.snapshot()
        assert snap["used"] == 3
        assert snap["cap"] == 10
        assert snap["remaining"] == 7
        assert ("7", 3) in snap["top_users"]
