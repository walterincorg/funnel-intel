"""Reverse tracking code lookup via SpyOnWeb API.

Given a GA ID or Pixel ID, find all other domains using the same code.
"""

from __future__ import annotations
import logging
import os
import requests
from datetime import datetime, timezone

from backend.db import get_db
from backend.worker.alerts import send_alert

log = logging.getLogger(__name__)

SPYONWEB_API_TOKEN = os.getenv("SPYONWEB_API_TOKEN", "")
SPYONWEB_BASE_URL = "https://api.spyonweb.com/v1"
DAILY_BUDGET = 100  # free tier limit


def _get_daily_usage(db) -> int:
    """Get today's SpyOnWeb API usage count."""
    today = datetime.now(timezone.utc).date().isoformat()
    rows = db.table("domain_fingerprints").select("id").execute().data
    # Use a simple metadata approach: count discovered_domains created today via reverse_lookup
    today_lookups = (
        db.table("discovered_domains")
        .select("id")
        .eq("discovery_source", "reverse_lookup")
        .gte("first_seen_at", today)
        .execute()
        .data
    )
    # Rough proxy: each lookup produces 0-N domains, but we count API calls as ~1 per unique code checked
    return len(today_lookups)


def _score_relevance(domain: str, is_known_competitor: bool) -> str:
    """Score discovered domain relevance."""
    # Known competitor domains are always high
    if is_known_competitor:
        return "high"

    # Heuristics for junk detection
    lower = domain.lower()
    if any(pattern in lower for pattern in [
        "staging", "dev.", "test.", "localhost", "example.",
        ".local", "preview.", "draft."
    ]):
        return "low"

    # Parked domain indicators
    if any(pattern in lower for pattern in [
        "parked", "forsale", "underconstruction"
    ]):
        return "low"

    return "medium"


def lookup_tracking_code(code_type: str, code_value: str) -> list[dict] | None:
    """Look up a tracking code on SpyOnWeb. Returns list of domains or None on error."""
    if not SPYONWEB_API_TOKEN:
        return None

    # Map our types to SpyOnWeb endpoints
    endpoint_map = {
        "google_analytics": "analytics",
        "facebook_pixel": "adsense",  # SpyOnWeb groups ad pixels under adsense
        "gtm": "analytics",
    }
    endpoint = endpoint_map.get(code_type)
    if not endpoint:
        return None

    try:
        resp = requests.get(
            f"{SPYONWEB_BASE_URL}/{endpoint}/{code_value}",
            params={"access_token": SPYONWEB_API_TOKEN},
            timeout=15,
        )

        if resp.status_code == 401:
            log.error("SpyOnWeb API key invalid (401)")
            send_alert("SpyOnWeb API key invalid, reverse lookups disabled")
            return None

        if resp.status_code == 429:
            log.warning("SpyOnWeb rate limit hit (429)")
            return None

        resp.raise_for_status()
        data = resp.json()

        # SpyOnWeb returns domains under result -> {code_value} -> items
        result = data.get("result", {})
        code_data = result.get(endpoint, {}).get(code_value, {})
        items = code_data.get("items", {})

        domains = []
        for domain, count in items.items():
            domains.append({"domain": domain, "count": count})

        return domains

    except requests.exceptions.HTTPError:
        log.exception("SpyOnWeb API error for %s=%s", code_type, code_value)
        return None
    except Exception:
        log.exception("SpyOnWeb lookup failed for %s=%s", code_type, code_value)
        return None


def run_reverse_lookups() -> int:
    """Run reverse lookups for all unique high-value tracking codes.

    Returns number of new domains discovered.
    """
    if not SPYONWEB_API_TOKEN:
        log.info("SpyOnWeb API token not configured, skipping reverse lookups")
        return 0

    db = get_db()

    # Check daily budget
    usage = _get_daily_usage(db)
    if usage >= DAILY_BUDGET:
        log.warning("SpyOnWeb daily limit reached (%d/%d), skipping reverse lookups", usage, DAILY_BUDGET)
        return 0

    # Get unique high-value tracking codes (GA and Pixel only)
    fingerprints = (
        db.table("domain_fingerprints")
        .select("fingerprint_type, fingerprint_value, competitor_id")
        .in_("fingerprint_type", ["google_analytics", "facebook_pixel"])
        .execute()
        .data
    )

    # Deduplicate by (type, value)
    seen_codes: set[tuple[str, str]] = set()
    unique_codes: list[dict] = []
    for fp in fingerprints:
        key = (fp["fingerprint_type"], fp["fingerprint_value"])
        if key not in seen_codes:
            seen_codes.add(key)
            unique_codes.append(fp)

    # Get known competitor domains for relevance scoring
    competitors = db.table("competitors").select("id, funnel_url").execute().data
    known_domains = set()
    for c in competitors:
        if c.get("funnel_url"):
            from urllib.parse import urlparse
            known_domains.add(urlparse(c["funnel_url"]).netloc.lower())

    # Get competitor_id -> name map for discovery reasons
    comp_names = {c["id"]: c.get("name", c["id"]) for c in
                  db.table("competitors").select("id, name").execute().data}

    new_domains = 0

    for code in unique_codes:
        if usage >= DAILY_BUDGET:
            log.warning("SpyOnWeb daily limit reached mid-run, stopping")
            break

        code_type = code["fingerprint_type"]
        code_value = code["fingerprint_value"]
        comp_name = comp_names.get(code["competitor_id"], "unknown")

        domains = lookup_tracking_code(code_type, code_value)
        if domains is None:
            continue

        usage += 1

        for d in domains:
            domain = d["domain"].lower().strip(".")

            # Skip our own known competitor domains
            if domain in known_domains:
                continue

            is_known = domain in known_domains
            relevance = _score_relevance(domain, is_known)

            try:
                db.table("discovered_domains").upsert({
                    "domain": domain,
                    "discovery_source": "reverse_lookup",
                    "discovery_reason": f"shares {code_type} {code_value} with {comp_name}",
                    "linked_fingerprint_value": code_value,
                    "relevance": relevance,
                    "last_checked_at": datetime.now(timezone.utc).isoformat(),
                }, on_conflict="domain").execute()
                new_domains += 1

                # Link to the competitor that has this code
                dd = db.table("discovered_domains").select("id").eq("domain", domain).single().execute()
                if dd.data:
                    try:
                        db.table("domain_competitor_links").upsert({
                            "domain_id": dd.data["id"],
                            "competitor_id": code["competitor_id"],
                            "link_reason": f"shared_{code_type.split('_')[0]}",
                        }, on_conflict="domain_id,competitor_id").execute()
                    except Exception:
                        pass  # link already exists

            except Exception:
                log.exception("Failed to store discovered domain %s", domain)

    log.info("Reverse lookups complete: %d new domains discovered", new_domains)
    return new_domains
