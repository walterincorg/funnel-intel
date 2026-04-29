"""Daily ad scrape orchestration.

Called from the main worker loop. Checks whether a daily scrape is due,
then runs the full pipeline: Apify scrape -> upsert ads -> snapshot -> signals -> alerts.
"""

from __future__ import annotations
import hashlib
import logging
import mimetypes
import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

from backend.config import APIFY_API_TOKEN, SUPABASE_STORAGE_BUCKET
from backend.db import get_db
from backend.settings import get_settings
from backend.worker.ad_scraper import scrape_competitor_ads, normalize_ad
from backend.worker.ad_analysis import run_briefing
from backend.worker.ad_signals import compute_signals
from backend.worker.alerts import send_alert

log = logging.getLogger(__name__)
MEDIA_CACHE_PREFIX = "ad-media"
MEDIA_DOWNLOAD_TIMEOUT = 30
MAX_MEDIA_BYTES = 75 * 1024 * 1024


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

    # Auto-schedule — gated by DB setting (default: disabled)
    settings = get_settings()
    if not settings.get("ad_scrape_enabled", False):
        return

    scrape_hour = settings.get("ad_scrape_hour_utc", 6)
    scrape_days = set(settings.get("ad_scrape_days_of_week", [0, 3]))

    if today.weekday() not in scrape_days:
        return
    if now.hour < scrape_hour:
        return

    existing = (
        db.table("ad_scrape_runs")
        .select("id")
        .gte("created_at", today.isoformat())
        .in_("status", ["running", "completed"])
        .limit(1)
        .execute()
    )
    if existing.data:
        return

    log.info("Starting scheduled ad scrape for %s", today)
    _run_ad_scrape(today)


def _run_ad_scrape(today: date):
    """Execute the full ad scrape pipeline for all competitors."""
    pipeline_start = time.perf_counter()
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

        duration_ms = (time.perf_counter() - pipeline_start) * 1000
        log.info(
            "Ad scrape completed: %d competitors, %d ads, %d signals, briefing=%s (%.1fs)",
            competitors_scraped, total_ads, total_signals,
            "ok" if briefing_ok else "failed",
            duration_ms / 1000,
            extra={"ad_count": total_ads, "signal_count": total_signals,
                   "duration_ms": round(duration_ms)},
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
    _cache_ad_media(db, competitor_id, normalized)

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


def _cache_ad_media(db, competitor_id: str, normalized: list[dict]) -> None:
    """Persist freshly scraped Meta media before Facebook CDN links expire."""
    for ad in normalized:
        meta_id = ad.get("meta_ad_id")
        if not meta_id:
            continue
        for field, kind in (("image_url", "image"), ("video_url", "video")):
            url = ad.get(field)
            if not _should_cache_media_url(url):
                continue
            cached = _cache_media_url(db, competitor_id, str(meta_id), str(url), kind)
            if cached:
                ad[field] = cached


def _should_cache_media_url(url: str | None) -> bool:
    return bool(url and str(url).startswith(("http://", "https://")))


def _safe_media_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return safe[:80] or hashlib.sha256(value.encode()).hexdigest()[:16]


def _extension_for_media(url: str, content_type: str | None, kind: str) -> str:
    parsed_ext = mimetypes.guess_extension((content_type or "").split(";")[0].strip())
    if parsed_ext:
        return ".jpg" if parsed_ext == ".jpe" else parsed_ext
    path_ext = mimetypes.guess_type(urlparse(url).path)[0]
    guessed = mimetypes.guess_extension(path_ext or "")
    if guessed:
        return ".jpg" if guessed == ".jpe" else guessed
    return ".mp4" if kind == "video" else ".jpg"


def _cache_media_url(db, competitor_id: str, meta_ad_id: str, url: str, kind: str) -> str | None:
    try:
        with requests.get(url, stream=True, timeout=MEDIA_DOWNLOAD_TIMEOUT) as resp:
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "").split(";")[0].strip()
            content_length = int(resp.headers.get("content-length") or 0)
            if content_length > MAX_MEDIA_BYTES:
                log.warning("Skipping oversized ad %s media: %d bytes", kind, content_length)
                return None

            chunks = []
            size = 0
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_MEDIA_BYTES:
                    log.warning("Skipping oversized ad %s media after streaming", kind)
                    return None
                chunks.append(chunk)

        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        ext = _extension_for_media(url, content_type, kind)
        object_path = (
            f"{MEDIA_CACHE_PREFIX}/{_safe_media_id(competitor_id)}/"
            f"{_safe_media_id(meta_ad_id)}/{kind}-{digest}{ext}"
        )
        db.storage.from_(SUPABASE_STORAGE_BUCKET).upload(
            object_path,
            b"".join(chunks),
            file_options={
                "content-type": content_type or ("video/mp4" if kind == "video" else "image/jpeg"),
                "x-upsert": "true",
            },
        )
        return object_path
    except Exception:
        log.warning("Failed to cache ad %s media", kind, exc_info=True)
        return None


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
