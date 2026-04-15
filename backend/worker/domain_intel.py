"""Domain intelligence — extract infrastructure fingerprints from competitor websites.

Primary: browser-use agent with specialized prompt.
Fallback: direct HTTP GET + regex extraction.
"""

from __future__ import annotations
import logging
import re
import requests
from datetime import datetime, timezone
from urllib.parse import urlparse

from backend.db import get_db

log = logging.getLogger(__name__)

# --- Regex patterns for tracking code extraction ---

GA4_PATTERN = re.compile(r"""gtag\s*\(\s*['"]config['"]\s*,\s*['"]([G]-[A-Z0-9]+)['"]""")
UA_PATTERN = re.compile(r"""(?:ga\s*\(\s*['"]create['"]\s*,\s*['"]|UA-)(\bUA-\d{4,}-\d{1,}\b)""")
GA_GENERIC = re.compile(r"""(G-[A-Z0-9]{6,})""")
FBQ_PATTERN = re.compile(r"""fbq\s*\(\s*['"]init['"]\s*,\s*['"](\d{6,})['"]""")
GTM_PATTERN = re.compile(r"""(GTM-[A-Z0-9]{4,})""")

# Tech stack detection patterns
SHOPIFY_PATTERNS = [
    re.compile(r'cdn\.shopify\.com'),
    re.compile(r'window\.Shopify'),
    re.compile(r'Shopify\.theme'),
]
WORDPRESS_PATTERNS = [
    re.compile(r'/wp-content/'),
    re.compile(r'/wp-includes/'),
    re.compile(r'wp-json'),
]
NEXTJS_PATTERNS = [
    re.compile(r'__NEXT_DATA__'),
    re.compile(r'/_next/'),
]


def extract_tracking_codes(html: str, url: str) -> list[dict]:
    """Extract tracking codes from HTML page source using regex."""
    codes: list[dict] = []
    seen: set[str] = set()

    for match in GA4_PATTERN.finditer(html):
        code_id = match.group(1)
        if code_id not in seen:
            codes.append({"type": "google_analytics", "id": code_id, "snippet": match.group(0)[:200]})
            seen.add(code_id)

    for match in UA_PATTERN.finditer(html):
        code_id = match.group(1) if match.group(1).startswith("UA-") else f"UA-{match.group(1)}"
        if code_id not in seen:
            codes.append({"type": "google_analytics", "id": code_id, "snippet": match.group(0)[:200]})
            seen.add(code_id)

    # Catch G- IDs not found by gtag config pattern
    for match in GA_GENERIC.finditer(html):
        code_id = match.group(1)
        if code_id not in seen:
            codes.append({"type": "google_analytics", "id": code_id, "snippet": match.group(0)[:200]})
            seen.add(code_id)

    for match in FBQ_PATTERN.finditer(html):
        code_id = match.group(1)
        if code_id not in seen:
            codes.append({"type": "facebook_pixel", "id": code_id, "snippet": match.group(0)[:200]})
            seen.add(code_id)

    for match in GTM_PATTERN.finditer(html):
        code_id = match.group(1)
        if code_id not in seen:
            codes.append({"type": "gtm", "id": code_id, "snippet": match.group(0)[:200]})
            seen.add(code_id)

    return codes


def detect_tech_stack(html: str) -> str:
    """Detect tech stack from HTML. Returns: 'shopify', 'wordpress', 'nextjs', or 'custom'."""
    if any(p.search(html) for p in SHOPIFY_PATTERNS):
        return "shopify"
    if any(p.search(html) for p in WORDPRESS_PATTERNS):
        return "wordpress"
    if any(p.search(html) for p in NEXTJS_PATTERNS):
        return "nextjs"
    return "custom"


def detect_hosting(headers: dict[str, str]) -> str | None:
    """Detect hosting provider from HTTP response headers."""
    server = headers.get("server", "").lower()
    via = headers.get("via", "").lower()
    powered_by = headers.get("x-powered-by", "").lower()

    if "cloudflare" in server or "cloudflare" in via:
        return "Cloudflare"
    if "shopify" in server or "shopify" in powered_by:
        return "Shopify"
    if "vercel" in server or "vercel" in via:
        return "Vercel"
    if "netlify" in server:
        return "Netlify"
    if "awselb" in server or "amazons3" in server or "cloudfront" in via:
        return "AWS"
    if "gws" in server or "gse" in server:
        return "GCP"
    if server:
        return server[:50]
    return None


def extract_fingerprints_http(url: str) -> dict:
    """Extract fingerprints via direct HTTP GET + regex. No browser needed."""
    result = {"tracking_codes": [], "tech_stack": "custom", "hosting": None, "final_url": url}

    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }, allow_redirects=True)
        resp.raise_for_status()

        html = resp.text
        result["final_url"] = resp.url
        result["tracking_codes"] = extract_tracking_codes(html, resp.url)
        result["tech_stack"] = detect_tech_stack(html)
        result["hosting"] = detect_hosting(dict(resp.headers))

    except Exception:
        log.exception("HTTP extraction failed for %s", url)

    return result


def run_fingerprint_extraction(competitor_id: str, competitor_name: str, url: str) -> dict:
    """Extract fingerprints for a single competitor. Returns summary stats."""
    db = get_db()
    domain = urlparse(url).netloc or url

    log.info("Extracting fingerprints for %s (%s)", competitor_name, url)

    # Use HTTP extraction (reliable, no LLM cost)
    result = extract_fingerprints_http(url)

    # If redirect, use final domain
    if result["final_url"] != url:
        domain = urlparse(result["final_url"]).netloc or domain

    fingerprints_stored = 0

    # Store tracking codes
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

    # Store tech stack
    if result["tech_stack"]:
        try:
            db.table("domain_fingerprints").upsert({
                "competitor_id": competitor_id,
                "domain": domain,
                "fingerprint_type": "tech_stack",
                "fingerprint_value": result["tech_stack"],
                "detected_at_url": result["final_url"],
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="competitor_id,fingerprint_type,fingerprint_value").execute()
            fingerprints_stored += 1
        except Exception:
            log.exception("Failed to store tech_stack for %s", competitor_name)

    # Store hosting
    if result["hosting"]:
        try:
            db.table("domain_fingerprints").upsert({
                "competitor_id": competitor_id,
                "domain": domain,
                "fingerprint_type": "hosting",
                "fingerprint_value": result["hosting"],
                "detected_at_url": result["final_url"],
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="competitor_id,fingerprint_type,fingerprint_value").execute()
            fingerprints_stored += 1
        except Exception:
            log.exception("Failed to store hosting for %s", competitor_name)

    log.info("  %s: %d tracking codes, tech=%s, hosting=%s",
             competitor_name, len(result["tracking_codes"]),
             result["tech_stack"], result["hosting"])

    return {
        "competitor_id": competitor_id,
        "tracking_codes": len(result["tracking_codes"]),
        "tech_stack": result["tech_stack"],
        "hosting": result["hosting"],
        "fingerprints_stored": fingerprints_stored,
    }
