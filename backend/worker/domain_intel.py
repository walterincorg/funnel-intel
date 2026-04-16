"""Extract Google Analytics + Facebook Pixel IDs from competitor homepages.

Two-pass extraction:
  1. Fetch the page HTML and regex-match inline tracking codes.
  2. If a GTM container is found, fetch the container JS and extract
     GA/Pixel IDs from inside it (most modern sites configure tracking
     through GTM, so codes never appear in the page HTML directly).

No browser, no LLM. GA/Pixel are the only signals kept — shared codes
across sites = same operator.
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
GA_SCRIPT_SRC = re.compile(r"""googletagmanager\.com/gtag/js\?id=(G-[A-Z0-9]+)""")
FBQ_PATTERN = re.compile(r"""fbq\s*\(\s*['"]init['"]\s*,\s*['"](\d{6,})['"]""")
FB_NOSCRIPT = re.compile(r"""facebook\.com/tr\?id=(\d{6,})""")

# GTM container detection — extract the container ID so we can fetch the JS
GTM_CONTAINER = re.compile(r"""googletagmanager\.com/gtm\.js\?id=(GTM-[A-Z0-9]+)""")


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

    # Script src: <script src="...googletagmanager.com/gtag/js?id=G-XXXXX">
    for match in GA_SCRIPT_SRC.finditer(html):
        add("google_analytics", match.group(1), match.group(0))

    for match in GA_GENERIC.finditer(html):
        add("google_analytics", match.group(1), match.group(0))

    for match in FBQ_PATTERN.finditer(html):
        add("facebook_pixel", match.group(1), match.group(0))

    # Noscript fallback pixel: <img src="...facebook.com/tr?id=PIXEL_ID...">
    for match in FB_NOSCRIPT.finditer(html):
        add("facebook_pixel", match.group(1), match.group(0))

    return codes


def _fetch_gtm_codes(html: str) -> list[dict]:
    """Find GTM container IDs in the HTML, fetch each container's JS,
    and extract GA/Pixel codes embedded inside.

    Most modern sites configure GA and Facebook Pixel through GTM. The
    tracking codes never appear in the page HTML — they're compiled into
    the GTM container JavaScript at `googletagmanager.com/gtm.js?id=GTM-XXX`.
    """
    gtm_ids = GTM_CONTAINER.findall(html)
    if not gtm_ids:
        return []

    codes: list[dict] = []
    for gtm_id in set(gtm_ids):
        try:
            resp = requests.get(
                f"https://www.googletagmanager.com/gtm.js?id={gtm_id}",
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            container_js = resp.text

            # GA IDs inside the container JS
            for m in GA_GENERIC.finditer(container_js):
                codes.append({"type": "google_analytics", "id": m.group(1),
                              "snippet": f"via {gtm_id}: {m.group(0)[:150]}"})

            for m in UA_PATTERN.finditer(container_js):
                raw = m.group(1)
                ga_id = raw if raw.startswith("UA-") else f"UA-{raw}"
                codes.append({"type": "google_analytics", "id": ga_id,
                              "snippet": f"via {gtm_id}: {m.group(0)[:150]}"})

            # Facebook Pixel IDs inside the container JS
            for m in FBQ_PATTERN.finditer(container_js):
                codes.append({"type": "facebook_pixel", "id": m.group(1),
                              "snippet": f"via {gtm_id}: {m.group(0)[:150]}"})

            # Also catch pixel IDs in the common GTM format:
            # "https://www.facebook.com/tr?id=PIXEL_ID..."
            for m in FB_NOSCRIPT.finditer(container_js):
                codes.append({"type": "facebook_pixel", "id": m.group(1),
                              "snippet": f"via {gtm_id}: {m.group(0)[:150]}"})

        except Exception:
            log.debug("Failed to fetch GTM container %s", gtm_id)

    return codes


def extract_fingerprints_http(url: str) -> dict:
    """Fetch the page and extract GA/Pixel codes.

    Two passes: (1) regex the page HTML directly, (2) if GTM containers
    are found, fetch each container JS and extract codes from inside.
    """
    result = {"tracking_codes": [], "final_url": url}

    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }, allow_redirects=True)
        resp.raise_for_status()
        result["final_url"] = resp.url
        html = resp.text

        # Pass 1: inline codes in page HTML
        codes = extract_tracking_codes(html, resp.url)

        # Pass 2: codes inside GTM containers
        gtm_codes = _fetch_gtm_codes(html)
        seen = {c["id"] for c in codes}
        for gc in gtm_codes:
            if gc["id"] not in seen:
                seen.add(gc["id"])
                codes.append(gc)

        result["tracking_codes"] = codes
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
