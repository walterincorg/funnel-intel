"""Apify integration for Meta Ads Library scraping."""

from __future__ import annotations
import logging
import time
from datetime import datetime, timezone
import requests

from backend.config import (
    APIFY_API_TOKEN,
    APIFY_ADS_ACTOR_ID,
    AD_SCRAPE_LIMIT_PER_SOURCE,
    AD_SCRAPE_COUNTRY_CODE,
)

log = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
POLL_INTERVAL = 10  # seconds between poll attempts
MAX_POLL_ATTEMPTS = 60  # 60 × 10s = 10 minutes max wait
REQUEST_TIMEOUT = 30  # seconds for individual HTTP requests


def scrape_competitor_ads(
    ads_library_url: str,
    limit_per_source: int | None = None,
    country_code: str | None = None,
) -> list[dict]:
    """Run the Apify Facebook Ads Library scraper and return ad items.

    Uses the async pattern: start run, poll until complete, fetch dataset.

    Args:
        ads_library_url: Meta Ads Library URL (page or keyword search).
        limit_per_source: Max ads per source URL. Defaults to AD_SCRAPE_LIMIT_PER_SOURCE.
                          This directly drives Apify cost (pay-per-event).
        country_code: Country filter for scrapePageAds. Defaults to AD_SCRAPE_COUNTRY_CODE.
                      Narrows scrape to one country to cap cost.
    """
    if not APIFY_API_TOKEN:
        raise RuntimeError("APIFY_API_TOKEN not configured")

    limit = limit_per_source if limit_per_source is not None else AD_SCRAPE_LIMIT_PER_SOURCE
    country = country_code if country_code is not None else AD_SCRAPE_COUNTRY_CODE

    actor_id = APIFY_ADS_ACTOR_ID.replace("/", "~")
    headers = {
        "Authorization": f"Bearer {APIFY_API_TOKEN}",
        "Content-Type": "application/json",
    }
    # Note: "scrapePageAds.countryCode" uses dot notation. The curious_coder actor
    # accepts this flat-dotted form for nested scrapePageAds options. If a future
    # actor version rejects it, switch to nested {"scrapePageAds": {"countryCode": country}}.
    payload = {
        "urls": [{"url": ads_library_url}],
        "limitPerSource": limit,
        "scrapePageAds.countryCode": country,
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

    log.info("Apify returned %d ads for %s (run %s)", len(items), ads_library_url, run_id)
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


def normalize_ad(raw: dict) -> dict:
    """Normalize an Apify ad item into our canonical field names.

    The Apify actor returns a nested structure with `snapshot` containing most fields.
    """
    snap = raw.get("snapshot") or {}
    body = snap.get("body") or {}

    # Extract start/end dates — may be Unix timestamps or ISO strings
    start_date = _parse_date(raw.get("start_date") or snap.get("ad_delivery_start_time"))
    stop_date = _parse_date(raw.get("end_date") or snap.get("ad_delivery_stop_time"))

    # Extract image/video from cards or snapshot
    cards = snap.get("cards") or []
    image_url = None
    video_url = None
    media_type = "image"

    videos = snap.get("videos") or []
    images = snap.get("images") or []
    if videos:
        video_url = videos[0].get("video_hd_url") or videos[0].get("video_sd_url")
        media_type = "video"
    elif images:
        image_url = images[0].get("original_image_url") or images[0].get("resized_image_url")
    elif cards:
        first_card = cards[0] if cards else {}
        image_url = first_card.get("original_image_url") or first_card.get("resized_image_url")
        if len(cards) > 1:
            media_type = "carousel"

    # Platforms from publisher_platforms
    platforms = snap.get("publisher_platforms") or raw.get("publisher_platforms") or []

    # Status — check if there's an end date or is_active flag
    is_active = raw.get("is_active")
    if is_active is True or (not stop_date and not raw.get("is_inactive")):
        status = "ACTIVE"
    else:
        status = "INACTIVE"

    return {
        "meta_ad_id": str(raw.get("ad_archive_id") or raw.get("id", "")),
        "status": status,
        "body_text": body.get("text") if isinstance(body, dict) else str(body) if body else None,
        "headline": snap.get("title") or snap.get("link_title"),
        "cta": snap.get("cta_text"),
        "image_url": image_url,
        "video_url": video_url,
        "start_date": start_date,
        "stop_date": stop_date,
        "platforms": platforms,
        "landing_page_url": snap.get("link_url") or snap.get("caption"),
        "advertiser_name": snap.get("page_name"),
        "page_id": str(snap.get("page_id") or raw.get("page_id", "")),
        "media_type": media_type,
        "impression_range": raw.get("eu_total_reach") or snap.get("impressions"),
    }
