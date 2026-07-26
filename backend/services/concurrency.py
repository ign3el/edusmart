"""Process-wide concurrency governors for the expensive external calls.

Every limit in this app used to be *per story*. `generate_images_parallel`
built its own `asyncio.Semaphore(4)` inside the call, so four concurrent
stories meant sixteen concurrent RunPod requests, twenty stories meant eighty,
and nothing in the process knew the total. The external services do have a real
ceiling - RunPod bills per second and serialises past its worker count, the
Kokoro container is CPU-bound and degrades sharply past a handful of parallel
requests - so the limit has to be a property of *this process*, not of whichever
story happens to be running.

Every ceiling here is an environment variable. The defaults suit the current
3-vCPU test box; on bigger hardware, or once Kokoro moves to the GPU pod, raise
the variable and restart. No code change, no rebuild.

These bound *external* calls, not CPU work, so they are sized by what the
downstream service can absorb, not by core count.
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Dict

logger = logging.getLogger(__name__)


def _limit(name: str, default: int) -> int:
    """Read a positive integer limit from the environment.

    A misconfigured 0 or negative value would mean "block forever" rather than
    "no limit", which is the worst possible failure mode for a governor, so the
    floor is 1 and a bad value falls back to the default rather than raising at
    import time and taking the whole app down.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning(f"{name}={raw!r} is not an integer; using default {default}")
        return default


# Concurrent RunPod image requests across the entire process. RunPod's serverless
# endpoint queues past its own worker count, so more than this buys latency, not
# throughput. Raise it when the endpoint's max workers goes up.
MAX_CONCURRENT_IMAGES = _limit("MAX_CONCURRENT_IMAGES", 6)

# Concurrent TTS requests across the entire process. Kokoro currently runs on
# CPU in a shared container; each request eats a core for its duration, so this
# is the single most important dial to raise after the GPU migration.
MAX_CONCURRENT_TTS = _limit("MAX_CONCURRENT_TTS", 4)

# Concurrent Groq story-generation calls. Cheap per call but rate-limited by
# tokens-per-minute upstream, and each one occupies a thread-pool slot for the
# whole round trip because the Groq SDK is synchronous.
MAX_CONCURRENT_LLM = _limit("MAX_CONCURRENT_LLM", 8)

# Per-story cap on parallel image generation, applied on top of the process-wide
# governor. The governor keeps the app as a whole from flooding RunPod; this
# keeps any single 10-scene story from taking every slot at once and starving
# the users queued behind it.
MAX_IMAGES_PER_STORY = _limit("MAX_IMAGES_PER_STORY", 4)


class Governor:
    """A named semaphore that also reports how contended it is.

    The counters exist because a limit you cannot observe is a limit you cannot
    tune. `waiting` and `peak_wait_s` are what tell you whether a ceiling is
    actually being hit in production, or whether raising it would change nothing
    because the bottleneck is somewhere else entirely.
    """

    def __init__(self, name: str, limit: int):
        self.name = name
        self.limit = limit
        self._sem = asyncio.Semaphore(limit)
        self.in_use = 0
        self.waiting = 0
        self.acquired = 0
        self.peak_wait_s = 0.0

    @asynccontextmanager
    async def slot(self):
        """Hold one slot for the duration of the block.

        The `waiting` counter is decremented in a `finally` so a cancelled
        request (client disconnect, shutdown) cannot leave it permanently
        inflated, and the slot is released in a `finally` so an exception inside
        the block cannot leak capacity - a leaked slot is unrecoverable without
        a restart.
        """
        started = time.monotonic()
        self.waiting += 1
        try:
            await self._sem.acquire()
        finally:
            self.waiting -= 1

        waited = time.monotonic() - started
        if waited > self.peak_wait_s:
            self.peak_wait_s = waited
        self.in_use += 1
        self.acquired += 1
        try:
            yield
        finally:
            self.in_use -= 1
            self._sem.release()

    def stats(self) -> Dict[str, float]:
        return {
            "limit": self.limit,
            "in_use": self.in_use,
            "waiting": self.waiting,
            "acquired_total": self.acquired,
            "peak_wait_s": round(self.peak_wait_s, 2),
        }


image_governor = Governor("images", MAX_CONCURRENT_IMAGES)
tts_governor = Governor("tts", MAX_CONCURRENT_TTS)
llm_governor = Governor("llm", MAX_CONCURRENT_LLM)


def governor_snapshot() -> Dict[str, Dict[str, float]]:
    """Current utilisation of every governor, for /api/health."""
    return {
        g.name: g.stats() for g in (image_governor, tts_governor, llm_governor)
    }


def log_limits() -> None:
    logger.info(
        f"✓ Concurrency governors: images={MAX_CONCURRENT_IMAGES} "
        f"tts={MAX_CONCURRENT_TTS} llm={MAX_CONCURRENT_LLM} "
        f"images_per_story={MAX_IMAGES_PER_STORY}"
    )
