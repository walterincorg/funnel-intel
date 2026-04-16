"""Brand keyword WHOIS monitoring via WhoisXML Brand Alert API.

Reads `brand_keyword` from each competitor (falls back to first token of the
competitor name). For every keyword we ask WhoisXML for domains registered in
the past 7 days. We keep any domain whose root label *contains* the keyword
(e.g. `getbioma.com` matches keyword `bioma`). Keywords shorter than 5 chars
are skipped to avoid noise like 'meal' matching everything.
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
            if not d or not _label_contains(keyword, d):
                continue
            rows_by_domain[d] = {
                "domain": d,
                "discovery_source": "whois_monitor",
                "discovery_reason": f"brand keyword match: '{keyword}' in '{d}'",
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


MIN_KEYWORD_LENGTH = 5  # "meal", "rise", "wall" are too generic

def _collect_keywords(competitors: list[dict]) -> list[str]:
    """Prefer an explicit `brand_keyword`. Fall back to the first alphanumeric
    token of the competitor name (length >= MIN_KEYWORD_LENGTH). Deduped, lowercase.

    Explicit brand_keywords bypass the length check — if you manually set
    a 4-char keyword you presumably know what you're doing.
    """
    seen: set[str] = set()
    keywords: list[str] = []
    for c in competitors:
        explicit = (c.get("brand_keyword") or "").strip().lower()
        if explicit:
            raw = explicit
        else:
            name = (c.get("name") or "").lower()
            raw = next(
                (t for t in re.findall(r"[a-z0-9]+", name) if len(t) >= MIN_KEYWORD_LENGTH),
                "",
            )
        if raw and raw not in seen:
            seen.add(raw)
            keywords.append(raw)
    return keywords


def _label_contains(keyword: str, domain: str) -> bool:
    """True if the keyword appears anywhere in the domain's root label.

    Accepts `liven.app`, `getliven.com`, `livenfitness.io`, `my-liven.app`.
    Rejects only domains where the keyword doesn't appear at all.
    """
    root_label = domain.split(".", 1)[0].lower()
    return keyword.lower() in root_label
