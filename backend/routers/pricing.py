import logging

from fastapi import APIRouter, HTTPException
from backend.config import SUPABASE_STORAGE_BUCKET
from backend.db import get_db
from backend.models import PricingSnapshotOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pricing", tags=["pricing"])


@router.get("", response_model=list[PricingSnapshotOut])
def list_pricing(competitor_id: str | None = None, limit: int = 50):
    q = (
        get_db()
        .table("pricing_snapshots")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
    )
    if competitor_id:
        q = q.eq("competitor_id", competitor_id)
    return q.execute().data


@router.get("/latest", response_model=list[PricingSnapshotOut])
def latest_pricing_per_competitor():
    """Get the most recent pricing snapshot for each competitor."""
    res = (
        get_db()
        .table("pricing_snapshots")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    seen = set()
    latest = []
    for row in res.data:
        cid = row["competitor_id"]
        if cid not in seen:
            seen.add(cid)
            latest.append(row)
    return latest


@router.get("/{snapshot_id}/screenshot-url")
def get_screenshot_url(snapshot_id: str):
    """Return a short-lived signed URL to the pricing screenshot.

    The pricing-history UI needs to display the captured pricing screenshot
    inline so a reviewer can validate what the extractor saw. Screenshots
    live in a private Supabase bucket, so we mint a signed URL on demand.
    """
    db = get_db()
    res = db.table("pricing_snapshots").select("screenshot_path").eq("id", snapshot_id).single().execute()
    if not res.data:
        raise HTTPException(404, "Snapshot not found")
    path = res.data.get("screenshot_path")
    if not path:
        return {"url": None}
    try:
        signed = db.storage.from_(SUPABASE_STORAGE_BUCKET).create_signed_url(path, 60 * 60)
    except Exception:
        log.exception("Failed to mint signed URL for %s", path)
        return {"url": None}
    url = (signed or {}).get("signedURL") or (signed or {}).get("signed_url")
    return {"url": url}
