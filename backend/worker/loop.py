"""Worker polling loop — picks scan jobs and runs traversals."""

from __future__ import annotations
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from backend.db import get_db
from backend.config import DEFAULT_TRAVERSAL_MODEL, SUPABASE_STORAGE_BUCKET
from backend.worker.traversal import run_traversal_sync
from backend.worker.differ import diff_runs
from backend.worker.alerts import send_alert
from backend.worker.ad_loop import maybe_run_ad_scrape
from backend.worker.domain_intel_loop import maybe_run_domain_intel
from backend.worker.pricing_extractor import (
    extract_from_path,
    vision_to_legacy,
    PRICING_EXTRACTOR_VERSION,
)

log = logging.getLogger(__name__)
_DEBUG_LOG_PATH = Path("/Users/lukaspostulka/local browser use setup/.cursor/debug-8d43ee.log")


def _dbg(hypothesis_id: str, location: str, message: str, data: dict, run_id: str = "pre-run") -> None:
    payload = {
        "sessionId": "8d43ee",
        "id": f"log_{int(time.time() * 1000)}_{uuid4().hex[:8]}",
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except Exception:
        pass

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


def _upload_scan_artifacts(db, run_id: str, items: list[dict]) -> None:
    """Upload local screenshots and replace paths with storage object paths."""
    for item in items:
        local_path = item.get("screenshot_path")
        if not local_path or str(local_path).startswith("scan-screenshots/"):
            continue
        try:
            path = Path(local_path)
            if not path.is_file():
                continue
            object_path = f"scan-screenshots/{run_id}/{path.name}"
            db.storage.from_(SUPABASE_STORAGE_BUCKET).upload(
                object_path,
                path.read_bytes(),
                file_options={"content-type": "image/png", "x-upsert": "true"},
            )
            item["screenshot_path"] = object_path
        except Exception:
            log.warning("Failed to upload screenshot %s", local_path, exc_info=True)


def _refine_pricing_with_vision(
    pricing: dict | None,
    competitor_name: str,
    run_id: str,
) -> None:
    """Re-extract pricing from the local screenshot via the vision pipeline.

    Mutates ``pricing`` in place: stashes the rich vision payload under
    ``metadata['vision']`` and overwrites ``plans``/``discounts``/``trial_info``
    with the legacy projection so the existing diff pipeline keeps working.

    Falls back gracefully when the screenshot path is missing or the API call
    raises — we never want vision extraction failures to break a scan.
    """
    if not pricing:
        return
    shot = pricing.get("screenshot_path")
    if not shot:
        log.info(
            "[vision-pricing] No screenshot for run %s; keeping freeform extraction",
            run_id,
        )
        return
    if str(shot).startswith("scan-screenshots/"):
        log.debug(
            "[vision-pricing] Screenshot already uploaded; skipping vision pass for %s",
            run_id,
        )
        return
    try:
        log.info("[vision-pricing] Re-extracting pricing for %s", competitor_name)
        vision = extract_from_path(
            shot,
            competitor_name=competitor_name,
            url=pricing.get("url"),
            visible_text=pricing.get("raw_text"),
        )
    except Exception:
        log.exception(
            "[vision-pricing] Vision extraction failed for %s — keeping freeform data",
            competitor_name,
        )
        return

    legacy = vision_to_legacy(vision)
    plans = legacy.get("plans") or []
    if not plans and not legacy.get("discounts") and not (legacy.get("trial_info") or {}).get("has_trial"):
        log.warning(
            "[vision-pricing] Vision extractor returned nothing for %s; keeping freeform data",
            competitor_name,
        )
        return

    # The live DB does not have the planned `pricing_snapshots.metadata`
    # column yet, and the user explicitly asked us not to touch the schema.
    # We stash the rich payload inside the existing `trial_info` jsonb column
    # under a single underscore-prefixed key. Old readers ignore unknown keys
    # so this is fully additive.
    trial_info = dict(legacy.get("trial_info") or {})
    trial_info["_vision"] = vision
    trial_info["_pricing_extractor_version"] = PRICING_EXTRACTOR_VERSION
    trial_info["_legacy_plans_pre_vision"] = pricing.get("plans") or []
    trial_info["_legacy_discounts_pre_vision"] = pricing.get("discounts") or []

    pricing["plans"] = plans
    pricing["discounts"] = legacy.get("discounts") or []
    pricing["trial_info"] = trial_info
    log.info(
        "[vision-pricing] Refined pricing for %s: %d plans, %d discounts, trial=%s",
        competitor_name, len(plans), len(legacy.get("discounts") or []),
        trial_info.get("has_trial"),
    )


def _has_pricing_evidence(pricing: dict | None) -> bool:
    if not pricing:
        return False
    if pricing.get("plans"):
        return True
    if pricing.get("discounts"):
        return True
    trial = pricing.get("trial_info")
    if isinstance(trial, dict):
        return bool(trial.get("has_trial") or trial.get("trial_days") or trial.get("trial_price"))
    return False


def _attach_pricing_screenshot(pricing: dict | None, steps: list[dict]) -> None:
    """Attach the LATEST step screenshot that was on the pricing URL.

    Prefers the highest step_number whose URL matches the pricing snapshot's
    URL, since funnels like Mad Muscles render their wheel-spin discount a
    couple seconds *after* arriving at /offer — the early screenshot would
    miss the discount, the late one captures it.
    """
    if not pricing or pricing.get("screenshot_path"):
        return
    pricing_url = pricing.get("url")
    candidates: list[tuple[int, int, str]] = []  # (score, step_number, path)
    for step in steps:
        shot = step.get("screenshot_path")
        if not shot:
            continue
        url = step.get("url") or ""
        text = (step.get("visible_text") or "").lower()
        score = 0
        if pricing_url and url == pricing_url:
            score += 100
        if any(token in url.lower() for token in ("offer", "checkout", "purchase", "paywall", "tariff", "final", "plan")):
            score += 5
        if any(token in text for token in ("choose your plan", "get my plan", "subscription", "billed", "/month", "/week", "/4 weeks")):
            score += 3
        if score:
            candidates.append((score, step.get("step_number", 0), shot))
    if not candidates:
        return
    # Sort by (score desc, step_number desc) — best match, most recent.
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    pricing["screenshot_path"] = candidates[0][2]


def _upload_screenshot(local_path: str | None, remote_path: str) -> str | None:
    if not local_path:
        return None
    try:
        with open(local_path, "rb") as f:
            get_db().storage.from_(SUPABASE_STORAGE_BUCKET).upload(
                remote_path,
                f.read(),
                file_options={"content-type": "image/png", "upsert": "true"},
            )
        return remote_path
    except Exception:
        log.exception("Failed to upload screenshot %s", local_path)
        return local_path


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
    traversal_model = job.get("traversal_model") or DEFAULT_TRAVERSAL_MODEL

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
    log.info("Starting scan %s for %s (job=%s model=%s)",
             run_id, comp["name"], job["id"], traversal_model,
             extra={"run_id": run_id, "competitor_id": competitor_id,
                    "job_id": job["id"], "traversal_model": traversal_model})

    # Check for baseline
    baseline_run, baseline_steps = get_baseline(competitor_id)
    # GPT mini is more reliable when it explores the live funnel from the DOM
    # instead of replaying stale baseline scripts.
    baseline_steps_data = None if traversal_model == DEFAULT_TRAVERSAL_MODEL else (baseline_steps if baseline_steps else None)

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
        effective_config = {**(comp.get("config") or {}), **(job.get("config") or {})}
        baseline_for_run = None if effective_config.get("max_funnel_pages") or effective_config.get("max_pages") else baseline_steps_data

        result = run_traversal_sync(
            competitor_name=comp["name"],
            funnel_url=comp["funnel_url"],
            config=effective_config,
            baseline_steps=baseline_for_run,
            on_progress=_on_progress,
            competitor_slug=comp.get("slug"),
            traversal_model=traversal_model,
            run_id=run_id,
        )

        _upload_scan_artifacts(db, run_id, result["steps"])
        if result.get("pricing"):
            _attach_pricing_screenshot(result["pricing"], result["steps"])
            # Run the vision pass BEFORE the screenshot is uploaded, since the
            # extractor needs the local file. This is the place where we go
            # from "the freeform agent jammed intro+renewal+per-day into one
            # field" to "structured intro vs renewal data with stable plan
            # ids", so the pricing-history chart stops showing fake jumps.
            _refine_pricing_with_vision(result["pricing"], comp["name"], run_id)
            _upload_scan_artifacts(db, run_id, [result["pricing"]])

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
                "screenshot_path": step.get("screenshot_path"),
                "metadata": {k: v for k, v in step.items()
                             if k not in ("step_number", "step_type", "question_text",
                                          "answer_options", "action_taken", "url",
                                          "screenshot_path", "log")},
            }).execute()

        progress_log = _progress_log_buffer

        # Store pricing if captured (skip empty snapshots where agent tagged
        # a page as pricing but extracted no actual plan/discount/trial data).
        # Vision payload is stashed inside trial_info under `_vision` because
        # the planned pricing_snapshots.metadata column is not yet applied to
        # the live DB and the user asked us not to touch the schema.
        pricing = result["pricing"]
        if _has_pricing_evidence(pricing):
            db.table("pricing_snapshots").insert({
                "run_id": run_id,
                "competitor_id": competitor_id,
                "plans": pricing.get("plans"),
                "discounts": pricing.get("discounts"),
                "trial_info": pricing.get("trial_info"),
                "captured_at_step": pricing.get("step_number"),
                "url": pricing.get("url"),
                "screenshot_path": pricing.get("screenshot_path"),
            }).execute()

        # Update run as completed
        summary = result["summary"]
        if not _has_pricing_evidence(pricing) and summary.get("stop_reason") in {"unknown", "max_steps", "funnel_reset"}:
            raise RuntimeError(
                f"Traversal ended before pricing: stop_reason={summary.get('stop_reason')} "
                f"steps={len(result['steps'])}"
            )
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
            # browser-use's bubus EventBus accumulates stale asyncio state between
            # successive asyncio.run() calls in the same process, causing Chrome to
            # fail on the second scan (30s timeout on CDP port bind).  The cleanest
            # fix is to exit after each scan so systemd restarts with a clean slate.
            # RestartSec=2 in the unit file keeps the gap brief.
            log.info("Worker %s exiting after scan for clean restart", WORKER_ID)
            sys.exit(0)
        else:
            # Background ad/domain loops only run on the primary worker to
            # avoid triple-firing when multiple workers are deployed.
            pending_scan_job = has_pending_scan_job()
            # region agent log
            _dbg(
                "H1-H3",
                "backend/worker/loop.py:main",
                "Background-loop gate check",
                {"is_primary": IS_PRIMARY, "worker_id": WORKER_ID, "has_pending_scan_job": pending_scan_job},
            )
            # endregion
            if IS_PRIMARY and not pending_scan_job:
                try:
                    maybe_run_ad_scrape()
                except Exception:
                    log.exception("Ad scrape check failed")
                try:
                    maybe_run_domain_intel()
                except Exception:
                    log.exception("Domain intel check failed")
            else:
                # region agent log
                _dbg(
                    "H1-H3",
                    "backend/worker/loop.py:main",
                    "Skipped background loops this cycle",
                    {
                        "reason": "not_primary" if not IS_PRIMARY else "pending_scan_job",
                        "is_primary": IS_PRIMARY,
                        "has_pending_scan_job": pending_scan_job,
                    },
                )
                # endregion
            time.sleep(POLL_INTERVAL)
    log.info("Worker stopped")


if __name__ == "__main__":
    main()
