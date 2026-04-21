"""Domain intelligence orchestration.

Scrapes BuiltWith relationship data for each competitor's domain.
"""

from __future__ import annotations
import logging
import time
from datetime import date, datetime, timezone

from urllib.parse import urlparse

from backend.db import get_db
from backend.settings import get_settings
from backend.worker.builtwith_scraper import scrape_relationships
from backend.worker.alerts import send_alert

log = logging.getLogger(__name__)


def maybe_run_domain_intel():
    """Check if a domain intel run is due and execute if so."""
    db = get_db()
    now = datetime.now(timezone.utc)
    today = now.date()

    pending = (
        db.table("domain_intel_runs")
        .select("id")
        .eq("status", "pending")
        .limit(1)
        .execute()
    )
    if pending.data:
        log.info("Found manually triggered domain intel run, starting now")
        _run_domain_intel(today)
        return

    settings = get_settings()
    if not settings.get("domain_intel_enabled", True):
        return

    if today.weekday() != settings.get("domain_intel_day_of_week", 1):
        return

    if now.hour < settings.get("domain_intel_hour_utc", 7):
        return

    existing = (
        db.table("domain_intel_runs")
        .select("id, status")
        .gte("created_at", today.isoformat())
        .in_("status", ["running", "completed"])
        .limit(1)
        .execute()
    )
    if existing.data:
        return

    failed_today = (
        db.table("domain_intel_runs")
        .select("id")
        .gte("created_at", today.isoformat())
        .eq("status", "failed")
        .execute()
    )
    if len(failed_today.data) >= 3:
        return

    log.info("Starting weekly domain intel run for %s", today)
    _run_domain_intel(today)


def _run_domain_intel(today: date):
    """Execute the domain intelligence pipeline."""
    pipeline_start = time.perf_counter()
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    pending = (
        db.table("domain_intel_runs")
        .select("id")
        .eq("status", "pending")
        .limit(1)
        .execute()
    )
    if pending.data:
        run_id = pending.data[0]["id"]
        db.table("domain_intel_runs").update({
            "status": "running",
            "started_at": now,
        }).eq("id", run_id).execute()
    else:
        run = db.table("domain_intel_runs").insert({
            "status": "running",
            "started_at": now,
        }).execute().data[0]
        run_id = run["id"]

    try:
        comps = db.table("competitors").select("id, name, funnel_url").execute().data
        if not comps:
            log.info("No competitors to scan")
            db.table("domain_intel_runs").update({
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", run_id).execute()
            return

        relationships_scraped = 0
        bw_competitors = [c for c in comps if c.get("funnel_url")]
        log.info("BuiltWith scraping: %d competitors to process", len(bw_competitors))
        for i, comp in enumerate(bw_competitors, 1):
            domain = urlparse(comp["funnel_url"]).netloc
            log.info("BuiltWith [%d/%d] scraping %s (%s)", i, len(bw_competitors), comp["name"], domain)
            comp_start = time.perf_counter()
            try:
                rows = scrape_relationships(domain)
                scrape_elapsed = time.perf_counter() - comp_start
                log.info("BuiltWith [%d/%d] %s -> %d rows (scraped in %.1fs)",
                         i, len(bw_competitors), domain, len(rows), scrape_elapsed,
                         extra={"duration_ms": round(scrape_elapsed * 1000), "competitor_id": comp["id"]})
                if rows:
                    log.debug("BuiltWith [%d/%d] %s rows preview: %s", i, len(bw_competitors), domain,
                              [r.get("domain") for r in rows[:3]])
                upsert_start = time.perf_counter()
                for row in rows:
                    db.table("builtwith_relationships").upsert({
                        "competitor_id": comp["id"],
                        "source_domain": domain,
                        "related_domain": row["domain"],
                        "attribute_value": row["attributeValue"],
                        "first_detected": row["firstDetected"],
                        "last_detected": row["lastDetected"],
                        "overlap_duration": row["overlapDuration"],
                        "scraped_at": datetime.now(timezone.utc).isoformat(),
                    }, on_conflict="competitor_id,related_domain,attribute_value").execute()
                if rows:
                    upsert_elapsed = time.perf_counter() - upsert_start
                    log.debug("BuiltWith [%d/%d] %s upserted %d rows in %.1fs",
                              i, len(bw_competitors), domain, len(rows), upsert_elapsed)
                relationships_scraped += len(rows)
                time.sleep(2.5)
            except Exception:
                scrape_elapsed = time.perf_counter() - comp_start
                log.exception("BuiltWith [%d/%d] scrape failed for %s after %.1fs",
                              i, len(bw_competitors), domain, scrape_elapsed,
                              extra={"competitor_id": comp["id"]})
        log.info("BuiltWith scraping complete: %d total rows across %d competitors", relationships_scraped, len(bw_competitors))

        bw_competitors_count = len(bw_competitors)
        db.table("domain_intel_runs").update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "competitors_scanned": bw_competitors_count,
        }).eq("id", run_id).execute()

        duration_ms = (time.perf_counter() - pipeline_start) * 1000
        log.info(
            "Domain intel completed: %d competitors, %d bw-relationships (%.1fs)",
            bw_competitors_count, relationships_scraped, duration_ms / 1000,
            extra={"duration_ms": round(duration_ms)},
        )

        send_alert(
            f"Domain Intel complete: {bw_competitors_count} competitors scanned, "
            f"{relationships_scraped} BuiltWith relationships"
        )

    except Exception as e:
        log.exception("Domain intel run failed")
        db.table("domain_intel_runs").update({
            "status": "failed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(e)[:500],
        }).eq("id", run_id).execute()
        send_alert(f"Domain intel run failed: {e}")
