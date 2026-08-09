"""Story -> video export: the ffmpeg pipeline in services/video_service.py,
the story_videos job-state additions in job_state.py, and the ownership /
precondition / dedupe rules in routers/video.py.

Runs against the real ffmpeg binary baked into this test image (no mocking
the render itself - see debug-loop-must-hit-real-app project convention) and
real tiny fixtures: a 1x1 PNG and a silent WAV of a known exact duration, so
the produced video's duration can be asserted against something concrete
instead of merely "the process exited 0".
"""
import asyncio
import base64
import io
import json
import os
import uuid
import wave
from pathlib import Path

import pytest
from fastapi import HTTPException

from database_models import UserOperations
from job_state import job_manager
from routers.video import generate_video
from services.video_queue import video_queue
from services.video_service import VideoRenderError, render_story_video
from services.video_service import _probe_duration as _ffprobe_duration
from story_storage import storage_manager

# A valid 1x1 black-pixel PNG. Needs to be genuinely decodable by ffmpeg, not
# merely have PNG-looking magic bytes.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _make_wav_bytes(duration_s: float, sample_rate: int = 8000) -> bytes:
    """A real, valid silent WAV of an exact duration - ffprobe reads this
    back precisely, which is what lets the render-duration assertion below
    be a real check rather than a fuzzy one."""
    buf = io.BytesIO()
    n_samples = int(duration_s * sample_rate)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_samples)
    return buf.getvalue()


def _write_scene_files(story_id: str, scene_durations):
    """Write real scene_N.png/scene_N.wav pairs into saved_stories/{id}/ and
    return the story_data scenes list pointing at them."""
    story_dir = storage_manager.get_story_path(story_id, in_saved=True)
    os.makedirs(story_dir, exist_ok=True)
    scenes = []
    for idx, duration in enumerate(scene_durations):
        png_path = os.path.join(story_dir, f"scene_{idx}.png")
        wav_path = os.path.join(story_dir, f"scene_{idx}.wav")
        with open(png_path, "wb") as f:
            f.write(_PNG_1X1)
        with open(wav_path, "wb") as f:
            f.write(_make_wav_bytes(duration))
        scenes.append({
            "text": f"This is scene number {idx + 1} of the test story, used to check caption wrapping.",
            "image_url": f"/api/saved-stories/{story_id}/scene_{idx}.png",
            "audio_url": f"/api/saved-stories/{story_id}/scene_{idx}.wav",
        })
    return scenes


def _make_story_row(db, user_id: int, scenes: list, story_id: str = None, name: str = "Video Test Story") -> str:
    """story_id is a param, not generated here, so a caller that also wrote
    scene files via _write_scene_files(story_id, ...) can point the DB row at
    the exact same folder those files were written into."""
    if story_id is None:
        story_id = f"pytest-edusmart-{uuid.uuid4().hex[:12]}"
    story_data = {"title": name, "scenes": scenes, "quiz": [], "key_points": []}
    with db(commit=True) as cur:
        cur.execute(
            "INSERT INTO user_stories (user_id, story_id, name, story_data) "
            "VALUES (%s, %s, %s, %s)",
            (user_id, story_id, name, json.dumps(story_data)),
        )
    return story_id


class TestPrecondition:
    def test_rejects_story_with_missing_audio(self, db, temp_user):
        scenes = [
            {"text": "ready scene", "image_url": "scene_0.png", "audio_url": "scene_0.wav"},
            {"text": "not ready", "image_url": "scene_1.png", "audio_url": ""},
        ]
        story_id = _make_story_row(db, temp_user, scenes)
        user = {"id": temp_user, "is_admin": False}

        with pytest.raises(HTTPException) as exc:
            asyncio.run(generate_video(story_id, user))
        assert exc.value.status_code == 409

        # Nothing should have been queued.
        assert job_manager.get_video_status(story_id) is None

    def test_rejects_story_with_no_scenes(self, db, temp_user):
        story_id = _make_story_row(db, temp_user, [])
        user = {"id": temp_user, "is_admin": False}

        with pytest.raises(HTTPException) as exc:
            asyncio.run(generate_video(story_id, user))
        assert exc.value.status_code == 400


