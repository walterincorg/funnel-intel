"""Daily ad scrape orchestration.

Called from the main worker loop. Checks whether a daily scrape is due,
then runs the full pipeline: Apify scrape -> upsert ads -> snapshot -> signals -> alerts.
"""

from __future__ import annotations
import logging
from datetime import date, datetime, timedelta, timezone

from backend.config import APIFY_API_TOKEN, AD_SCRAPE_HOUR_UTC, AD_SCRAPE_DAYS_OF_WEEK
from backend.db import get_db
from backend.worker.ad_scraper import scrape_competitor_ads, normalize_ad
from backend.worker.ad_analysis import run_briefing
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

    # Check day-of-week schedule
    if today.weekday() not in AD_SCRAPE_DAYS_OF_WEEK:
        return

    # Otherwise check scheduled time
    if now.hour < AD_SCRAPE_HOUR_UTC:
        return

    # Check if already ran today (completed or running)
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

    # Stop retrying after 3 failures today (prevents infinite loop on persistent errors)
    failed_today = (
        db.table("ad_scrape_runs")
        .select("id")
        .gte("created_at", today.isoformat())
        .eq("status", "failed")
        .execute()
    )
    if len(failed_today.data) >= 3:
        return

    log.info("Starting daily ad scrape for %s", today)
    _run_ad_scrape(today)


