"""Apify integration for Meta Ads Library scraping."""

from __future__ import annotations
import logging
from datetime import datetime, timezone
import requests

from backend.config import APIFY_API_TOKEN, APIFY_ADS_ACTOR_ID

log = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
SYNC_TIMEOUT = 300  # seconds — Apify sync endpoint blocks until done


def scrape_competitor_ads(ads_library_url: str) -> list[dict]:
    """Run the Apify Facebook Ads Library scraper and return ad items.

    Uses the synchronous run endpoint which blocks until the actor finishes
    and returns dataset items directly.
    """
    if not APIFY_API_TOKEN:
        raise RuntimeError("APIFY_API_TOKEN not configured")

    actor_id = APIFY_ADS_ACTOR_ID.replace("/", "~")
    url = f"{APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items"

    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {APIFY_API_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "urls": [{"url": ads_library_url}],
        },
        timeout=SYNC_TIMEOUT,
    )
    resp.raise_for_status()

    items = resp.json()
    if not isinstance(items, list):
        log.warning("Unexpected Apify response type: %s", type(items))
        return []

    log.info("Apify returned %d ads for %s", len(items), ads_library_url)
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
