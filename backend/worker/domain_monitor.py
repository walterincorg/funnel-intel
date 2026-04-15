"""Brand-prefixed WHOIS monitoring via WhoisXML Brand Alert API.

Reads `brand_keyword` from each competitor (falls back to first token of the
competitor name). For every keyword we ask WhoisXML for domains registered in
the past 7 days. WhoisXML matches substrings, so we post-filter to keep only
domains whose root label *starts with* the keyword — the equivalent of a
`brand.*` pattern. That eliminates noise like `enliven-living.com` for
keyword `liven`.
"""

from __future__ import annotations
import logging
import os
import re
import requests
from datetime import datetime, timedelta, timezone

from backend.db import get_db

log = logging.getLogger(__name__)

WHOISXML_API_KEY = os.getenv("WHOISXML_API_KEY", "")
WHOISXML_BASE_URL = "https://brand-alert.whoisxmlapi.com/api/v2"


def poll_new_domains() -> int:
    """Query WhoisXML for domains registered in the past 7 days matching
    each competitor's brand keyword. Stores matches in `discovered_domains`.
    Returns the count of rows upserted.
    """
    if not WHOISXML_API_KEY:
        log.info("WhoisXML API key not configured, skipping domain monitoring")
        return 0

    db = get_db()

    competitors = db.table("competitors").select("id, name, brand_keyword").execute().data
    keywords = _collect_keywords(competitors)

    if not keywords:
        return 0

    new_domains = 0
    since_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

    for keyword in keywords:
        try:
            resp = requests.post(
                WHOISXML_BASE_URL,
                json={
                    "apiKey": WHOISXML_API_KEY,
                    "sinceDate": since_date,
                    "mode": "purchase",
                    "punycode": True,
                    "searchType": "domain",
                    "includeSearchTerms": [keyword],
                    "responseFormat": "json",
                },
                timeout=30,
            )

            if resp.status_code == 429:
                log.warning("WhoisXML rate limit hit, stopping monitoring")
                break

            resp.raise_for_status()
            data = resp.json()
        except Exception:
            log.exception("WhoisXML request failed for keyword '%s'", keyword)
            continue

        now_iso = datetime.now(timezone.utc).isoformat()
        rows_by_domain: dict[str, dict] = {}
        for entry in data.get("domainsList", []):
            d = (entry.get("domainName") or "").lower()
            if not d or not _label_starts_with(keyword, d):
                continue
            rows_by_domain[d] = {
                "domain": d,
                "discovery_source": "whois_monitor",
                "discovery_reason": f"brand prefix match: '{keyword}.*'",
                "last_checked_at": now_iso,
            }

        rows = list(rows_by_domain.values())
        for i in range(0, len(rows), 500):
            chunk = rows[i : i + 500]
            try:
                db.table("discovered_domains").upsert(
                    chunk, on_conflict="domain"
                ).execute()
                new_domains += len(chunk)
            except Exception:
                log.exception(
                    "Failed to store monitored domain batch (keyword=%s size=%d)",
                    keyword, len(chunk),
                )

    log.info("Domain monitoring complete: %d new domains from %d keywords", new_domains, len(keywords))
    return new_domains


def _collect_keywords(competitors: list[dict]) -> list[str]:
    """Prefer an explicit `brand_keyword`. Fall back to the first alphanumeric
    token of the competitor name (length >= 4). Deduped, lowercase.
    """
    seen: set[str] = set()
    keywords: list[str] = []
    for c in competitors:
        raw = (c.get("brand_keyword") or "").strip().lower()
        if not raw:
            name = (c.get("name") or "").lower()
            raw = next((t for t in re.findall(r"[a-z0-9]+", name) if len(t) >= 4), "")
        if raw and raw not in seen:
            seen.add(raw)
            keywords.append(raw)
    return keywords


def _label_starts_with(keyword: str, domain: str) -> bool:
    """True if the domain's root label starts with `keyword` followed by end
    of label (dot) or an alphanumeric continuation.

    Accepts `liven.app`, `liven-plus.com`, `livenfitness.io`.
    Rejects `enliven.com`, `myliven.app`, `meal-liven.com`.
    """
    root_label = domain.split(".", 1)[0]
    if not root_label.startswith(keyword):
        return False
    # Accept the whole label or a continuation with alnum / `-` / `_`.
    # We don't require a separator — `livenfitness` is a legitimate brand
    # extension — but the label MUST start with the keyword.
    return True
