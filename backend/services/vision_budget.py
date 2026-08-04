"""Daily spend ceiling for vision (page-reading) API calls.

Why this exists
---------------
RunPod image generation already has a monthly AED cap with an atomic
reserve-before-spend check (services/story_service.py). The vision model had
no ceiling of any kind. That mattered because its quota is SHARED and daily:
the Gemini free tier allows a few hundred requests per day across the whole
application, so one user uploading one large document could consume the entire
day's allowance and leave every other user unable to generate anything.

The page cap in services/concurrency.py (VISION_MAX_PAGES) bounds a SINGLE
document. This bounds the day, per user and globally - a user can still upload
twenty 30-page documents without it.

Design notes
------------
- threading.Lock, not asyncio.Lock. Vision extraction runs inside a worker
  thread that spins up its own event loop (see _vision_read_images_blocking);
  an asyncio.Lock created on one loop and awaited on another is a bug waiting
  to happen. The critical section is a file read/write measured in
  microseconds, so a plain thread lock is both correct and cheap here.
- Reserve BEFORE the call, never after it succeeds. Reserving afterwards lets
  concurrent callers all read the same stale count, all pass the check, and all
  spend - which is exactly the race the RunPod cap was fixed for.
- Counter lives in db_data/ (the Docker named volume). The RunPod counter
  originally sat in services/, which the container cannot write to, so every
  save failed silently and the cap enforced nothing for five days. Do not
  repeat that: this path must be writable and persistent.
"""
import contextlib
import fcntl
import json
import logging
import os
import threading
import time
from typing import Tuple

logger = logging.getLogger(__name__)

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_USAGE_FILE = os.path.join(_APP_ROOT, "db_data", "vision_usage.json")
_LOCK_FILE = _USAGE_FILE + ".lock"

_lock = threading.Lock()


@contextlib.contextmanager
def _cross_process_lock():
    """Exclusive lock over db_data/vision_usage.json.lock, held via flock.

    threading.Lock only serializes callers inside ONE Python process. Blue/green
    deploys briefly run two backend containers that both mount the same
    db_data named volume, so two separate processes can race the same
    load-modify-save cycle in reserve() below - each reading the same stale
    total, both granting, one write clobbering the other's. flock is a kernel
    lock tied to the underlying inode, so it serializes correctly across
    containers sharing that volume (plain local Docker volume, not NFS).
    A dedicated lock file, not the data file itself, so replacing the data
    file via os.replace() never invalidates a lock someone else is holding.
    """
    os.makedirs(os.path.dirname(_LOCK_FILE), exist_ok=True)
    with open(_LOCK_FILE, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _limit(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        # Tolerate an inline comment, matching config._env_int - a .env written
        # as `VISION_DAILY_CAP=400  # per day` must not turn a cap into a crash.
        return max(0, int(str(raw).split("#", 1)[0].strip()))
    except ValueError:
        logger.warning(f"{name}={raw!r} is not an integer; using default {default}")
        return default


# Global daily ceiling across all users. Default 400 sits under the ~500/day
# free tier with headroom for retries and any manual testing.
VISION_DAILY_CAP = _limit("VISION_DAILY_CAP", 400)

# Per-user daily ceiling. At VISION_MAX_PAGES=30 this is ~4 full-size documents
# per user per day - generous for real classroom use, and low enough that no
# single account can drain the global pool.
VISION_DAILY_CAP_PER_USER = _limit("VISION_DAILY_CAP_PER_USER", 120)


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _load() -> dict:
    try:
        with open(_USAGE_FILE, "r") as f:
            data = json.load(f)
        if data.get("day") == _today():
            return data
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"vision usage file unreadable, starting a fresh day: {e}")
    return {"day": _today(), "total": 0, "per_user": {}}


def _save(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_USAGE_FILE), exist_ok=True)
        tmp = _USAGE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, _USAGE_FILE)  # atomic; never leaves a half-written file
    except Exception as e:
        # A cap that cannot persist is a cap that does not exist.
        logger.error(f"⚠️ Vision budget NOT persisted ({_USAGE_FILE}): {e}")


def reserve(n: int, user_id=None) -> Tuple[int, str]:
    """Reserve up to `n` vision calls. Returns (granted, reason).

    Partial grants are intentional: if 8 calls remain and a 30-page document
    arrives, reading 8 pages and telling the model the document was truncated
    beats refusing outright and charging the user for nothing.
    """
    if n <= 0:
        return 0, ""

    with _lock, _cross_process_lock():
        data = _load()
        uid = str(user_id) if user_id is not None else "_anonymous"

        remaining_global = max(0, VISION_DAILY_CAP - data.get("total", 0))
        used_by_user = data.get("per_user", {}).get(uid, 0)
        remaining_user = max(0, VISION_DAILY_CAP_PER_USER - used_by_user)

        granted = min(n, remaining_global, remaining_user)
        reason = ""
        if granted < n:
            if remaining_user <= remaining_global:
                reason = (
                    f"per-user daily vision limit reached "
                    f"({used_by_user}/{VISION_DAILY_CAP_PER_USER} pages today)"
                )
            else:
                reason = (
                    f"global daily vision limit reached "
                    f"({data.get('total', 0)}/{VISION_DAILY_CAP} pages today)"
                )

        if granted > 0:
            data["total"] = data.get("total", 0) + granted
            data.setdefault("per_user", {})[uid] = used_by_user + granted
            _save(data)

        return granted, reason


def snapshot() -> dict:
    """Current usage, for the admin panel and /api/health."""
    with _lock:
        data = _load()
        return {
            "day": data.get("day"),
            "used": data.get("total", 0),
            "cap": VISION_DAILY_CAP,
            "remaining": max(0, VISION_DAILY_CAP - data.get("total", 0)),
            "per_user_cap": VISION_DAILY_CAP_PER_USER,
            "top_users": sorted(
                data.get("per_user", {}).items(), key=lambda kv: kv[1], reverse=True
            )[:10],
        }
