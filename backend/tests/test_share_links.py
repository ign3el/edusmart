"""is_shared/share_created_at on StoryOperations.get_user_stories - the fields
LoadStory.jsx's Shared Links tab filters on. See routers/share.py for the
create/revoke logic itself, already covered by its own docstrings/behavior;
this only guards the list-view projection added alongside the new tab.
"""
import json
import uuid

from database_models import StoryOperations


def _make_story(db, user_id):
    story_id = f"pytest-edusmart-{uuid.uuid4().hex[:12]}"
    with db(commit=True) as cur:
        cur.execute(
            "INSERT INTO user_stories (user_id, story_id, name, story_data) "
            "VALUES (%s, %s, %s, %s)",
            (user_id, story_id, "Test Story", json.dumps({})),
        )
    return story_id


class TestSharedStateInStoryList:
    def test_fresh_story_is_not_shared(self, db, temp_user):
        story_id = _make_story(db, temp_user)

        stories = StoryOperations.get_user_stories(temp_user)
        row = next(s for s in stories if s["story_id"] == story_id)

        assert not row["is_shared"]
        assert row["share_created_at"] is None

    def test_shared_story_is_flagged_with_timestamp(self, db, temp_user):
        story_id = _make_story(db, temp_user)
        owner = {"id": temp_user, "is_admin": False}

        assert StoryOperations.create_share_token(story_id, owner)

        stories = StoryOperations.get_user_stories(temp_user)
        row = next(s for s in stories if s["story_id"] == story_id)

        assert row["is_shared"]
        assert row["share_created_at"] is not None

    def test_revoked_story_drops_back_to_unshared(self, db, temp_user):
        story_id = _make_story(db, temp_user)
        owner = {"id": temp_user, "is_admin": False}

        StoryOperations.create_share_token(story_id, owner)
        assert StoryOperations.revoke_share_token(story_id, owner) is True

        stories = StoryOperations.get_user_stories(temp_user)
        row = next(s for s in stories if s["story_id"] == story_id)

        assert not row["is_shared"]
        assert row["share_created_at"] is None
