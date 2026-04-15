"""Domain intelligence orchestration.

Weekly pipeline with three phases:
  1. Extract GA + Pixel codes from each competitor's homepage.
  2. Cluster competitors sharing a GA or Pixel (same-operator detection).
  3. Poll WhoisXML for new `brand.*` domain registrations (past 7 days).
"""

from __future__ import annotations
import logging
from datetime import date, datetime, timezone

from backend.config import DOMAIN_INTEL_DAY_OF_WEEK, DOMAIN_INTEL_HOUR_UTC
from backend.db import get_db
from backend.worker.domain_intel import run_fingerprint_extraction
from backend.worker.domain_clustering import compute_clusters
from backend.worker.domain_monitor import poll_new_domains
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

    if today.weekday() != DOMAIN_INTEL_DAY_OF_WEEK:
        return

    if now.hour < DOMAIN_INTEL_HOUR_UTC:
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

    total_fingerprints = 0
    competitors_scanned = 0

    try:
        comps = db.table("competitors").select("id, name, funnel_url").execute().data
        if not comps:
            log.info("No competitors to scan")
            db.table("domain_intel_runs").update({
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", run_id).execute()
            return

        # Phase 1: extract GA + Pixel codes
        for comp in comps:
            if not comp.get("funnel_url"):
                continue
            try:
                result = run_fingerprint_extraction(
                    comp["id"], comp["name"], comp["funnel_url"]
                )
                total_fingerprints += result.get("fingerprints_stored", 0)
                competitors_scanned += 1
            except Exception:
                log.exception("Failed to extract fingerprints for %s", comp["name"])

        # Phase 2: cluster operators sharing GA/Pixel
        clusters_found = 0
        try:
            clusters_found = compute_clusters()
        except Exception:
            log.exception("Clustering failed")

        # Phase 3: WHOIS brand-prefix monitoring
        domains_discovered = 0
        try:
            domains_discovered = poll_new_domains()
        except Exception:
            log.exception("Domain monitoring failed")

        db.table("domain_intel_runs").update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "competitors_scanned": competitors_scanned,
            "fingerprints_found": total_fingerprints,
            "clusters_found": clusters_found,
            "domains_discovered": domains_discovered,
        }).eq("id", run_id).execute()

        log.info(
            "Domain intel completed: %d competitors, %d fingerprints, %d clusters, %d domains",
            competitors_scanned, total_fingerprints, clusters_found, domains_discovered,
        )

        send_alert(
            f"Domain Intel complete: {competitors_scanned} competitors scanned, "
            f"{clusters_found} clusters, {domains_discovered} new domains"
        )

    except Exception as e:
        log.exception("Domain intel run failed")
        db.table("domain_intel_runs").update({
            "status": "failed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(e)[:500],
        }).eq("id", run_id).execute()
        send_alert(f"Domain intel run failed: {e}")
