"""Apify integration for Meta Ads Library scraping."""

from __future__ import annotations
import logging
import time
from datetime import datetime, timezone
import requests

from backend.config import APIFY_API_TOKEN, APIFY_ADS_ACTOR_ID

log = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
POLL_INTERVAL = 10  # seconds between poll attempts
MAX_POLL_ATTEMPTS = 60  # 60 × 10s = 10 minutes max wait
REQUEST_TIMEOUT = 30  # seconds for individual HTTP requests


def scrape_competitor_ads(ads_library_url: str) -> list[dict]:
    """Run the Apify Facebook Ads Library scraper and return ad items.

    Uses the async pattern: start run, poll until complete, fetch dataset.
    """
    if not APIFY_API_TOKEN:
        raise RuntimeError("APIFY_API_TOKEN not configured")

    scrape_start = time.perf_counter()
    actor_id = APIFY_ADS_ACTOR_ID.replace("/", "~")
    headers = {
        "Authorization": f"Bearer {APIFY_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "urls": [{"url": ads_library_url}],
        "limitPerSource": 500,
        "scrapePageAds.countryCode": "US",
        "scrapePageAds.activeStatus": "active",
        "scrapePageAds.sortBy": "impressions_desc",
    }

    # Step 1: Start the actor run
    start_url = f"{APIFY_BASE}/acts/{actor_id}/runs"
    resp = requests.post(start_url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    run_data = resp.json().get("data", {})
    run_id = run_data.get("id")
    if not run_id:
        raise RuntimeError("Apify returned no run ID")
    log.info("Apify run started: %s", run_id)

    # Step 2: Poll until run completes
    run_url = f"{APIFY_BASE}/actor-runs/{run_id}"
    for attempt in range(MAX_POLL_ATTEMPTS):
        time.sleep(POLL_INTERVAL)
        poll_resp = requests.get(run_url, headers=headers, timeout=REQUEST_TIMEOUT)
        poll_resp.raise_for_status()
        status = poll_resp.json().get("data", {}).get("status")
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
        log.debug("Apify run %s status: %s (poll %d/%d)", run_id, status, attempt + 1, MAX_POLL_ATTEMPTS)
    else:
        raise TimeoutError(f"Apify run {run_id} did not complete after {MAX_POLL_ATTEMPTS * POLL_INTERVAL}s")

    if status != "SUCCEEDED":
        raise RuntimeError(f"Apify run {run_id} ended with status: {status}")

    # Step 3: Fetch dataset items
    dataset_id = poll_resp.json().get("data", {}).get("defaultDatasetId")
    if not dataset_id:
        raise RuntimeError(f"Apify run {run_id} has no dataset ID")

    items_url = f"{APIFY_BASE}/datasets/{dataset_id}/items"
    items_resp = requests.get(items_url, headers=headers, timeout=REQUEST_TIMEOUT)
    items_resp.raise_for_status()

    items = items_resp.json()
    if not isinstance(items, list):
        log.warning("Unexpected Apify dataset response type: %s", type(items))
        return []

    duration_ms = (time.perf_counter() - scrape_start) * 1000
    log.info("Apify returned %d ads for %s (run %s, %.1fs)",
             len(items), ads_library_url, run_id, duration_ms / 1000,
             extra={"ad_count": len(items), "duration_ms": round(duration_ms)})
    return items


def _parse_date(val) -> str | None:
    """Convert a date value (Unix timestamp, ISO string, or None) to ISO date string."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(val, tz=timezone.utc).date().isoformat()
        except (ValueError, OSError):
            return None
    s = str(val).strip()
    if not s:
        return None
    # If it looks like a pure number string, parse as timestamp
    if s.isdigit():
        try:
            return datetime.fromtimestamp(int(s), tz=timezone.utc).date().isoformat()
        except (ValueError, OSError):
            return None
    # Already an ISO-ish string — return the date part
    return s[:10] if len(s) >= 10 else s


def _first_value(data: dict | None, *keys: str):
    """Return the first present, non-empty value from a dict."""
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return value
    return None


def normalize_ad(raw: dict) -> dict:
    """Normalize an Apify ad item into our canonical field names.

    The Apify actor returns a nested structure with `snapshot` containing most fields.
    """
    snap = raw.get("snapshot") or {}
    body = snap.get("body") or {}

    # Extract start/end dates — may be Unix timestamps or ISO strings
    start_date = _parse_date(
        _first_value(raw, "start_date", "startDate")
        or _first_value(snap, "ad_delivery_start_time", "adDeliveryStartTime")
    )
    stop_date = _parse_date(
        _first_value(raw, "end_date", "endDate")
        or _first_value(snap, "ad_delivery_stop_time", "adDeliveryStopTime")
    )

    # Extract image/video from cards or snapshot
    cards = snap.get("cards") or []
    image_url = None
    video_url = None
    media_type = "image"

    videos = snap.get("videos") or []
    images = snap.get("images") or []
    if videos:
        video_url = _first_value(
            videos[0],
            "video_hd_url",
            "videoHdUrl",
            "videoHDUrl",
            "video_sd_url",
            "videoSdUrl",
            "videoSDUrl",
            "video_url",
            "videoUrl",
            "url",
        )
        media_type = "video"
    elif images:
        image_url = _first_value(
            images[0],
            "original_image_url",
            "originalImageUrl",
            "resized_image_url",
            "resizedImageUrl",
            "image_url",
            "imageUrl",
            "url",
        )
    elif cards:
        first_card = cards[0] if cards else {}
        video_url = _first_value(
            first_card,
            "video_hd_url",
            "videoHdUrl",
            "videoHDUrl",
            "video_sd_url",
            "videoSdUrl",
            "videoSDUrl",
            "video_url",
            "videoUrl",
        )
        image_url = _first_value(
            first_card,
            "original_image_url",
            "originalImageUrl",
            "resized_image_url",
            "resizedImageUrl",
            "image_url",
            "imageUrl",
        )
        if len(cards) > 1:
            media_type = "carousel"
        elif video_url:
            media_type = "video"

    # Platforms from publisher_platforms
    platforms = (
        _first_value(snap, "publisher_platforms", "publisherPlatforms")
        or _first_value(raw, "publisher_platforms", "publisherPlatforms")
        or []
    )

    # Status — trust is_active flag from Apify; end_date is unreliable
    # (scraper sets end_date to current date on ALL ads, even active ones)
    is_active = _first_value(raw, "is_active", "isActive")
    if is_active is True:
        status = "ACTIVE"
    elif is_active is False or _first_value(raw, "is_inactive", "isInactive"):
        status = "INACTIVE"
    else:
        status = "ACTIVE"  # default to active if no flag present

    return {
        "meta_ad_id": str(_first_value(raw, "ad_archive_id", "adArchiveId", "adArchiveID", "id") or ""),
        "status": status,
        "body_text": body.get("text") if isinstance(body, dict) else str(body) if body else None,
        "headline": _first_value(snap, "title", "link_title", "linkTitle"),
        "cta": _first_value(snap, "cta_text", "ctaText"),
        "image_url": image_url,
        "video_url": video_url,
        "start_date": start_date,
        "stop_date": stop_date,
        "platforms": platforms,
        "landing_page_url": _first_value(snap, "link_url", "linkUrl", "caption"),
        "advertiser_name": _first_value(snap, "page_name", "pageName"),
        "page_id": str(
            _first_value(snap, "page_id", "pageId")
            or _first_value(raw, "page_id", "pageId")
            or ""
        ),
        "media_type": media_type,
        "impression_range": _first_value(raw, "eu_total_reach", "euTotalReach") or snap.get("impressions"),
    }
