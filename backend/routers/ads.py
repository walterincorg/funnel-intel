from datetime import date, timedelta

from fastapi import APIRouter, HTTPException
from backend.db import get_db
from backend.models import AdOut, AdSnapshotOut, AdSignalOut, AdScrapeRunOut, CompetitorAnalysisOut

router = APIRouter(prefix="/api/ads", tags=["ads"])


@router.get("", response_model=list[AdOut])
def list_ads(competitor_id: str | None = None, limit: int = 100):
    q = get_db().table("ads").select("*").order("last_seen_at", desc=True).limit(limit)
    if competitor_id:
        q = q.eq("competitor_id", competitor_id)
    return q.execute().data


@router.get("/signals", response_model=list[AdSignalOut])
def list_signals(
    competitor_id: str | None = None,
    signal_type: str | None = None,
    days: int = 7,
    limit: int = 200,
):
    since = (date.today() - timedelta(days=days)).isoformat()
    q = (
        get_db()
        .table("ad_signals")
        .select("*")
        .gte("signal_date", since)
        .order("created_at", desc=True)
        .limit(limit)
    )
    if competitor_id:
        q = q.eq("competitor_id", competitor_id)
    if signal_type:
        q = q.eq("signal_type", signal_type)
    return q.execute().data


@router.get("/signals/summary")
def signals_summary(days: int = 7):
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = (
        get_db()
        .table("ad_signals")
        .select("signal_type")
        .gte("signal_date", since)
        .execute()
        .data
    )
    counts: dict[str, int] = {}
    for row in rows:
        t = row["signal_type"]
        counts[t] = counts.get(t, 0) + 1
    return [{"signal_type": k, "count": v} for k, v in counts.items()]


@router.get("/analysis", response_model=list[CompetitorAnalysisOut])
def list_analyses(competitor_id: str | None = None):
    """Get the latest LLM analysis per competitor."""
    db = get_db()
    q = (
        db.table("competitor_analyses")
        .select("*")
        .order("analysis_date", desc=True)
    )
    if competitor_id:
        q = q.eq("competitor_id", competitor_id).limit(1)
    else:
        # Get all, then dedupe to latest per competitor in Python
        q = q.limit(200)

    rows = q.execute().data
    if not competitor_id:
        seen = set()
        deduped = []
        for row in rows:
            if row["competitor_id"] not in seen:
                seen.add(row["competitor_id"])
                deduped.append(row)
        return deduped
    return rows


@router.get("/scrape-runs", response_model=list[AdScrapeRunOut])
def list_scrape_runs(limit: int = 20):
    return (
        get_db()
        .table("ad_scrape_runs")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


@router.post("/scrape/trigger", status_code=201)
def trigger_scrape():
    """Manually trigger an ad scrape by inserting a pending run."""
    from datetime import datetime, timezone

    db = get_db()

    # Clean up stale pending/running rows so the worker doesn't double-fire
    db.table("ad_scrape_runs").update({"status": "cancelled"}).in_("status", ["pending", "running"]).execute()

    run = (
        db
        .table("ad_scrape_runs")
        .insert({"status": "pending"})
        .execute()
        .data[0]
    )
    return {"run_id": run["id"], "status": "pending"}


@router.get("/{ad_id}", response_model=AdOut)
def get_ad(ad_id: str):
    res = get_db().table("ads").select("*").eq("id", ad_id).single().execute()
    if not res.data:
        raise HTTPException(404, "Ad not found")
    return res.data


@router.get("/{ad_id}/snapshots", response_model=list[AdSnapshotOut])
def get_ad_snapshots(ad_id: str, limit: int = 30):
    return (
        get_db()
        .table("ad_snapshots")
        .select("*")
        .eq("ad_id", ad_id)
        .order("captured_date", desc=True)
        .limit(limit)
        .execute()
        .data
    )
