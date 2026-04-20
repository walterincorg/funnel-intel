"""Worker polling loop — picks scan jobs and runs traversals."""

from __future__ import annotations
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from backend.db import get_db
from backend.worker.traversal import run_traversal_sync
from backend.worker.differ import diff_runs
from backend.worker.alerts import send_alert
from backend.worker.ad_loop import maybe_run_ad_scrape
from backend.worker.domain_intel_loop import maybe_run_domain_intel

log = logging.getLogger(__name__)

POLL_INTERVAL = 10  # seconds
_shutdown = False

# Worker identity for multi-instance deployments. Instance "1" is the primary —
# it runs cleanup on startup and the ad/domain background loops. Other instances
# only process scan jobs.
WORKER_ID = os.getenv("WORKER_ID", "1")
IS_PRIMARY = WORKER_ID == "1"

# Scans can take a long time (funnel crawls); ad scrapes are fast.
STALE_AGE_SCANS = timedelta(minutes=45)
STALE_AGE_AD_SCRAPES = timedelta(minutes=15)


def cleanup_stale_jobs():
    """Mark picked/running rows older than their stale threshold as failed.

    Only the primary worker (WORKER_ID=1) runs this, and the age thresholds
    mean scans still executing on sibling workers are never touched.
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    scan_cutoff = (now - STALE_AGE_SCANS).isoformat()
    scrape_cutoff = (now - STALE_AGE_AD_SCRAPES).isoformat()
    now_iso = now.isoformat()

    stale_runs = db.table("scan_runs").update({
        "status": "failed",
        "completed_at": now_iso,
        "summary": {"error": "Worker restarted — scan was interrupted"},
    }).eq("status", "running").lt("started_at", scan_cutoff).execute()

    stale_jobs = db.table("scan_jobs").update({
        "status": "failed",
    }).eq("status", "picked").lt("picked_at", scan_cutoff).execute()

    stale_scrapes = db.table("ad_scrape_runs").update({
        "status": "failed",
        "completed_at": now_iso,
        "error": "Worker restarted — scrape was interrupted",
    }).in_("status", ["running", "pending"]).lt("started_at", scrape_cutoff).execute()

    n_runs = len(stale_runs.data) if stale_runs.data else 0
    n_jobs = len(stale_jobs.data) if stale_jobs.data else 0
    n_scrapes = len(stale_scrapes.data) if stale_scrapes.data else 0
    if n_runs or n_jobs or n_scrapes:
        log.warning("Cleaned up %d stale runs, %d stale jobs, %d stale ad scrapes from previous worker", n_runs, n_jobs, n_scrapes)


def pick_job() -> dict | None:
    """Claim the next pending job. Returns the job row or None."""
    db = get_db()
    # Fetch oldest pending job
    res = (
        db.table("scan_jobs")
        .select("*")
        .eq("status", "pending")
        .order("priority", desc=True)
        .order("created_at")
        .limit(1)
        .execute()
    )
    if not res.data:
        return None

    job = res.data[0]
    # Atomically claim it — only succeeds if still pending
    claim = db.table("scan_jobs").update({
        "status": "picked",
        "picked_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", job["id"]).eq("status", "pending").execute()

    # If another worker already claimed it, this update returns no rows
    if not claim.data:
        return None

    return job


def get_baseline(competitor_id: str) -> tuple[dict | None, list[dict]]:
    """Get the baseline run and its steps for a competitor."""
    db = get_db()
    res = (
        db.table("scan_runs")
        .select("*")
        .eq("competitor_id", competitor_id)
        .eq("is_baseline", True)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None, []

    run = res.data[0]
    steps = (
        db.table("scan_steps")
        .select("*")
        .eq("run_id", run["id"])
        .order("step_number")
        .execute()
        .data
    )
    return run, steps


def process_job(job: dict):
    job_start = time.perf_counter()
    db = get_db()
    competitor_id = job["competitor_id"]

    # Fetch competitor
    comp = db.table("competitors").select("*").eq("id", competitor_id).single().execute().data
    if not comp:
        log.error("Competitor %s not found, skipping job %s", competitor_id, job["id"],
                  extra={"competitor_id": competitor_id, "job_id": job["id"]})
        db.table("scan_jobs").update({"status": "failed"}).eq("id", job["id"]).execute()
        return

    # Create scan run
    now = datetime.now(timezone.utc).isoformat()
    run = db.table("scan_runs").insert({
        "competitor_id": competitor_id,
        "status": "running",
        "started_at": now,
    }).execute().data[0]

    run_id = run["id"]
    log.info("Starting scan %s for %s (job=%s)", run_id, comp["name"], job["id"],
             extra={"run_id": run_id, "competitor_id": competitor_id, "job_id": job["id"]})

    # Baseline is still used for diffing, but no longer drives traversal —
    # Stagehand's recipe/replay system replaces the old guided-prompt flow.
    baseline_run, baseline_steps = get_baseline(competitor_id)

    # Progress callback — appends log entries to the DB in real-time
    _progress_log_buffer = []

    def _on_progress(entry: dict):
        _progress_log_buffer.append(entry)
        try:
            db.table("scan_runs").update({
                "progress_log": _progress_log_buffer,
            }).eq("id", run_id).execute()
        except Exception:
            log.debug("Failed to flush progress log for %s", run_id)

    try:
        result = run_traversal_sync(
            competitor_name=comp["name"],
            funnel_url=comp["funnel_url"],
            config=comp.get("config"),
            on_progress=_on_progress,
            competitor_slug=comp.get("slug"),
            competitor_id=competitor_id,
            run_id=run_id,
        )

        # Deduplicate steps — keep last occurrence per step_number (most complete)
        deduped_steps: dict[int, dict] = {}
        for step in result["steps"]:
            num = step.get("step_number", 0)
            deduped_steps[num] = step
        for step in deduped_steps.values():
            db.table("scan_steps").insert({
                "run_id": run_id,
                "step_number": step.get("step_number", 0),
                "step_type": step.get("step_type", "unknown"),
                "question_text": step.get("question_text"),
                "answer_options": step.get("answer_options"),
                "action_taken": step.get("action_taken"),
                "url": step.get("url"),
                "metadata": {k: v for k, v in step.items()
                             if k not in ("step_number", "step_type", "question_text",
                                          "answer_options", "action_taken", "url", "log")},
            }).execute()

        progress_log = _progress_log_buffer

        # Store pricing if captured (skip empty snapshots where agent tagged
        # a page as pricing but extracted no actual plan/discount/trial data)
        pricing = result["pricing"]
        if pricing and any(pricing.get(k) for k in ("plans", "discounts", "trial_info")):
            db.table("pricing_snapshots").insert({
                "run_id": run_id,
                "competitor_id": competitor_id,
                "plans": pricing.get("plans"),
                "discounts": pricing.get("discounts"),
                "trial_info": pricing.get("trial_info"),
                "captured_at_step": pricing.get("step_number"),
                "url": pricing.get("url"),
            }).execute()

        # Update run as completed
        summary = result["summary"]
        update_data = {
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "total_steps": summary.get("total_steps", len(result["steps"])),
            "stop_reason": summary.get("stop_reason"),
            "summary": summary,
            "progress_log": progress_log,
        }

        # If no baseline exists, this becomes the baseline — but only if it
        # actually captured a meaningful number of steps. A 0- or 1-step run
        # (funnel_reset, early paywall, broken capture) shouldn't become the
        # poison baseline for every future diff.
        captured_steps = len(result["steps"])
        if not baseline_run and captured_steps >= 3:
            update_data["is_baseline"] = True
            log.info("First successful run for %s — marking as baseline (%d steps)", comp["name"], captured_steps)
        elif not baseline_run:
            log.warning("First run for %s captured only %d steps — NOT promoting to baseline", comp["name"], captured_steps)

        # Diff against baseline if one exists
        if baseline_run and baseline_steps:
            new_steps = result["steps"]
            baseline_pricing_res = db.table("pricing_snapshots").select("*").eq("run_id", baseline_run["id"]).limit(1).execute()
            baseline_pricing = baseline_pricing_res.data[0] if baseline_pricing_res.data else None
            new_pricing = result["pricing"]

            diff = diff_runs(baseline_steps, new_steps, baseline_pricing, new_pricing)
            update_data["drift_level"] = diff.drift_level
            update_data["drift_details"] = [
                {"severity": c.severity, "category": c.category,
                 "step_number": c.step_number, "description": c.description}
                for c in diff.changes
            ]
            if diff.summary:
                s = update_data.get("summary") or {}
                s["drift_summary"] = diff.summary
                update_data["summary"] = s

            # Alert only on LLM-identified important changes (pricing or genuinely new questions)
            if diff.alert_worthy_changes:
                alert_lines = [f"🔔 {comp['name']} — funnel changes detected:"]
                for desc in diff.alert_worthy_changes:
                    alert_lines.append(f"  🟠 {desc}")
                send_alert("\n".join(alert_lines))

        db.table("scan_runs").update(update_data).eq("id", run_id).execute()
        db.table("scan_jobs").update({"status": "done"}).eq("id", job["id"]).execute()
        duration_ms = (time.perf_counter() - job_start) * 1000
        log.info("Scan %s completed: %d steps in %.1fs (drift=%s)",
                 run_id, len(result["steps"]), duration_ms / 1000,
                 update_data.get("drift_level", "n/a"),
                 extra={"run_id": run_id, "competitor_id": competitor_id,
                        "step_count": len(result["steps"]), "duration_ms": round(duration_ms)})

    except Exception as e:
        duration_ms = (time.perf_counter() - job_start) * 1000
        log.exception("Scan %s failed after %.1fs", run_id, duration_ms / 1000,
                      extra={"run_id": run_id, "competitor_id": competitor_id,
                             "duration_ms": round(duration_ms)})
        db.table("scan_runs").update({
            "status": "failed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "summary": {"error": str(e)},
        }).eq("id", run_id).execute()
        db.table("scan_jobs").update({"status": "failed"}).eq("id", job["id"]).execute()
        send_alert(f"❌ {comp['name']}: scan failed — {e}")


def has_pending_scan_job() -> bool:
    """Quick check — used to interrupt ad scrape if a scan job is waiting."""
    res = get_db().table("scan_jobs").select("id").eq("status", "pending").limit(1).execute()
    return bool(res.data)


def main():
    log.info("Worker %s started, polling every %ds (primary=%s)", WORKER_ID, POLL_INTERVAL, IS_PRIMARY,
             extra={"worker_id": WORKER_ID})
    if IS_PRIMARY:
        cleanup_stale_jobs()
    while not _shutdown:
        job = pick_job()
        if job:
            process_job(job)
        else:
            # Background ad/domain loops only run on the primary worker to
            # avoid triple-firing when multiple workers are deployed.
            if IS_PRIMARY and not has_pending_scan_job():
                try:
                    maybe_run_ad_scrape()
                except Exception:
                    log.exception("Ad scrape check failed")
                try:
                    maybe_run_domain_intel()
                except Exception:
                    log.exception("Domain intel check failed")
            time.sleep(POLL_INTERVAL)
    log.info("Worker stopped")


if __name__ == "__main__":
    main()
