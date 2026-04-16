"""Centralised schedule config — reads from DB with short TTL cache."""

import time
import logging

from backend.db import get_db

log = logging.getLogger(__name__)

_cache: dict = {"data": None, "ts": 0.0}
_TTL = 60  # seconds


def get_settings() -> dict:
    now = time.time()
    if _cache["data"] and (now - _cache["ts"]) < _TTL:
        return _cache["data"]

    try:
        row = (
            get_db()
            .table("app_settings")
            .select("*")
            .eq("id", 1)
            .single()
            .execute()
            .data
        )
    except Exception:
        log.warning("Failed to read app_settings — using defaults")
        row = None

    if not row:
        row = {
            "funnel_scan_interval_minutes": 90,
            "funnel_scan_enabled": True,
            "ad_scrape_enabled": False,
            "ad_scrape_hour_utc": 6,
            "ad_scrape_days_of_week": [0, 3],
            "domain_intel_enabled": True,
            "domain_intel_day_of_week": 1,
            "domain_intel_hour_utc": 7,
        }

    _cache["data"] = row
    _cache["ts"] = now
    return row


def invalidate_cache():
    _cache["data"] = None
    _cache["ts"] = 0.0
