import logging

from fastapi import APIRouter
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
    # Fetch all, then deduplicate by competitor_id (take first = most recent)
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
