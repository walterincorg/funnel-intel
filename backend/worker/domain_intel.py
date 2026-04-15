"""Extract Google Analytics + Facebook Pixel IDs from competitor homepages.

Fetches the page over HTTP and regex-matches tracking codes. No browser, no LLM.
GA/Pixel are the only signals kept — shared codes across sites = same operator.
"""

from __future__ import annotations
import logging
import re
import requests
from datetime import datetime, timezone
from urllib.parse import urlparse

from backend.db import get_db

log = logging.getLogger(__name__)

GA4_PATTERN = re.compile(r"""gtag\s*\(\s*['"]config['"]\s*,\s*['"]([G]-[A-Z0-9]+)['"]""")
UA_PATTERN = re.compile(r"""(?:ga\s*\(\s*['"]create['"]\s*,\s*['"]|UA-)(\bUA-\d{4,}-\d{1,}\b)""")
GA_GENERIC = re.compile(r"""(G-[A-Z0-9]{6,})""")
FBQ_PATTERN = re.compile(r"""fbq\s*\(\s*['"]init['"]\s*,\s*['"](\d{6,})['"]""")


def extract_tracking_codes(html: str, url: str) -> list[dict]:
    """Return GA + Pixel IDs found in the HTML source."""
    codes: list[dict] = []
    seen: set[str] = set()

    def add(code_type: str, code_id: str, snippet: str) -> None:
        if code_id in seen:
            return
        seen.add(code_id)
        codes.append({"type": code_type, "id": code_id, "snippet": snippet[:200]})

    for match in GA4_PATTERN.finditer(html):
        add("google_analytics", match.group(1), match.group(0))

    for match in UA_PATTERN.finditer(html):
        raw = match.group(1)
        add("google_analytics", raw if raw.startswith("UA-") else f"UA-{raw}", match.group(0))

    for match in GA_GENERIC.finditer(html):
        add("google_analytics", match.group(1), match.group(0))

    for match in FBQ_PATTERN.finditer(html):
        add("facebook_pixel", match.group(1), match.group(0))

    return codes


def extract_fingerprints_http(url: str) -> dict:
    """Fetch the page and extract GA/Pixel codes."""
    result = {"tracking_codes": [], "final_url": url}

    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }, allow_redirects=True)
        resp.raise_for_status()
        result["final_url"] = resp.url
        result["tracking_codes"] = extract_tracking_codes(resp.text, resp.url)
    except Exception:
        log.exception("HTTP extraction failed for %s", url)

    return result


def run_fingerprint_extraction(competitor_id: str, competitor_name: str, url: str) -> dict:
    """Extract GA/Pixel codes for a single competitor. Returns summary stats."""
    db = get_db()
    domain = urlparse(url).netloc or url

    log.info("Extracting fingerprints for %s (%s)", competitor_name, url)

    result = extract_fingerprints_http(url)
    if result["final_url"] != url:
        domain = urlparse(result["final_url"]).netloc or domain

    fingerprints_stored = 0
    for code in result["tracking_codes"]:
        try:
            db.table("domain_fingerprints").upsert({
                "competitor_id": competitor_id,
                "domain": domain,
                "fingerprint_type": code["type"],
                "fingerprint_value": code["id"],
                "detected_at_url": result["final_url"],
                "raw_snippet": code.get("snippet"),
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="competitor_id,fingerprint_type,fingerprint_value").execute()
            fingerprints_stored += 1
        except Exception:
            log.exception("Failed to store fingerprint %s for %s", code["id"], competitor_name)

    log.info("  %s: %d tracking codes", competitor_name, len(result["tracking_codes"]))

    return {
        "competitor_id": competitor_id,
        "tracking_codes": len(result["tracking_codes"]),
        "fingerprints_stored": fingerprints_stored,
    }
