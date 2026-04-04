"""Apify integration for Meta Ads Library scraping."""

from __future__ import annotations
import logging
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

    url = f"{APIFY_BASE}/acts/{APIFY_ADS_ACTOR_ID}/run-sync-get-dataset-items"

    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {APIFY_API_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "startUrls": [{"url": ads_library_url}],
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


def normalize_ad(raw: dict) -> dict:
    """Normalize an Apify ad item into our canonical field names."""
    return {
        "meta_ad_id": raw.get("adArchiveID") or raw.get("ad_id") or raw.get("id", ""),
        "status": (raw.get("isActive") and "ACTIVE") or raw.get("status", "INACTIVE"),
        "body_text": raw.get("bodyText") or raw.get("body", {}).get("text"),
        "headline": raw.get("title") or raw.get("headline"),
        "cta": raw.get("ctaText") or raw.get("cta_text"),
        "image_url": raw.get("imageThumbnail") or raw.get("image_url"),
        "video_url": raw.get("videoURL") or raw.get("video_url"),
        "start_date": raw.get("startDate") or raw.get("ad_delivery_start_time"),
        "stop_date": raw.get("endDate") or raw.get("ad_delivery_stop_time"),
        "platforms": raw.get("publisherPlatform") or raw.get("platforms", []),
        "landing_page_url": raw.get("linkUrl") or raw.get("landing_page_url"),
        "advertiser_name": raw.get("pageName") or raw.get("advertiser_name"),
        "page_id": raw.get("pageId") or raw.get("page_id"),
        "media_type": raw.get("mediaType") or raw.get("media_type", "image"),
        "impression_range": raw.get("impressions") or raw.get("impression_range"),
    }
