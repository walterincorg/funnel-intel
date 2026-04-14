"""New domain monitoring via WhoisXML API Brand Monitor."""

from __future__ import annotations
import logging
import os
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

    # Build keyword list from competitor names
    competitors = db.table("competitors").select("id, name").execute().data
    keywords = [c["name"].lower() for c in competitors if c.get("name")]

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
                    "basicSearchTerms": {
                        "include": [keyword],
                    },
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
            for entry in domain_list:
                domain = entry.get("domainName", "").lower()
                if not domain:
                    continue

                try:
                    db.table("discovered_domains").upsert({
                        "domain": domain,
                        "discovery_source": "whois_monitor",
                        "discovery_reason": f"keyword match: '{keyword}'",
                        "relevance": "medium",
                        "last_checked_at": datetime.now(timezone.utc).isoformat(),
                    }, on_conflict="domain").execute()
                    new_domains += 1
                except Exception:
                    log.exception("Failed to store monitored domain %s", domain)

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
