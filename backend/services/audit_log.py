"""Records admin actions to admin_audit_log.

A logging failure must never fail the admin action it's describing - every
call here swallows and logs its own exception rather than raising, so a
transient DB hiccup can't turn "grant credits" into a 500 for an unrelated
reason.
"""
import json
import logging
from typing import Optional

from database import get_db_cursor

logger = logging.getLogger(__name__)


def record(
    actor_user_id: int,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                """INSERT INTO admin_audit_log
                   (actor_user_id, action, target_type, target_id, details)
                   VALUES (%s, %s, %s, %s, %s)""",
                (
                    actor_user_id,
                    action,
                    target_type,
                    str(target_id) if target_id is not None else None,
                    json.dumps(details) if details is not None else None,
                ),
            )
    except Exception as e:
        logger.error(f"⚠️ audit_log.record failed (action={action}, actor={actor_user_id}): {e}")