def _run_ad_scrape(today: date):
    """Execute the full ad scrape pipeline for all competitors."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Claim an existing pending row (from manual trigger) or create a new running row
    pending = (
        db.table("ad_scrape_runs")
        .select("id")
        .eq("status", "pending")
        .limit(1)
        .execute()
    )
    if pending.data:
        run_id = pending.data[0]["id"]
        db.table("ad_scrape_runs").update({
            "status": "running",
            "started_at": now,
        }).eq("id", run_id).execute()
    else:
        run = db.table("ad_scrape_runs").insert({
            "status": "running",
            "started_at": now,
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
            # Yield to scan jobs — they take priority over ad scraping
            if db.table("scan_jobs").select("id").eq("status", "pending").limit(1).execute().data:
                log.info("Pending scan job detected — pausing ad scrape to run scan first")
                db.table("ad_scrape_runs").update({"status": "pending"}).eq("id", run_id).execute()
                return

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

        # Mark stale ads as INACTIVE — any ad not seen in 3+ days is likely dead
        stale_cutoff = (today - timedelta(days=3)).isoformat()
        stale_res = (
            db.table("ads")
            .update({"status": "INACTIVE"})
            .eq("status", "ACTIVE")
            .lt("last_seen_at", stale_cutoff)
            .execute()
        )
        stale_count = len(stale_res.data) if stale_res.data else 0
        if stale_count:
            log.info("Marked %d stale ads as INACTIVE (not seen since %s)", stale_count, stale_cutoff)

        # Generate cross-competitor CEO briefing (single LLM call)
        briefing_ok = False
        try:
            briefing_ok = run_briefing(today)
        except Exception:
            log.exception("CEO briefing generation failed")

        db.table("ad_scrape_runs").update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "competitors_scraped": competitors_scraped,
            "ads_found": total_ads,
            "signals_generated": total_signals,
            "analyses_completed": 1 if briefing_ok else 0,
            "analyses_failed": 0 if briefing_ok else 1,
        }).eq("id", run_id).execute()

        log.info(
            "Ad scrape completed: %d competitors, %d ads, %d signals, briefing=%s",
            competitors_scraped, total_ads, total_signals,
            "ok" if briefing_ok else "failed",
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

    # Build meta_ad_id -> raw_data lookup (fixes O(n²) .index() bug)
    meta_to_raw = {}
    for raw, norm in zip(raw_ads, [normalize_ad(r) for r in raw_ads]):
        if norm["meta_ad_id"]:
            meta_to_raw[norm["meta_ad_id"]] = raw

    # 2. Batch upsert ads
    ad_id_map = _batch_upsert_ads(db, competitor_id, normalized)

    # 3. Batch upsert snapshots
    _batch_upsert_snapshots(db, competitor_id, normalized, ad_id_map, meta_to_raw, today)

    # 4. Compute signals (pass ad_id_map so new_ad signals get correct ad_id)
    signals = compute_signals(competitor_id, normalized, today, ad_id_map=ad_id_map)

    # 5. Insert signals
    for sig in signals:
        db.table("ad_signals").insert(sig).execute()

    # 6. Alert only on winner ads and count spikes
    alert_signals = [s for s in signals if s["signal_type"] in ("proven_winner", "count_spike")]
    if alert_signals:
        lines = [f"Ad Intel — {name}:"]
        for s in alert_signals:
            icon = "WINNER" if s["signal_type"] == "proven_winner" else "SPIKE"
            lines.append(f"  [{icon}] {s['title']}")
        send_alert("\n".join(lines))

    log.info("  %s: %d ads, %d signals", name, len(normalized), len(signals))
    return len(normalized), len(signals)


def _batch_upsert_ads(
    db, competitor_id: str, normalized: list[dict]
) -> dict[str, str]:
    """Batch upsert ads and return a meta_ad_id -> db_id map."""
    # Fetch all existing ads for this competitor
    existing_res = (
        db.table("ads")
        .select("id, meta_ad_id")
        .eq("competitor_id", competitor_id)
        .execute()
    )
    existing_map = {row["meta_ad_id"]: row["id"] for row in existing_res.data}

    now = datetime.now(timezone.utc).isoformat()
    new_rows = []
    update_rows = []

    for ad in normalized:
        meta_id = ad["meta_ad_id"]
        if meta_id in existing_map:
            update_rows.append({
                "competitor_id": competitor_id,
                "meta_ad_id": meta_id,
                "last_seen_at": now,
                "status": ad.get("status"),
                "platforms": ad.get("platforms"),
                "landing_page_url": ad.get("landing_page_url"),
                "media_type": ad.get("media_type"),
            })
        else:
            new_rows.append({
                "competitor_id": competitor_id,
                "meta_ad_id": meta_id,
                "status": ad.get("status"),
                "advertiser_name": ad.get("advertiser_name"),
                "page_id": ad.get("page_id"),
                "media_type": ad.get("media_type"),
                "platforms": ad.get("platforms"),
                "landing_page_url": ad.get("landing_page_url"),
            })

    # Batch upsert (try .upsert(), fall back to individual inserts)
    if new_rows:
        try:
            res = db.table("ads").upsert(new_rows, on_conflict="meta_ad_id,competitor_id").execute()
            for row in res.data:
                existing_map[row["meta_ad_id"]] = row["id"]
        except Exception:
            log.warning("Batch upsert failed for new ads, falling back to individual inserts")
            for row in new_rows:
                inserted = db.table("ads").insert(row).execute()
                existing_map[row["meta_ad_id"]] = inserted.data[0]["id"]

    if update_rows:
        try:
            res = db.table("ads").upsert(update_rows, on_conflict="meta_ad_id,competitor_id").execute()
            for row in res.data:
                existing_map[row["meta_ad_id"]] = row["id"]
        except Exception:
            log.warning("Batch upsert failed for existing ads, falling back to individual updates")
            for row in update_rows:
                db.table("ads").update({
                    "last_seen_at": row["last_seen_at"],
                    "status": row["status"],
                    "platforms": row["platforms"],
                    "landing_page_url": row["landing_page_url"],
                    "media_type": row["media_type"],
                }).eq("competitor_id", competitor_id).eq("meta_ad_id", row["meta_ad_id"]).execute()

    return existing_map


def _batch_upsert_snapshots(
    db,
    competitor_id: str,
    normalized: list[dict],
    ad_id_map: dict[str, str],
    meta_to_raw: dict[str, dict],
    today: date,
):
    """Batch upsert ad snapshots for today."""
    snap_rows = []
    for ad in normalized:
        meta_id = ad["meta_ad_id"]
        ad_db_id = ad_id_map.get(meta_id)
        if not ad_db_id:
            continue
        snap_rows.append({
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
            "raw_data": meta_to_raw.get(meta_id),
        })

    if snap_rows:
        try:
            db.table("ad_snapshots").upsert(snap_rows, on_conflict="ad_id,captured_date").execute()
        except Exception:
            log.warning("Batch snapshot upsert failed, falling back to individual inserts")
            for row in snap_rows:
                existing_snap = (
                    db.table("ad_snapshots")
                    .select("id")
                    .eq("ad_id", row["ad_id"])
                    .eq("captured_date", row["captured_date"])
                    .limit(1)
                    .execute()
                )
                if existing_snap.data:
                    db.table("ad_snapshots").update(row).eq("id", existing_snap.data[0]["id"]).execute()
                else:
                    db.table("ad_snapshots").insert(row).execute()