class TestOwnership:
    def test_other_user_cannot_generate_video(self, db, temp_user):
        story_id = f"pytest-edusmart-{uuid.uuid4().hex[:12]}"
        scenes = _write_scene_files(story_id, [0.4])
        _make_story_row(db, temp_user, scenes, story_id=story_id)

        email = f"pytest-edusmart-{uuid.uuid4().hex[:12]}@example.invalid"
        username = f"pytest-edusmart-{uuid.uuid4().hex[:8]}"
        intruder = UserOperations.create(email=email, username=username, password="Throwaway!123")
        assert intruder, "could not create second temp user"
        try:
            with pytest.raises(HTTPException) as exc:
                asyncio.run(generate_video(story_id, {"id": intruder["id"], "is_admin": False}))
            assert exc.value.status_code == 404
        finally:
            with db(commit=True) as cur:
                cur.execute("DELETE FROM users WHERE id = %s", (intruder["id"],))

    def test_admin_can_render_story_owned_by_someone_else(self, db, temp_user):
        """Regression test for a real production bug (2026-08-08): an admin
        generating a video for a story they don't own got a 200 from POST
        /video (StoryOperations.get_story's admin bypass correctly allowed
        it), but the render worker then re-fetched the story with
        is_admin hardcoded to False, re-applying the owner-only filter
        against the ADMIN's id instead of the real owner's - so the story
        looked deleted and every admin-queued render failed with
        "Story no longer exists." even though the story was fine.

        Exercises the actual worker path (video_queue._run), not just the
        job-state table, since the bug was specifically in what the worker
        does with a claimed job.
        """
        story_id = f"pytest-edusmart-{uuid.uuid4().hex[:12]}"
        scenes = _write_scene_files(story_id, [0.3])
        _make_story_row(db, temp_user, scenes, story_id=story_id)

        email = f"pytest-edusmart-{uuid.uuid4().hex[:12]}@example.invalid"
        username = f"pytest-edusmart-{uuid.uuid4().hex[:8]}"
        admin = UserOperations.create(email=email, username=username, password="Throwaway!123")
        assert admin, "could not create second temp user"
        try:
            with db(commit=True) as cur:
                cur.execute("UPDATE users SET is_admin = 1 WHERE id = %s", (admin["id"],))

            # The router's own check must pass (admin bypass) even though
            # admin["id"] != the story's owner (temp_user).
            result = asyncio.run(generate_video(story_id, {"id": admin["id"], "is_admin": True}))
            assert result["status"] == "queued"

            job = job_manager.claim_next_video()
            assert job["story_id"] == story_id
            asyncio.run(video_queue._run(job))

            state = job_manager.get_video_status(story_id)
            assert state["status"] == "completed", state.get("error")
        finally:
            with db(commit=True) as cur:
                cur.execute("DELETE FROM users WHERE id = %s", (admin["id"],))


