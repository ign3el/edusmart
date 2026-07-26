"""Durable generation queue with a fixed worker pool and admission control.

Story generation used to run through FastAPI's `BackgroundTasks`, which is
fire-and-forget and completely unbounded: every accepted upload started its own
workflow immediately, and each workflow fanned out to RunPod and Kokoro on its
own. Fifty simultaneous uploads meant fifty concurrent workflows, several
hundred in-flight external calls, and a thundering herd against services sized
for a handful. Nothing was written down, so a restart - or a crash, or a deploy -
silently dropped every job that was in flight, leaving the story stuck at
"initializing" forever and the user's credit spent.

This module replaces that with three things:

1. **A durable queue.** Jobs are rows in the existing job-state SQLite database
   (see `job_state.JobStateManager`, `generation_queue` table), so a job that has
   been accepted but not started survives a restart and is picked up again.
2. **A fixed worker pool.** `GENERATION_WORKERS` asyncio tasks drain the queue.
   That number - not the number of users who happen to press upload at the same
   moment - is what decides how many stories generate concurrently.
3. **Admission control.** A per-user cap and a global queue-depth cap, both
   returning 429 with a `Retry-After`, so overload is an explicit, honest answer
   instead of silent degradation for everybody.

Everything is env-driven. On bigger hardware raise `GENERATION_WORKERS` (and the
governors in `services/concurrency.py`) and restart; nothing here needs editing.
No Redis, no extra container - the SQLite file is already a mounted volume and
already runs in WAL mode, which is what makes the atomic claim below safe even
across several uvicorn worker processes.
"""
import asyncio
import logging
import os
import time
from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException

from job_state import job_manager

logger = logging.getLogger(__name__)


