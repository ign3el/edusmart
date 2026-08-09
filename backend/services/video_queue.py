"""Single dedicated worker draining `story_videos`, the way GenerationQueue
(job_queue.py) drains `generation_queue` - but deliberately its own smaller
queue rather than a `job_type` column on that one.

Video rendering is CPU-bound ffmpeg work sharing a container that has no
CPU/memory limits set anywhere in docker-compose.yml. Running it through the
existing GENERATION_WORKERS pool would let ffmpeg encodes compete with (and
starve) live story-generation and API traffic. A single hardcoded worker
keeps rendering serial and bounded without touching that pool's sizing.
"""
import asyncio
import logging

from database_models import StoryOperations
from job_state import job_manager
from services.video_service import VideoRenderError, render_story_video

logger = logging.getLogger(__name__)

IDLE_POLL_SECONDS = 2.0


class VideoQueue:
    """One asyncio task, polling story_videos for 'queued' rows."""

    def __init__(self) -> None:
        self._task = None
        self._wake = asyncio.Event()
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None:
            return
        recovered = job_manager.recover_stuck_videos()
        if recovered:
            logger.info(f"Video queue recovery: {recovered} job(s) abandoned mid-render")
        self._stopping = False
        self._task = asyncio.create_task(self._worker())
        self._wake.set()
        logger.info("✓ Video render queue started (1 worker)")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def wake(self) -> None:
        """Nudge the worker after enqueuing a job, same as GenerationQueue.submit."""
        self._wake.set()

    async def _worker(self) -> None:
        while not self._stopping:
            try:
                # Clear before claiming, same reasoning as job_queue.py's
                # _worker: a submit landing between claim and wait must not
                # be swallowed by this iteration clearing the event after it.
                self._wake.clear()
                job = await asyncio.to_thread(job_manager.claim_next_video)
                if job is None:
                    await self._idle()
                    continue
                await self._run(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Video worker loop error; continuing")
                await asyncio.sleep(1.0)

    async def _idle(self) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=IDLE_POLL_SECONDS)
        except asyncio.TimeoutError:
            pass

    async def _run(self, job: dict) -> None:
        story_id = job["story_id"]
        user_id = job.get("user_id")
        logger.info(f"▶ Rendering video for story {story_id[:8]}")
        try:
            # Ownership was already verified once, at enqueue time (see
            # routers/video.py) - including the admin bypass, which lets an
            # admin queue a render for a story they don't own. Re-applying
            # the owner-only filter here with is_admin=False would recheck
            # against the REQUESTER's id, not the story's actual owner, and
            # break admin-queued jobs (get_story returns None -> "Story no
            # longer exists" even though the story is fine). is_admin=True
            # makes this an unconditional fetch by story_id, which is what a
            # trusted internal worker - not user input - should do.
            story = await asyncio.to_thread(
                StoryOperations.get_story, story_id, {"id": user_id, "is_admin": True}
            )
            if not story:
                raise VideoRenderError("Story no longer exists.")
            story_data = story.get("story_data") or {}

            async def progress_cb(done: int, total: int) -> None:
                await asyncio.to_thread(job_manager.update_video_progress, story_id, done)

            await render_story_video(story_id, story_data, progress_cb=progress_cb)
            job_manager.finish_video(story_id)
            logger.info(f"✓ Video ready for story {story_id[:8]}")
        except VideoRenderError as exc:
            logger.warning(f"✗ Video render failed for {story_id[:8]}: {exc}")
            job_manager.finish_video(story_id, error=str(exc)[:500])
        except Exception as exc:
            logger.exception(f"✗ Video render crashed for {story_id[:8]}")
            job_manager.finish_video(story_id, error=f"{type(exc).__name__}: {exc}"[:500])


video_queue = VideoQueue()