class TestJobStateTransitions:
    def test_queued_processing_completed(self, db, temp_user):
        story_id = f"pytest-edusmart-{uuid.uuid4().hex[:12]}"

        job_manager.enqueue_video(story_id, temp_user, total_scenes=2)
        state = job_manager.get_video_status(story_id)
        assert state["status"] == "queued"
        assert state["progress_scene"] == 0

        claimed = job_manager.claim_next_video()
        assert claimed["story_id"] == story_id
        state = job_manager.get_video_status(story_id)
        assert state["status"] == "processing"

        job_manager.update_video_progress(story_id, 1)
        assert job_manager.get_video_status(story_id)["progress_scene"] == 1

        job_manager.finish_video(story_id)
        state = job_manager.get_video_status(story_id)
        assert state["status"] == "completed"
        assert state["error"] is None

    def test_duplicate_generate_does_not_reset_in_flight_job(self, db, temp_user):
        story_id = f"pytest-edusmart-{uuid.uuid4().hex[:12]}"
        scenes = _write_scene_files(story_id, [0.3])
        _make_story_row(db, temp_user, scenes, story_id=story_id)
        user = {"id": temp_user, "is_admin": False}

        job_manager.enqueue_video(story_id, temp_user, total_scenes=len(scenes))
        job_manager.claim_next_video()
        job_manager.update_video_progress(story_id, 1)

        result = asyncio.run(generate_video(story_id, user))
        assert result["status"] == "processing"

        # A second render must not have been queued on top of the running one -
        # progress recorded above would be wiped back to 0 by enqueue_video if
        # it had been called again.
        state = job_manager.get_video_status(story_id)
        assert state["status"] == "processing"
        assert state["progress_scene"] == 1


class TestRenderPipeline:
    def test_produces_playable_video_of_expected_duration(self, db, temp_user):
        durations = [0.6, 0.8, 0.5]
        story_id = f"pytest-edusmart-{uuid.uuid4().hex[:12]}"
        scenes = _write_scene_files(story_id, durations)
        story_data = {"title": "Render Test", "scenes": scenes}

        output_path = asyncio.run(render_story_video(story_id, story_data))

        assert os.path.exists(output_path)
        assert os.path.getsize(output_path) > 0

        actual_duration = asyncio.run(_ffprobe_duration(Path(output_path)))
        expected = sum(durations)
        # A little slack for container/encoder overhead per clip boundary.
        assert abs(actual_duration - expected) < 1.0

    def test_progress_callback_fires_per_scene(self, db, temp_user):
        story_id = f"pytest-edusmart-{uuid.uuid4().hex[:12]}"
        scenes = _write_scene_files(story_id, [0.3, 0.3])
        story_data = {"title": "Progress Test", "scenes": scenes}

        seen = []

        async def progress_cb(done, total):
            seen.append((done, total))

        asyncio.run(render_story_video(story_id, story_data, progress_cb=progress_cb))
        assert seen == [(1, 2), (2, 2)]


class TestRenderFailure:
    def test_broken_audio_fails_cleanly_and_records_error(self, db, temp_user):
        story_id = f"pytest-edusmart-{uuid.uuid4().hex[:12]}"
        story_dir = storage_manager.get_story_path(story_id, in_saved=True)
        os.makedirs(story_dir, exist_ok=True)
        with open(os.path.join(story_dir, "scene_0.png"), "wb") as f:
            f.write(_PNG_1X1)
        # Not a real audio file - ffprobe must fail to read a duration from it.
        with open(os.path.join(story_dir, "scene_0.wav"), "wb") as f:
            f.write(b"this is not audio data")

        scenes = [{
            "text": "broken scene",
            "image_url": f"/api/saved-stories/{story_id}/scene_0.png",
            "audio_url": f"/api/saved-stories/{story_id}/scene_0.wav",
        }]
        story_data = {"title": "Broken", "scenes": scenes}

        job_manager.enqueue_video(story_id, temp_user, total_scenes=1)
        job_manager.claim_next_video()

        # Mirrors video_queue.py's _run: catch, then hand the exact message
        # to finish_video - not a hand-typed stand-in for it.
        with pytest.raises(VideoRenderError) as exc_info:
            asyncio.run(render_story_video(story_id, story_data))

        job_manager.finish_video(story_id, error=str(exc_info.value)[:500])
        state = job_manager.get_video_status(story_id)
        assert state["status"] == "failed"
        assert state["error"]
        # Never left dangling as "processing" - the router's precondition/
        # status routes both treat that as "still rendering forever" otherwise.
        assert state["status"] != "processing"
