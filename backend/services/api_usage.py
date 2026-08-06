"""Counters for outbound provider calls: per story, and per day per key.

Two questions this answers that nothing else could:

  1. "How many API calls did generating THIS story cost?"  Per-story totals,
     broken down by provider and model.
  2. "How much of today's quota has each key burned?"  Gemini's free tier meters
     RPD per model *per Google Cloud project*, which is why this app runs a
     second key from a separate project (see GEMINI_API_KEY_FALLBACK in
     story_service). Rotation is sticky and one-way, so without a per-key
     counter there is no way to see how close the active key is to flipping -
     the first visible symptom is generation failing.

Deliberately SQLite in the existing job_state database rather than in-process
counters: the app runs multiple workers and gets redeployed constantly, and a
number that resets on every deploy cannot answer "requests per DAY".

Never raises. This is observability - a broken counter must not be able to fail
a story generation. Every public function swallows its own errors and degrades
to "no data" rather than propagating.
"""
import contextvars
import logging
import os
import sqlite3
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB = os.path.join(_APP_ROOT, "db_data", "job_state.db")

# The story a call belongs to. A ContextVar rather than a parameter threaded
# through twenty call sites: the providers are reached from deep inside
# StoryService, from asyncio tasks, and from asyncio.to_thread workers, and a
# ContextVar is copied into all three automatically. Unset (None) for calls that
# genuinely have no story - the admin retry endpoint, a health probe - and those
# are counted in the daily totals but attributed to no story.
current_story: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_story", default=None
)

_lock = threading.Lock()
_init_done = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema() -> None:
    global _init_done
    if _init_done:
        return
    with _lock:
        if _init_done:
            return
        with _connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_usage_daily (
                    day TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    key_label TEXT NOT NULL,
                    calls INTEGER NOT NULL DEFAULT 0,
                    errors INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (day, provider, model, key_label)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_usage_story (
                    story_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    calls INTEGER NOT NULL DEFAULT 0,
                    errors INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (story_id, provider, model)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_story ON api_usage_story(story_id)"
            )
        _init_done = True


def _today() -> str:
    # UTC, matching Google's quota reset. Local time would roll the counter over
    # at the wrong moment and make a key look fresh while it is still exhausted.
    return time.strftime("%Y-%m-%d", time.gmtime())


def record(provider: str, model: str, key_label: str = "-", ok: bool = True,
           story_id: Optional[str] = None) -> None:
    """Count one outbound provider call. Best effort; never raises."""
    try:
        _ensure_schema()
        sid = story_id if story_id is not None else current_story.get()
        err = 0 if ok else 1
        with _lock, _connect() as conn:
            conn.execute("""
                INSERT INTO api_usage_daily (day, provider, model, key_label, calls, errors)
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(day, provider, model, key_label)
                DO UPDATE SET calls = calls + 1, errors = errors + ?
            """, (_today(), provider, model or "-", key_label, err, err))
            if sid:
                conn.execute("""
                    INSERT INTO api_usage_story (story_id, provider, model, calls, errors)
                    VALUES (?, ?, ?, 1, ?)
                    ON CONFLICT(story_id, provider, model)
                    DO UPDATE SET calls = calls + 1, errors = errors + ?
                """, (sid, provider, model or "-", err, err))
    except Exception as e:  # pragma: no cover - observability must not break generation
        logger.debug(f"api_usage.record failed ({provider}/{model}): {e}")


def story_totals(story_ids: List[str]) -> Dict[str, Dict]:
    """{story_id: {total, errors, breakdown:[{provider, model, calls, errors}]}}.

    One query for the whole page rather than N+1 - the admin story list renders
    up to a few hundred rows.
    """
    if not story_ids:
        return {}
    try:
        _ensure_schema()
        marks = ",".join("?" * len(story_ids))
        with _connect() as conn:
            rows = conn.execute(
                f"""SELECT story_id, provider, model, calls, errors
                    FROM api_usage_story WHERE story_id IN ({marks})""",
                story_ids,
            ).fetchall()
        out: Dict[str, Dict] = {}
        for r in rows:
            e = out.setdefault(r["story_id"], {"total": 0, "errors": 0, "breakdown": []})
            e["total"] += r["calls"]
            e["errors"] += r["errors"]
            e["breakdown"].append({
                "provider": r["provider"], "model": r["model"],
                "calls": r["calls"], "errors": r["errors"],
            })
        for e in out.values():
            e["breakdown"].sort(key=lambda b: -b["calls"])
        return out
    except Exception as e:
        logger.debug(f"api_usage.story_totals failed: {e}")
        return {}


def rpd_snapshot(days: int = 1) -> Dict:
    """Today's per-model, per-key request counts plus the configured ceiling.

    The cap is per (model, key) for Gemini because the quota is scoped to the
    Cloud project behind the key, not to the app. GEMINI_RPD_CAP is advisory -
    it is what the free tier grants, not something this app enforces - so it is
    reported alongside the count rather than used to block anything.
    """
    try:
        _ensure_schema()
        gemini_cap = int(os.getenv("GEMINI_RPD_CAP", "500"))
        with _connect() as conn:
            rows = conn.execute("""
                SELECT day, provider, model, key_label, calls, errors
                FROM api_usage_daily
                WHERE day >= date('now', ?)
                ORDER BY day DESC, calls DESC
            """, (f"-{max(0, days - 1)} day",)).fetchall()

        items = []
        for r in rows:
            cap = gemini_cap if r["provider"] == "gemini" else None
            items.append({
                "day": r["day"], "provider": r["provider"], "model": r["model"],
                "key_label": r["key_label"], "calls": r["calls"], "errors": r["errors"],
                "cap": cap,
                "pct": round(r["calls"] / cap * 100, 1) if cap else None,
            })
        today = _today()
        return {
            "day": today,
            "total_today": sum(i["calls"] for i in items if i["day"] == today),
            "gemini_cap": gemini_cap,
            "items": items,
        }
    except Exception as e:
        logger.debug(f"api_usage.rpd_snapshot failed: {e}")
        return {"day": _today(), "total_today": 0, "gemini_cap": 0, "items": []}
