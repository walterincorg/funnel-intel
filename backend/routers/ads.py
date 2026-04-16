import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException
from backend.db import get_db
from backend.models import AdOut, AdSnapshotOut, AdSignalOut, AdScrapeRunOut, AdBriefingOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ads", tags=["ads"])


@router.get("", response_model=list[AdOut])
def list_ads(competitor_id: str | None = None, status: str = "ACTIVE", limit: int = 100):
    q = get_db().table("ads").select("*").order("last_seen_at", desc=True).limit(limit)
    if competitor_id:
        q = q.eq("competitor_id", competitor_id)
    if status != "ALL":
        q = q.eq("status", status)
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


@router.get("/briefing", response_model=AdBriefingOut | None)
def get_briefing():
    """Get the latest CEO ad briefing."""
    db = get_db()
    try:
        rows = (
            db.table("ad_briefings")
            .select("*")
            .order("briefing_date", desc=True)
            .limit(1)
            .execute()
            .data
        )
    except Exception:
        return None

    return rows[0] if rows else None


@router.get("/winners", response_model=list[dict])
def list_winners(limit: int = 10, period: str = "all-time"):
    """Get top winner ads across all competitors.

    period=all-time: all active ads sorted by longest-running.
    period=recent: ads running 30+ days and still active.
    """
    db = get_db()
    ads = (
        db.table("ads")
        .select("id, meta_ad_id, competitor_id, media_type, landing_page_url, status")
        .eq("status", "ACTIVE")
        .order("first_seen_at", desc=False)
        .limit(1000)
        .execute()
        .data
    )
    if not ads:
        return []

    # Get latest snapshot per ad
    ad_ids = [a["id"] for a in ads]
    all_snaps = []
    for i in range(0, len(ad_ids), 50):
        batch = ad_ids[i:i + 50]
        batch_res = (
            db.table("ad_snapshots")
            .select("ad_id, headline, body_text, image_url, video_url, start_date, stop_date, cta")
            .in_("ad_id", batch)
            .order("captured_date", desc=True)
            .execute()
        )
        all_snaps.extend(batch_res.data)

    latest_snaps = {}
    for snap in all_snaps:
        if snap["ad_id"] not in latest_snaps:
            latest_snaps[snap["ad_id"]] = snap

    # Get competitor names
    comps = db.table("competitors").select("id, name").execute().data
    comp_names = {c["id"]: c["name"] for c in comps}

    today = date.today()
    min_days = 30 if period == "recent" else 0
    ranked = []
    for ad in ads:
        snap = latest_snaps.get(ad["id"], {})
        start = snap.get("start_date")
        if not start:
            continue
        try:
            days = (today - date.fromisoformat(str(start)[:10])).days
        except (ValueError, TypeError):
            continue

        if days < min_days:
            continue

        ranked.append({
            "ad_id": ad["id"],
            "meta_ad_id": ad["meta_ad_id"],
            "competitor_id": ad["competitor_id"],
            "competitor_name": comp_names.get(ad["competitor_id"], "Unknown"),
            "media_type": ad.get("media_type"),
            "headline": snap.get("headline"),
            "body_text": (snap.get("body_text") or "")[:200],
            "image_url": snap.get("image_url"),
            "video_url": snap.get("video_url"),
            "cta": snap.get("cta"),
            "days_active": days,
            "landing_page_url": ad.get("landing_page_url"),
        })

    ranked.sort(key=lambda x: x["days_active"], reverse=True)
    return ranked[:limit]


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
    log.info("Ad scrape triggered manually: run=%s", run["id"])
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
