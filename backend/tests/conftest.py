"""Shared fixtures.

These tests run against the REAL production dependency set (they execute inside
a throwaway container built from the backend image - see run-tests.sh), which
means the MySQL they see is the live database. Every DB-touching fixture here
therefore creates its own clearly-namespaced rows and deletes them in teardown.
Nothing in this suite mutates or deletes a row it did not create.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, "/app")

# Marker for anything this suite creates, so a failed teardown leaves an
# obvious, greppable trail rather than anonymous junk.
TEST_PREFIX = "pytest-edusmart-"


@pytest.fixture(scope="session")
def db():
    from database import get_db_cursor
    return get_db_cursor


@pytest.fixture
def temp_user(db):
    """A throwaway non-admin user with a known credit balance.

    Credit tests must not run against a real account: they assert on exact
    balances, and a real user generating a story mid-test would make the
    assertion flap. This user exists only for the duration of one test.
    """
    from database_models import UserOperations

    email = f"{TEST_PREFIX}{uuid.uuid4().hex[:12]}@example.invalid"
    username = f"{TEST_PREFIX}{uuid.uuid4().hex[:8]}"
    user = UserOperations.create(email=email, username=username, password="Throwaway!123")
    assert user, "could not create temp user"
    uid = user["id"]

    with db(commit=True) as cur:
        cur.execute("UPDATE users SET credits_balance = 5, is_admin = 0 WHERE id = %s", (uid,))

    yield uid

    with db(commit=True) as cur:
        cur.execute("DELETE FROM credit_transactions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM user_stories WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))


@pytest.fixture
def story_service():
    """StoryService with the network calls stubbed.

    Constructed normally (so __init__ wiring is itself under test), then the
    two outbound calls are replaced. A unit test must never spend real API
    quota or depend on a third party being up.
    """
    from services.story_service import StoryService

    svc = StoryService()
    svc._vision_read_image = lambda b, mime="image/png": "stubbed page text"
    return svc


@pytest.fixture
def sample_pdf_bytes():
    """A small, real, multi-page PDF generated on the fly."""
    import fitz

    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 100), f"Page {i + 1}: photosynthesis converts light to chemical energy.")
    data = doc.tobytes()
    doc.close()
    return data
