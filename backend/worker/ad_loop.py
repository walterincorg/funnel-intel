"""Daily ad scrape orchestration.

Called from the main worker loop. Checks whether a daily scrape is due,
then runs the full pipeline: Apify scrape -> upsert ads -> snapshot -> signals -> alerts.
"""

from __future__ import annotations
import logging
from datetime import date, datetime, timezone

from backend.config import APIFY_API_TOKEN, AD_SCRAPE_HOUR_UTC
from backend.db import get_db
from backend.worker.ad_scraper import scrape_competitor_ads, normalize_ad
from backend.worker.ad_signals import compute_signals
from backend.worker.alerts import send_alert

log = logging.getLogger(__name__)


def maybe_run_ad_scrape():
    """Check if a daily ad scrape is due and run it if so.

    Guards:
    - APIFY_API_TOKEN must be configured
    - No currently running ad_scrape_runs
    - Either: a pending (manually triggered) run exists, OR it's past the daily scrape hour with no completed run today
    """
    if not APIFY_API_TOKEN:
        return

    now = datetime.now(timezone.utc)
    today = now.date()
    db = get_db()

    # Check for manually triggered pending runs first
    pending = (
        db.table("ad_scrape_runs")
        .select("id")
        .eq("status", "pending")
        .limit(1)
        .execute()
    )

    if pending.data:
        log.info("Found manually triggered ad scrape, starting now")
        _run_ad_scrape(today)
        return

    # Otherwise check scheduled time
    if now.hour < AD_SCRAPE_HOUR_UTC:
        return

    # Check if already ran today
    existing = (
        db.table("ad_scrape_runs")
        .select("id, status")
        .gte("created_at", today.isoformat())
        .in_("status", ["running", "completed"])
        .limit(1)
        .execute()
    )
    if existing.data:
        return

    log.info("Starting daily ad scrape for %s", today)
    _run_ad_scrape(today)


def _run_ad_scrape(today: date):
    """Execute the full ad scrape pipeline for all competitors."""
    db = get_db()

    # Create scrape run record
    run = db.table("ad_scrape_runs").insert({
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }).execute().data[0]
    run_id = run["id"]

    total_ads = 0
    total_signals = 0
    competitors_scraped = 0

    try:
        # Get all competitors with ads_library_url
        comps = (
            db.table("competitors")
            .select("id, name, ads_library_url")
            .not_.is_("ads_library_url", "null")
            .execute()
            .data
        )

        if not comps:
            log.info("No competitors with ads_library_url configured")
            db.table("ad_scrape_runs").update({
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", run_id).execute()
            return

        for comp in comps:
            try:
                ads_count, signals_count = _scrape_one_competitor(
                    comp["id"], comp["name"], comp["ads_library_url"], today
                )
                total_ads += ads_count
                total_signals += signals_count
                competitors_scraped += 1
            except Exception:
                log.exception("Failed to scrape ads for %s", comp["name"])
                send_alert(f"Ad scrape failed for {comp['name']}")

        db.table("ad_scrape_runs").update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "competitors_scraped": competitors_scraped,
            "ads_found": total_ads,
            "signals_generated": total_signals,
        }).eq("id", run_id).execute()

        log.info(
            "Ad scrape completed: %d competitors, %d ads, %d signals",
            competitors_scraped, total_ads, total_signals,
        )

    except Exception as e:
        log.exception("Ad scrape run failed")
        db.table("ad_scrape_runs").update({
            "status": "failed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(e)[:500],
        }).eq("id", run_id).execute()
        send_alert(f"Ad scrape run failed: {e}")


def _scrape_one_competitor(
    competitor_id: str, name: str, ads_library_url: str, today: date
) -> tuple[int, int]:
    """Scrape, ingest, and compute signals for one competitor. Returns (ads_count, signals_count)."""
    db = get_db()
    log.info("Scraping ads for %s", name)

    # 1. Fetch from Apify
    raw_ads = scrape_competitor_ads(ads_library_url)
    normalized = [normalize_ad(raw) for raw in raw_ads]
    normalized = [a for a in normalized if a["meta_ad_id"]]  # filter blanks

    # 2. Upsert ads + insert snapshots
    for ad in normalized:
        # Upsert into ads table
        existing = (
            db.table("ads")
            .select("id")
            .eq("competitor_id", competitor_id)
            .eq("meta_ad_id", ad["meta_ad_id"])
            .limit(1)
            .execute()
        )

        if existing.data:
            ad_db_id = existing.data[0]["id"]
            db.table("ads").update({
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
                "status": ad.get("status"),
                "platforms": ad.get("platforms"),
                "landing_page_url": ad.get("landing_page_url"),
                "media_type": ad.get("media_type"),
            }).eq("id", ad_db_id).execute()
        else:
            inserted = db.table("ads").insert({
                "competitor_id": competitor_id,
                "meta_ad_id": ad["meta_ad_id"],
                "status": ad.get("status"),
                "advertiser_name": ad.get("advertiser_name"),
                "page_id": ad.get("page_id"),
                "media_type": ad.get("media_type"),
                "platforms": ad.get("platforms"),
                "landing_page_url": ad.get("landing_page_url"),
            }).execute()
            ad_db_id = inserted.data[0]["id"]

        # Insert snapshot (upsert on ad_id + captured_date)
        existing_snap = (
            db.table("ad_snapshots")
            .select("id")
            .eq("ad_id", ad_db_id)
            .eq("captured_date", today.isoformat())
            .limit(1)
            .execute()
        )

        snap_data = {
            "ad_id": ad_db_id,
            "competitor_id": competitor_id,
            "captured_date": today.isoformat(),
            "status": ad.get("status"),
            "body_text": ad.get("body_text"),
            "headline": ad.get("headline"),
            "cta": ad.get("cta"),
            "image_url": ad.get("image_url"),
            "video_url": ad.get("video_url"),
            "start_date": ad.get("start_date"),
            "stop_date": ad.get("stop_date"),
            "platforms": ad.get("platforms"),
            "impression_range": ad.get("impression_range"),
            "landing_page_url": ad.get("landing_page_url"),
            "raw_data": raw_ads[normalized.index(ad)] if ad in normalized else None,
        }

        if existing_snap.data:
            db.table("ad_snapshots").update(snap_data).eq("id", existing_snap.data[0]["id"]).execute()
        else:
            db.table("ad_snapshots").insert(snap_data).execute()

    # 3. Compute signals
    signals = compute_signals(competitor_id, normalized, today)

    # 4. Insert signals
    for sig in signals:
        db.table("ad_signals").insert(sig).execute()

    # 5. Alert on high-severity signals
    high_signals = [s for s in signals if s["severity"] in ("high", "critical")]
    if high_signals:
        lines = [f"Ad Intel — {name}:"]
        for s in high_signals:
            icon = {"new_ad": "NEW", "proven_winner": "WINNER", "count_spike": "SPIKE",
                    "copy_change": "COPY", "platform_expansion": "EXPAND", "failed_test": "FAIL"}.get(
                s["signal_type"], "SIGNAL"
            )
            lines.append(f"  [{icon}] {s['title']}")
        send_alert("\n".join(lines))

    log.info("  %s: %d ads, %d signals", name, len(normalized), len(signals))
    return len(normalized), len(signals)
