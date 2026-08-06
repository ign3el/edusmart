"""Admin CRUD for the app_config feature-flag table. See services/app_config.py
for the reader used by real request paths."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from routers.admin import get_admin_user
from services import app_config, audit_log

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/config-flags",
    tags=["Admin - Feature Flags"],
    dependencies=[Depends(get_admin_user)],
)


class ConfigFlagUpdateRequest(BaseModel):
    config_value: str


@router.get("")
async def list_config_flags():
    return {"items": app_config.get_all()}


@router.put("/{key}")
async def update_config_flag(key: str, update: ConfigFlagUpdateRequest, current_user: dict = Depends(get_admin_user)):
    try:
        app_config.set_flag(key, update.config_value, updated_by=current_user["id"])
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No such config key: {key}")

    audit_log.record(
        actor_user_id=current_user["id"],
        action="update_config_flag",
        target_type="app_config",
        target_id=key,
        details={"config_value": update.config_value},
    )
    return {"config_key": key, "config_value": update.config_value}
