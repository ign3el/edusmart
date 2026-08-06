"""Live-editable feature flags backed by the app_config MySQL table.

Same "DB not code" pattern as subscription_plans (database.py) - see
DEFAULT_APP_CONFIG there for the seeded starting flags. Short-TTL cache in
front of the SELECT so a hot request path checking a flag doesn't add a DB
round trip per request, matching the "don't tax the hot path" concern the
concurrency governors already reflect elsewhere in this codebase.
"""
import logging
import time

from database import get_db_cursor

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30
_cache: dict = {}
_cache_loaded_at: float = 0.0


def _refresh_cache() -> None:
    global _cache, _cache_loaded_at
    try:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT config_key, config_value FROM app_config")
            _cache = {row["config_key"]: row["config_value"] for row in cursor.fetchall()}
        _cache_loaded_at = time.time()
    except Exception as e:
        # A flag read must never crash the request path it's guarding - keep
        # whatever was last cached (or the caller's default) and log loudly.
        logger.error(f"⚠️ app_config cache refresh failed, keeping stale values: {e}")


def _ensure_fresh() -> None:
    if time.time() - _cache_loaded_at > _CACHE_TTL_SECONDS:
        _refresh_cache()


def get_flag(key: str, default: bool = True) -> bool:
    """Boolean flag read - the only shape today's flags need."""
    _ensure_fresh()
    raw = _cache.get(key)
    if raw is None:
        return default
    return raw.strip().lower() == "true"


def get_all() -> list:
    """Admin panel listing - always reads fresh, bypasses the cache."""
    with get_db_cursor() as cursor:
        cursor.execute(
            "SELECT config_key, config_value, description, updated_by, updated_at "
            "FROM app_config ORDER BY config_key"
        )
        return cursor.fetchall()


def set_flag(key: str, value: str, updated_by: int) -> None:
    """Raises KeyError if the key doesn't exist - flags are seeded, not
    created ad hoc from the admin panel, so an unknown key is a caller bug."""
    with get_db_cursor(commit=True) as cursor:
        # SELECT-then-UPDATE, not rowcount==0 as an existence check: MySQL
        # reports rowcount as ROWS CHANGED, not rows matched, so setting a
        # flag to the value it already has would look identical to "no such
        # key" if we relied on rowcount here.
        cursor.execute("SELECT 1 FROM app_config WHERE config_key = %s", (key,))
        if cursor.fetchone() is None:
            raise KeyError(key)
        cursor.execute(
            "UPDATE app_config SET config_value = %s, updated_by = %s WHERE config_key = %s",
            (value, updated_by, key),
        )
    global _cache_loaded_at
    _cache_loaded_at = 0.0  # next get_flag() sees the new value immediately
