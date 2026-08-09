"""Owner-scoped video export for a saved story.

Unlike routers/share.py's token, a video isn't a separate consent surface -
it's a derived asset of a story the owner already controls, so this reuses
the same ownership check StoryOperations.get_story already does rather than
inventing a second one. Once a video exists it's exposed automatically
through the story's existing share link too (see share.py's video_url
addition to _public_story_payload) - no second link to manage.
"""
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from database_models import StoryOperations, User
from job_state import job_manager
from routers.auth import get_current_user
from services import story_media
from services.video_queue import video_queue
from story_storage import storage_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Video export"])


def _own_story_or_404(story_id: str, user: User) -> dict:
    story = StoryOperations.get_story(story_id, user)
    if not story:
        raise HTTPException(status_code=404, detail="Story not found or not yours.")
    return story


@router.post("/api/story/{story_id}/video")
async def generate_video(story_id: str, user: User = Depends(get_current_user)):
    """Idempotent while a render is in flight: pressing the button twice
    returns the current progress instead of starting a second render."""
    story = _own_story_or_404(story_id, user)
    scenes = (story.get("story_data") or {}).get("scenes") or []
    if not scenes:
        raise HTTPException(status_code=400, detail="This story has no scenes yet.")
    for idx, scene in enumerate(scenes):
        if not scene.get("image_url") or not scene.get("audio_url"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Scene {idx + 1} isn't fully generated yet - image and "
                    "narration are both required before creating a video."
                ),
            )

    existing = job_manager.get_video_status(story_id)
    if existing and existing["status"] in ("queued", "processing"):
        return {
            "status": existing["status"],
            "progress_scene": existing["progress_scene"],
            "total_scenes": existing["total_scenes"],
        }

    job_manager.enqueue_video(story_id, user.get("id"), total_scenes=len(scenes))
    video_queue.wake()
    logger.info(f"Video render queued for story {story_id} by user {user.get('id')}")
    return {"status": "queued", "progress_scene": 0, "total_scenes": len(scenes)}


@router.get("/api/story/{story_id}/video/status")
async def get_video_status(story_id: str, user: User = Depends(get_current_user)):
    _own_story_or_404(story_id, user)
    state = job_manager.get_video_status(story_id)
    if not state:
        return {"status": "none"}
    return {
        "status": state["status"],
        "progress_scene": state["progress_scene"],
        "total_scenes": state["total_scenes"],
        "error": state["error"],
    }


@router.get("/api/story/{story_id}/video")
async def download_video(story_id: str, user: User = Depends(get_current_user)):
    story = _own_story_or_404(story_id, user)
    state = job_manager.get_video_status(story_id)
    if not state or state["status"] != "completed":
        raise HTTPException(status_code=404, detail="Video not ready yet.")

    story_dir = Path(storage_manager.get_story_path(story_id, in_saved=True))
    resolved = story_media.resolve_scene_file(story_dir, "video.mp4")
    if resolved is None:
        raise HTTPException(status_code=404, detail="Video file is missing.")

    download_name = f"{(story.get('name') or story_id).strip() or story_id}.mp4"
    return story_media.media_response(resolved, download_name=download_name)
