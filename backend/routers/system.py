"""Read-only admin observability routes: config, deploy/backup status, health +
cost, rate-limit snapshot, content review, audit log.

Deliberately no route here can trigger a deploy, rollback, or blue/green
switchover - see PROJECT.md and the admin-observability plan for why. This
router only reads state that other processes (deploy.sh, backup.sh, the
generation pipeline) already produce.
"""
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from config import Config
from routers.admin import get_admin_user, get_db_connection
from routers.auth import _rate_limiter
from services import vision_budget, runpod_usage, failure_reasons
from services.concurrency import governor_snapshot
from services.job_queue import generation_queue
from database import get_db_cursor

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/system",
    tags=["Admin - System"],
    dependencies=[Depends(get_admin_user)],
)

_STATUS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "status")


def _read_status_file(filename: str) -> Optional[dict]:
    path = os.path.join(_STATUS_DIR, filename)
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.error(f"⚠️ failed to read {path}: {e}")
        return None


@router.get("/config")
async def get_config():
    """Active provider/model config per subsystem - already redacted, no
    secrets: Config.get_info() reports backend names and model strings only."""
    return Config.get_info()


@router.get("/deploy-status")
async def get_deploy_status():
    """Which color is live per service, written by deploy.sh after each
    deploy (status/deploy_status.json, read-only bind mount - this route
    cannot trigger a deploy, only report the last one)."""
    data = _read_status_file("deploy_status.json")
    if data is None:
        return {"backend": None, "frontend": None, "note": "no deploy recorded since this feature was added"}
    return data


@router.get("/backup-status")
async def get_backup_status():
    """Last offsite backup outcome, written by scripts/backup.sh."""
    data = _read_status_file("backup_status.json")
    if data is None:
        return {"note": "no backup recorded since this feature was added"}
    return data


@router.get("/health")
async def get_system_health():
    """Concurrency + queue + vision budget (same internals as the public
    /api/health) plus RunPod AED spend, so cost sits next to utilisation in
    one call for the admin dashboard."""
    return {
        "concurrency": governor_snapshot(),
        "vision_budget": vision_budget.snapshot(),
        "queue": generation_queue.stats(),
        "runpod_usage": runpod_usage.snapshot(),
    }


@router.get("/rate-limits")
async def get_rate_limits():
    """Current in-process rate-limit buckets. Per-container: only whichever
    color is currently active has live data here (see routers/auth.py -
    RateLimiter is in-process by design, revisit only if Redis is running)."""
    return {"buckets": _rate_limiter.snapshot(), "note": "per-container - only the active color's counters are shown"}


@router.get("/content-review")
async def get_content_review(limit: int = Query(50, ge=1, le=200)):
    """Failed generations with a human-readable reason, for spotting patterns
    without digging through logs. Reuses the same job_state SQLite access as
    GET /api/admin/db/job_state/table/{table_name}."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT story_id, title, user_id, username, status, error, created_at "
            "FROM stories WHERE error IS NOT NULL AND error != '' "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to query content review: {e}")
    finally:
        conn.close()

    for row in rows:
        failure = failure_reasons.classify(row.get("error"))
        row["failure_code"] = failure.code
        row["failure_message"] = failure.message
        row["can_retry"] = failure.can_retry
    return {"items": rows}


@router.get("/audit-log")
async def get_audit_log(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor_user_id: Optional[int] = None,
    action: Optional[str] = None,
):
    conditions = []
    params: list = []
    if actor_user_id is not None:
        conditions.append("actor_user_id = %s")
        params.append(actor_user_id)
    if action is not None:
        conditions.append("action = %s")
        params.append(action)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with get_db_cursor() as cursor:
        cursor.execute(
            f"""SELECT a.id, a.actor_user_id, u.username AS actor_username, a.action,
                       a.target_type, a.target_id, a.details, a.created_at
                FROM admin_audit_log a
                LEFT JOIN users u ON u.id = a.actor_user_id
                {where}
                ORDER BY a.created_at DESC
                LIMIT %s OFFSET %s""",
            (*params, limit, offset),
        )
        rows = cursor.fetchall()
        cursor.execute(f"SELECT COUNT(*) AS total FROM admin_audit_log a {where}", params)
        total = cursor.fetchone()["total"]
    return {"items": rows, "total": total, "limit": limit, "offset": offset}
