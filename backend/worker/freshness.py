"""Per-source freshness tracking.

Every worker loop calls mark_success() or mark_failure() after processing a
competitor. The synthesis loop reads is_stale() / get_stale_sources() as its
first gate before producing a weekly ship list.

Sources are strings: 'ad_scrape', 'domain_intel', 'funnel_scan'.
Keep them in sync with SOURCE_* constants below.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from backend.config import FRESHNESS_STALE_HOURS
from backend.db import get_db

log = logging.getLogger(__name__)

SOURCE_AD_SCRAPE = "ad_scrape"
SOURCE_DOMAIN_INTEL = "domain_intel"
SOURCE_FUNNEL_SCAN = "funnel_scan"

VALID_SOURCES = {SOURCE_AD_SCRAPE, SOURCE_DOMAIN_INTEL, SOURCE_FUNNEL_SCAN}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_source(source: str) -> None:
    if source not in VALID_SOURCES:
        raise ValueError(f"Unknown freshness source: {source!r}. Must be one of {VALID_SOURCES}")


def mark_success(source: str, competitor_id: str) -> None:
    """Record a successful run for (source, competitor)."""
    _validate_source(source)
    now = _now_iso()
    try:
        get_db().table("data_freshness").upsert(
            {
                "source": source,
                "competitor_id": competitor_id,
                "last_success_at": now,
                "updated_at": now,
            },
            on_conflict="source,competitor_id",
        ).execute()
    except Exception:
        # Freshness tracking must never break the worker. Log and continue.
        log.exception("mark_success failed for source=%s competitor=%s", source, competitor_id)


def mark_failure(source: str, competitor_id: str, error: str) -> None:
    """Record a failed run for (source, competitor) with a short error string."""
    _validate_source(source)
    now = _now_iso()
    try:
        get_db().table("data_freshness").upsert(
            {
                "source": source,
                "competitor_id": competitor_id,
                "last_failure_at": now,
                "last_error": (error or "")[:500],
                "updated_at": now,
            },
            on_conflict="source,competitor_id",
        ).execute()
    except Exception:
        log.exception("mark_failure failed for source=%s competitor=%s", source, competitor_id)


def get_stale_sources(
    stale_hours: int = FRESHNESS_STALE_HOURS,
) -> list[dict]:
    """Return every (source, competitor_id) row that is stale.

    A row is stale if:
      - it has never succeeded, OR
      - last_success_at is older than `stale_hours`, OR
      - last_failure_at is newer than last_success_at (last run failed).

    Returns a list of dicts with source, competitor_id, last_success_at,
    last_failure_at, last_error. Empty list means every tracked source is fresh.
    """
    threshold = datetime.now(timezone.utc) - timedelta(hours=stale_hours)
    threshold_iso = threshold.isoformat()
    try:
        res = (
            get_db()
            .table("data_freshness")
            .select(
                "source,competitor_id,last_success_at,last_failure_at,last_error"
            )
            .execute()
        )
    except Exception:
        log.exception("get_stale_sources query failed")
        return []

    stale: list[dict] = []
    for row in res.data or []:
        last_success = row.get("last_success_at")
        last_failure = row.get("last_failure_at")

        if not last_success:
            stale.append(row)
            continue
        if last_success < threshold_iso:
            stale.append(row)
            continue
        if last_failure and last_failure > last_success:
            stale.append(row)
            continue

    return stale


def is_stale(source: str, competitor_id: str, stale_hours: int = FRESHNESS_STALE_HOURS) -> bool:
    """Return True if this (source, competitor) row is stale."""
    _validate_source(source)
    try:
        res = (
            get_db()
            .table("data_freshness")
            .select("last_success_at,last_failure_at")
            .eq("source", source)
            .eq("competitor_id", competitor_id)
            .limit(1)
            .execute()
        )
    except Exception:
        log.exception("is_stale query failed for source=%s competitor=%s", source, competitor_id)
        return True  # fail-stale: if we cannot read, assume stale

    rows = res.data or []
    if not rows:
        return True

    row = rows[0]
    last_success = row.get("last_success_at")
    last_failure = row.get("last_failure_at")

    if not last_success:
        return True
    threshold_iso = (
        datetime.now(timezone.utc) - timedelta(hours=stale_hours)
    ).isoformat()
    if last_success < threshold_iso:
        return True
    if last_failure and last_failure > last_success:
        return True
    return False
