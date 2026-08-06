"""Site-wide announcement banner: admin CRUD + one public read endpoint.

GET /api/announcements/active has NO auth - it's what the public site banner
calls on every page load, same trust level as any other public marketing
content on the site.
"""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from routers.admin import get_admin_user
from services import audit_log
from database import get_db_cursor

logger = logging.getLogger(__name__)

admin_router = APIRouter(
    prefix="/api/admin/announcements",
    tags=["Admin - Announcements"],
    dependencies=[Depends(get_admin_user)],
)

public_router = APIRouter(tags=["Announcements"])


class AnnouncementCreateRequest(BaseModel):
    message: str = Field(..., max_length=500)
    severity: str = Field("info", pattern="^(info|warning|critical)$")
    is_active: bool = False
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


class AnnouncementUpdateRequest(BaseModel):
    message: Optional[str] = Field(None, max_length=500)
    severity: Optional[str] = Field(None, pattern="^(info|warning|critical)$")
    is_active: Optional[bool] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


@admin_router.get("")
async def list_announcements():
    with get_db_cursor() as cursor:
        cursor.execute("SELECT * FROM announcements ORDER BY created_at DESC")
        return {"items": cursor.fetchall()}


@admin_router.post("")
async def create_announcement(payload: AnnouncementCreateRequest, current_user: dict = Depends(get_admin_user)):
    with get_db_cursor(commit=True) as cursor:
        cursor.execute(
            """INSERT INTO announcements (message, severity, is_active, starts_at, ends_at, created_by)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (payload.message, payload.severity, payload.is_active, payload.starts_at, payload.ends_at, current_user["id"]),
        )
        new_id = cursor.lastrowid

    audit_log.record(
        actor_user_id=current_user["id"],
        action="create_announcement",
        target_type="announcement",
        target_id=new_id,
        details={"message": payload.message, "is_active": payload.is_active},
    )
    return {"id": new_id}


@admin_router.put("/{announcement_id}")
async def update_announcement(announcement_id: int, payload: AnnouncementUpdateRequest, current_user: dict = Depends(get_admin_user)):
    fields = payload.dict(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    with get_db_cursor(commit=True) as cursor:
        cursor.execute("SELECT 1 FROM announcements WHERE id = %s", (announcement_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Announcement not found")

        set_clause = ", ".join(f"{k} = %s" for k in fields)
        cursor.execute(
            f"UPDATE announcements SET {set_clause} WHERE id = %s",
            (*fields.values(), announcement_id),
        )

    audit_log.record(
        actor_user_id=current_user["id"],
        action="update_announcement",
        target_type="announcement",
        target_id=announcement_id,
        details=fields,
    )
    return {"success": True}


@public_router.get("/api/announcements/active")
async def get_active_announcement():
    """No auth - public banner endpoint. Returns the single currently-active
    announcement within its start/end window, or null if none."""
    with get_db_cursor() as cursor:
        cursor.execute(
            """SELECT id, message, severity FROM announcements
               WHERE is_active = TRUE
               AND (starts_at IS NULL OR starts_at <= NOW())
               AND (ends_at IS NULL OR ends_at >= NOW())
               ORDER BY created_at DESC LIMIT 1"""
        )
        row = cursor.fetchone()
    return row
