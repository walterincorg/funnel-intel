import logging

from fastapi import APIRouter, HTTPException
from backend.db import get_db
from backend.models import Competitor, CompetitorCreate, CompetitorUpdate, FunnelRecordingOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/competitors", tags=["competitors"])


@router.get("", response_model=list[Competitor])
def list_competitors():
    res = get_db().table("competitors").select("*").order("created_at").execute()
    return res.data


@router.get("/{competitor_id}", response_model=Competitor)
def get_competitor(competitor_id: str):
    res = get_db().table("competitors").select("*").eq("id", competitor_id).single().execute()
    if not res.data:
        raise HTTPException(404, "Competitor not found")
    return res.data


@router.post("", response_model=Competitor, status_code=201)
def create_competitor(body: CompetitorCreate):
    res = get_db().table("competitors").insert(body.model_dump(exclude_none=True)).execute()
    log.info("Competitor created: %s (id=%s)", body.name, res.data[0]["id"])
    return res.data[0]


@router.patch("/{competitor_id}", response_model=Competitor)
def update_competitor(competitor_id: str, body: CompetitorUpdate):
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(400, "No fields to update")
    res = (
        get_db()
        .table("competitors")
        .update(data)
        .eq("id", competitor_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Competitor not found")
    log.info("Competitor updated: %s fields=%s", competitor_id, list(data.keys()))
    return res.data[0]


@router.delete("/{competitor_id}", status_code=204)
def delete_competitor(competitor_id: str):
    log.info("Competitor deleted: %s", competitor_id)
    get_db().table("competitors").delete().eq("id", competitor_id).execute()


@router.get("/{competitor_id}/recording", response_model=FunnelRecordingOut)
def get_competitor_recording(competitor_id: str):
    """Return the funnel_recordings row for the competitor's scripted replay.

    Used by the dashboard to render the "Recording" card + trace.zip artifact
    link on the scan detail page. 404 if the competitor has no recording yet
    (i.e. scripted replay isn't active for them).
    """
    res = (
        get_db()
        .table("funnel_recordings")
        .select("*")
        .eq("competitor_id", competitor_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "No recording for this competitor yet")
    row = res.data[0]

    trace_url = None
    if row.get("trace_path"):
        # Lazy import so the router doesn't pull the worker module graph on cold start.
        from backend.worker.recorder import get_signed_trace_url
        trace_url = get_signed_trace_url(row["trace_path"])

    return {**row, "trace_url": trace_url}


@router.delete("/{competitor_id}/recording", status_code=204)
def delete_competitor_recording(competitor_id: str):
    """Force a re-record on the next run. Used from ops when a funnel has
    drifted far enough that in-place patches no longer make sense."""
    get_db().table("funnel_recordings").delete().eq("competitor_id", competitor_id).execute()
    log.info("Recording cleared for competitor %s", competitor_id)