def _limit(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning(f"{name}={raw!r} is not an integer; using default {default}")
        return default


# How many stories generate at once, process-wide. This is the headline dial:
# it decides throughput, and everything downstream (RunPod, Kokoro, the thread
# pool) is sized to absorb this many concurrent workflows.
GENERATION_WORKERS = _limit("GENERATION_WORKERS", 4)

# How many jobs may be waiting before new uploads are refused. Accepting an
# unbounded backlog is not generosity - it is promising a user a story they will
# wait an hour for. Refusing with 429 lets the frontend say "try again shortly".
MAX_QUEUE_DEPTH = _limit("MAX_QUEUE_DEPTH", 200)

# How many generations one account may have queued or running at once. Without
# this a single user (or a stuck retry loop in their browser) can fill the whole
# queue and lock everyone else out.
MAX_JOBS_PER_USER = _limit("MAX_JOBS_PER_USER", 3)

# Idle workers re-check the table on this interval. The in-process wake-up Event
# makes this irrelevant for jobs submitted by this process; the poll is what lets
# a second uvicorn worker process pick up work submitted by the first.
IDLE_POLL_SECONDS = float(os.getenv("QUEUE_POLL_SECONDS", "2.0"))

# Finished rows are kept this long so the admin job viewer can show recent
# history, then pruned.
QUEUE_RETENTION_HOURS = _limit("QUEUE_RETENTION_HOURS", 48)

# Hard ceiling on one story's total generation time. A fixed worker pool turns
# any hang into a permanent loss of capacity: if the Kokoro container stops
# responding mid-story the workflow's own retries eventually give up, but a
# genuinely wedged socket never returns at all, and that worker is gone until
# the next deploy. Four such stories and the app quietly stops generating
# anything while still happily accepting uploads. The timeout is deliberately
# generous - a 10-scene story on CPU TTS legitimately takes several minutes -
# because its job is to catch hangs, not slow runs.
JOB_TIMEOUT_SECONDS = float(os.getenv("GENERATION_TIMEOUT_SECONDS", "1800"))

_PRUNE_INTERVAL_S = 3600


class GenerationQueue:
    """Fixed pool of workers draining the durable job queue."""

    def __init__(self) -> None:
        self._handler: Optional[Callable[..., Any]] = None
        self._workers: list = []
        self._wake = asyncio.Event()
        self._stopping = False
        self._last_prune = 0.0

    def set_handler(self, handler: Callable[..., Any]) -> None:
        """Register the coroutine that actually generates a story.

        Injected rather than imported because the workflow lives in `main.py`,
        which imports this module - importing it back would be circular.
        """
        self._handler = handler

    async def start(self) -> Dict[str, Any]:
        """Recover the queue, then bring the worker pool up.

        Returns the recovery summary, including `abandoned_jobs` - jobs that
        were mid-generation when the previous process died. The caller owns the
        refunds for those, and must issue them before the separate
        orphaned-story reconciler runs, or the same credit is refunded twice.
        """
        if self._workers:
            return {"requeued": 0, "abandoned": 0, "abandoned_jobs": [], "pruned": 0}
        if self._handler is None:
            raise RuntimeError("GenerationQueue.start() called before set_handler()")

        recovered = job_manager.recover_queue(retention_hours=QUEUE_RETENTION_HOURS)
        if recovered["requeued"] or recovered["abandoned"]:
            logger.info(
                f"Queue recovery: {recovered['requeued']} job(s) still queued from "
                f"before this process, {recovered['abandoned']} abandoned mid-run"
            )

        self._stopping = False
        for i in range(GENERATION_WORKERS):
            self._workers.append(asyncio.create_task(self._worker(i)))
        logger.info(
            f"✓ Generation queue started: {GENERATION_WORKERS} worker(s), "
            f"max depth {MAX_QUEUE_DEPTH}, {MAX_JOBS_PER_USER} concurrent job(s) per user"
        )
        # Anything left queued by a previous process should start immediately.
        self._wake.set()
        return recovered

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        for task in self._workers:
            task.cancel()
        self._workers = []

    def submit(self, story_id: str, user_id: Optional[int], payload: Dict[str, Any]) -> int:
        """Record a job durably and nudge the workers. Returns its queue position.

        Position 1 means it is next in line, which - with a free worker - means
        it starts within milliseconds. This returns *after* the row is committed,
        so the HTTP response can only promise a story that will actually survive
        a restart.
        """
        position = job_manager.enqueue_job(story_id, user_id, payload)
        self._wake.set()
        return position

    def cancel(self, story_id: str) -> None:
        """Drop a job that has not started yet (story deleted, duplicate chosen)."""
        job_manager.remove_queued_job(story_id)

    async def _worker(self, index: int) -> None:
        while not self._stopping:
            try:
                # Clear *before* claiming: a submit landing between the claim and
                # the wait would otherwise set the Event and then have it cleared
                # by this worker, costing one poll interval of latency.
                self._wake.clear()
                job = await asyncio.to_thread(job_manager.claim_next_job)

                if job is None:
                    await self._idle()
                    continue

                await self._run(job, index)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A worker that dies takes a permanent slice of throughput with
                # it, so nothing short of cancellation is allowed to end the loop.
                logger.exception(f"Generation worker {index} loop error; continuing")
                await asyncio.sleep(1.0)

    async def _idle(self) -> None:
        now = time.monotonic()
        if now - self._last_prune > _PRUNE_INTERVAL_S:
            self._last_prune = now
            try:
                await asyncio.to_thread(job_manager.prune_queue, QUEUE_RETENTION_HOURS)
            except Exception as exc:
                logger.warning(f"Queue prune failed: {exc}")
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=IDLE_POLL_SECONDS)
        except asyncio.TimeoutError:
            pass

    async def _run(self, job: Dict[str, Any], index: int) -> None:
        story_id = job["story_id"]
        started = time.monotonic()
        logger.info(f"▶ Worker {index} picked up story {story_id[:8]} (attempt {job['attempts']})")
        try:
            await asyncio.wait_for(
                self._handler(**job["payload"]), timeout=JOB_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            # wait_for cancels the workflow, which raises CancelledError inside
            # it - and CancelledError is a BaseException, so the workflow's own
            # `except Exception` handler does NOT run. Its failure bookkeeping
            # (mark failed, refund) therefore has to happen here instead.
            logger.error(
                f"✗ Story {story_id} timed out after {JOB_TIMEOUT_SECONDS:.0f}s; "
                f"worker {index} reclaimed"
            )
            job_manager.mark_story_failed(story_id, "generation timed out")
            job_manager.finish_job(story_id, error=f"timed out after {JOB_TIMEOUT_SECONDS:.0f}s")
            user_id = job.get("user_id")
            if user_id:
                try:
                    # Imported lazily: routers.billing pulls in the FastAPI router
                    # tree, and importing it at module scope would make this
                    # module's own import order matter.
                    from routers.billing import refund_credit
                    refund_credit(user_id, story_id)
                except Exception as refund_err:
                    logger.warning(f"Could not refund timed-out story {story_id}: {refund_err}")
        except asyncio.CancelledError:
            # Shutdown mid-generation. Leave the row as an explicit failure
            # rather than 'running', which would otherwise look like a live job
            # to the next process and never be cleaned up.
            job_manager.finish_job(story_id, error="cancelled during shutdown")
            raise
        except Exception as exc:
            # The workflow already marks the story failed and refunds the credit;
            # this only records the queue-side outcome.
            logger.exception(f"✗ Story {story_id} failed in worker {index}")
            job_manager.finish_job(story_id, error=f"{type(exc).__name__}: {exc}"[:500])
        else:
            job_manager.finish_job(story_id)
            logger.info(
                f"✓ Worker {index} finished story {story_id[:8]} in "
                f"{time.monotonic() - started:.1f}s"
            )

    def stats(self) -> Dict[str, Any]:
        counts = job_manager.queue_stats()
        return {
            "workers": GENERATION_WORKERS,
            "queued": counts.get("queued", 0),
            "running": counts.get("running", 0),
            "max_depth": MAX_QUEUE_DEPTH,
        }


generation_queue = GenerationQueue()


def admit_generation(user_id: int) -> None:
    """Reject a new generation request that the system cannot honour.

    Called before the credit is reserved and before any folder or job-state row
    exists, so a refused request leaves nothing behind to clean up.

    429 rather than 503: the request is well-formed and the service is healthy,
    the client simply needs to come back later. `Retry-After` gives the frontend
    something concrete to show instead of a spinner that never resolves.
    """
    counts = job_manager.queue_stats()
    depth = counts.get("queued", 0)
    if depth >= MAX_QUEUE_DEPTH:
        logger.warning(f"Queue full ({depth}/{MAX_QUEUE_DEPTH}); rejecting new generation")
        raise HTTPException(
            status_code=429,
            detail={
                "error": "queue_full",
                "message": "The story queue is full right now. Please try again in a few minutes.",
                "queue_depth": depth,
            },
            headers={"Retry-After": "120"},
        )

    active = job_manager.active_jobs_for_user(user_id)
    if active >= MAX_JOBS_PER_USER:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "user_limit",
                "message": (
                    f"You already have {active} story/stories generating. "
                    "Wait for one to finish before starting another."
                ),
                "active_jobs": active,
                "limit": MAX_JOBS_PER_USER,
            },
            headers={"Retry-After": "60"},
        )
