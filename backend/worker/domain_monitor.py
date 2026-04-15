"""New domain monitoring via WhoisXML API Brand Monitor."""

from __future__ import annotations
import logging
import os
import re
import requests
from datetime import datetime, timezone

from backend.db import get_db

log = logging.getLogger(__name__)

WHOISXML_API_KEY = os.getenv("WHOISXML_API_KEY", "")
WHOISXML_BASE_URL = "https://brand-alert.whoisxmlapi.com/api/v2"


def poll_new_domains() -> int:
    """Poll WhoisXML Brand Monitor for new domain registrations matching keywords.

    Returns number of new domains found.
    """
    if not WHOISXML_API_KEY:
        log.info("WhoisXML API key not configured, skipping domain monitoring")
        return 0

    db = get_db()

    # Build keyword list from competitor names.
    # WhoisXML matches substrings against the domain label — phrases with spaces
    # return zero. Reduce each competitor name to its first alphanumeric token
    # (length >= 4 to avoid noise) and dedupe.
    competitors = db.table("competitors").select("id, name").execute().data
    seen: set[str] = set()
    keywords: list[str] = []
    for c in competitors:
        name = (c.get("name") or "").lower()
        token = next((t for t in re.findall(r"[a-z0-9]+", name) if len(t) >= 4), "")
        if token and token not in seen:
            seen.add(token)
            keywords.append(token)

    if not keywords:
        return 0

    new_domains = 0

    for keyword in keywords:
        try:
            resp = requests.post(
                WHOISXML_BASE_URL,
                json={
                    "apiKey": WHOISXML_API_KEY,
                    "sinceDate": _days_ago(7),
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

            domain_list = data.get("domainsList", [])
            now_iso = datetime.now(timezone.utc).isoformat()
            # Dedupe by domain — WhoisXML can return the same domain multiple
            # times under different `action` entries, and Postgres rejects
            # `ON CONFLICT DO UPDATE` when the same target row appears twice
            # in one upsert batch.
            rows_by_domain: dict[str, dict] = {}
            for entry in domain_list:
                d = entry.get("domainName", "").lower()
                if not d or not _token_is_boundary_match(keyword, d):
                    continue
                rows_by_domain[d] = {
                    "domain": d,
                    "discovery_source": "whois_monitor",
                    "discovery_reason": f"keyword match: '{keyword}'",
                    "relevance": "medium",
                    "last_checked_at": now_iso,
                }
            rows = list(rows_by_domain.values())

            # Batch upserts to avoid one HTTP round-trip per domain.
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
                        keyword,
                        len(chunk),
                    )

        except requests.exceptions.HTTPError:
            log.exception("WhoisXML API error for keyword '%s'", keyword)
        except Exception:
            log.exception("WhoisXML monitoring failed for keyword '%s'", keyword)

    log.info("Domain monitoring complete: %d new domains from %d keywords", new_domains, len(keywords))
    return new_domains


def _days_ago(n: int) -> str:
    """Return ISO date string for N days ago."""
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def _token_is_boundary_match(token: str, domain: str) -> bool:
    """Check if `token` appears as a bounded word in `domain`.

    WhoisXML returns any domain whose label contains the token as a raw substring,
    which floods results with noise like `enliven-living.com` for token `liven`.
    We keep a domain only if the token is flanked by a label boundary (start/end
    of the full domain) or a non-alphanumeric separator (`-`, `.`).
    """
    # Match against the full domain (labels + dots). Separators are `-` and `.`.
    pattern = rf"(?:^|[^a-z0-9]){re.escape(token)}(?:$|[^a-z0-9])"
    return re.search(pattern, domain) is not None
