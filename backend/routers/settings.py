import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from backend.db import get_db
from backend.models import AppSettings, AppSettingsUpdate
from backend.settings import get_settings, invalidate_cache

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=AppSettings)
def get_settings_endpoint():
    return get_settings()


@router.patch("", response_model=AppSettings)
def update_settings(body: AppSettingsUpdate):
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(400, "No fields to update")
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = get_db().table("app_settings").update(data).eq("id", 1).execute()
    invalidate_cache()
    log.info("Settings updated: %s", list(data.keys()))
    if not res.data:
        raise HTTPException(500, "Failed to update settings")
    return res.data[0]
