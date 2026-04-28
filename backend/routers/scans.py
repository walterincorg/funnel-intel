import logging

from fastapi import APIRouter, HTTPException
from backend.db import get_db
from backend.config import DEFAULT_TRAVERSAL_MODEL
from backend.models import ScanRunOut, ScanStepOut, ScanTrigger
from typing import Any

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scans", tags=["scans"])


@router.get("/jobs/active")
def list_active_jobs() -> list[dict[str, Any]]:
    """Return all pending or picked jobs so the UI can show accurate button state."""
    res = (
        get_db()
        .table("scan_jobs")
        .select("id,competitor_id,status,traversal_model,created_at,picked_at")
        .in_("status", ["pending", "picked"])
        .execute()
    )
    return res.data


@router.get("", response_model=list[ScanRunOut])
def list_scans(competitor_id: str | None = None, limit: int = 50):
    q = get_db().table("scan_runs").select("*").order("created_at", desc=True).limit(limit)
    if competitor_id:
        q = q.eq("competitor_id", competitor_id)
    return q.execute().data


@router.get("/{run_id}", response_model=ScanRunOut)
def get_scan(run_id: str):
    res = get_db().table("scan_runs").select("*").eq("id", run_id).single().execute()
    if not res.data:
        raise HTTPException(404, "Scan run not found")
    return res.data


@router.get("/{run_id}/steps", response_model=list[ScanStepOut])
def get_scan_steps(run_id: str):
    res = (
        get_db()
        .table("scan_steps")
        .select("*")
        .eq("run_id", run_id)
        .order("step_number")
        .order("created_at", desc=True)
        .execute()
    )
    # Deduplicate — keep first (latest created_at) per step_number
    seen: dict[int, dict] = {}
    for row in res.data:
        num = row.get("step_number")
        if num not in seen:
            seen[num] = row
    return sorted(seen.values(), key=lambda r: r.get("step_number", 0))


@router.post("/trigger", status_code=201)
def trigger_scan(body: ScanTrigger):
    """Enqueue a new scan job for a competitor."""
    db = get_db()

    # Verify competitor exists
    comp = db.table("competitors").select("id").eq("id", body.competitor_id).single().execute()
    if not comp.data:
        raise HTTPException(404, "Competitor not found")

    # Dedup: return existing job if one is already pending or picked
    existing = (
        db.table("scan_jobs")
        .select("id,status")
        .eq("competitor_id", body.competitor_id)
        .in_("status", ["pending", "picked"])
        .limit(1)
        .execute()
    )
    if existing.data:
        log.info("Scan trigger deduplicated for competitor %s (existing job %s)", body.competitor_id, existing.data[0]["id"])
        return {"job_id": existing.data[0]["id"], "status": existing.data[0]["status"]}

    res = (
        db.table("scan_jobs")
        .insert({
            "competitor_id": body.competitor_id,
            "priority": body.priority,
            "status": "pending",
            "traversal_model": body.traversal_model or DEFAULT_TRAVERSAL_MODEL,
        })
        .execute()
    )
    log.info("Scan job enqueued: job=%s competitor=%s priority=%d", res.data[0]["id"], body.competitor_id, body.priority)
    return {"job_id": res.data[0]["id"], "status": "pending"}
