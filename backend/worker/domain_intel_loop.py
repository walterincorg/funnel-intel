"""Domain intelligence orchestration.

Called from the main worker loop. Checks whether a weekly extraction is due,
then runs the full pipeline: extract fingerprints -> cluster -> reverse lookup -> monitor.
"""

from __future__ import annotations
import logging
from datetime import date, datetime, timezone

from backend.config import DOMAIN_INTEL_DAY_OF_WEEK, DOMAIN_INTEL_HOUR_UTC
from backend.db import get_db
from backend.worker.domain_intel import run_fingerprint_extraction
from backend.worker.domain_clustering import compute_clusters
from backend.worker.domain_reverse_lookup import run_reverse_lookups
from backend.worker.domain_monitor import poll_new_domains
from backend.worker.domain_changes import detect_changes, cleanup_old_changes
from backend.worker.alerts import send_alert
from backend.worker import freshness

log = logging.getLogger(__name__)


def maybe_run_domain_intel():
    """Check if a domain intel run is due and execute if so."""
    db = get_db()
    now = datetime.now(timezone.utc)
    today = now.date()

    # Check for manually triggered pending runs
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

    # Check day-of-week schedule (default: Tuesday)
    if today.weekday() != DOMAIN_INTEL_DAY_OF_WEEK:
        return

    # Check hour
    if now.hour < DOMAIN_INTEL_HOUR_UTC:
        return

    # Check if already ran today
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

    # Stop retrying after 3 failures today
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
    """Execute the full domain intelligence pipeline."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Claim pending row or create new running row
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
        # Get all competitors
        comps = db.table("competitors").select("id, name, funnel_url").execute().data
        if not comps:
            log.info("No competitors to scan")
            db.table("domain_intel_runs").update({
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", run_id).execute()
            return

        # Phase 1: Extract fingerprints for each competitor
        for comp in comps:
            if not comp.get("funnel_url"):
                continue

            try:
                result = run_fingerprint_extraction(
                    comp["id"], comp["name"], comp["funnel_url"]
                )
                total_fingerprints += result.get("fingerprints_stored", 0)
                competitors_scanned += 1

                # Detect changes for this competitor
                detect_changes(comp["id"], comp["name"])

                freshness.mark_success(freshness.SOURCE_DOMAIN_INTEL, comp["id"])
            except Exception as e:
                log.exception("Failed to extract fingerprints for %s", comp["name"])
                freshness.mark_failure(freshness.SOURCE_DOMAIN_INTEL, comp["id"], str(e))

        # Phase 2: Compute operator clusters
        clusters_found = 0
        try:
            clusters_found = compute_clusters()
        except Exception:
            log.exception("Clustering failed")

        # Phase 3: Run reverse lookups
        domains_discovered = 0
        try:
            domains_discovered = run_reverse_lookups()
        except Exception:
            log.exception("Reverse lookups failed")

        # Phase 4: Poll for new domains
        try:
            domains_discovered += poll_new_domains()
        except Exception:
            log.exception("Domain monitoring failed")

        # Monthly cleanup
        if today.day == 1:
            cleanup_old_changes()

        # Update run record
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
