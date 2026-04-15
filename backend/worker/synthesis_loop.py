"""Weekly synthesis loop — the scheduler that runs the synthesis pipeline.

Called from backend.worker.loop.main() as a fourth peer alongside funnel
scans, ad scrape, and domain intel. On a weekly schedule (or via a pending
row for manual trigger), this module:

  1. Computes the target week_of (most recent Monday).
  2. Creates a synthesis_runs row with status='running'.
  3. Runs the freshness gate — if any tracked source is stale beyond
     FRESHNESS_STALE_HOURS, marks the run aborted_stale and returns.
  4. Runs pattern_extraction.extract_all_patterns() — deterministic mining.
  5. Runs ship_list.generate_ship_list(week_of=...) — LLM + citation guard.
  6. Updates the synthesis_runs row with status, counts, cost, duration.
  7. Alerts on completion (happy or sad).

Recovery: cleanup_stale_jobs() in loop.py marks any 'running' synthesis_runs
row as 'failed' on worker startup. Next tick then re-enters the schedule
guard and re-runs if still due.

Failure mode hierarchy (from most recoverable to least):
  aborted_stale  → retry next cycle when freshness recovers
  empty          → not a failure; honest output
  failed         → logged, alerted, retried next cycle up to 3x/day
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from backend.config import (
    SYNTHESIS_DAY_OF_WEEK,
    SYNTHESIS_HOUR_UTC,
    SYNTHESIS_MAX_FAILURES_PER_DAY,
)
from backend.db import get_db
from backend.worker import freshness, pattern_extraction, ship_list
from backend.worker.alerts import send_alert

log = logging.getLogger(__name__)


# --- Public entry point -----------------------------------------------------


def maybe_run_synthesis() -> None:
    """Check if a synthesis run is due and execute if so.

    Mirrors the guard structure of maybe_run_ad_scrape / maybe_run_domain_intel:
      - Pending row (manual trigger) always runs immediately.
      - Otherwise require day-of-week + hour-of-day match.
      - Skip if a run already completed for this week_of.
      - Skip if >= SYNTHESIS_MAX_FAILURES_PER_DAY failures today.
    """
    db = get_db()
    now = datetime.now(timezone.utc)

    # 1. Pending manual trigger — always takes priority.
    pending = (
        db.table("synthesis_runs")
        .select("id")
        .eq("status", "pending")
        .limit(1)
        .execute()
    )
    if pending.data:
        log.info("Found manually triggered synthesis run, starting now")
        _run_synthesis(_compute_week_of(now), trigger="manual", now=now)
        return

    # 2. Day-of-week gate.
    if now.weekday() != SYNTHESIS_DAY_OF_WEEK:
        return

    # 3. Hour-of-day gate.
    if now.hour < SYNTHESIS_HOUR_UTC:
        return

    # 4. Already ran this week?
    week_of = _compute_week_of(now)
    existing = (
        db.table("synthesis_runs")
        .select("id,status")
        .eq("week_of", week_of.isoformat())
        .in_("status", ["running", "completed", "empty", "aborted_stale"])
        .limit(1)
        .execute()
    )
    if existing.data:
        return

    # 5. Too many failures today?
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    failed_today = (
        db.table("synthesis_runs")
        .select("id")
        .gte("created_at", today_start)
        .eq("status", "failed")
        .execute()
    )
    if len(failed_today.data or []) >= SYNTHESIS_MAX_FAILURES_PER_DAY:
        log.warning(
            "Synthesis has failed %d times today, skipping until tomorrow",
            len(failed_today.data or []),
        )
        return

    log.info("Starting weekly synthesis run for week of %s", week_of)
    _run_synthesis(week_of, trigger="scheduled", now=now)


# --- Run orchestration ------------------------------------------------------


def _run_synthesis(week_of: date, trigger: str, now: datetime) -> None:
    """Execute one synthesis run end-to-end, never raising."""
    db = get_db()
    started_at = now
    started_iso = started_at.isoformat()

    # Claim a pending row if we're running a manual trigger, else insert a new row.
    run_id = _claim_or_create_run(week_of, trigger, started_iso)
    if run_id is None:
        log.error("Failed to create synthesis_runs row; aborting cycle")
        return

    stale_sources: list[dict] = []
    stats: dict[str, Any] = {}

    try:
        # --- Phase 1: freshness gate --------------------------------------
        stale_sources = freshness.get_stale_sources()
        if stale_sources:
            _finish_aborted_stale(run_id, stale_sources, started_at)
            return

        # --- Phase 2: deterministic pattern extraction --------------------
        pattern_stats = pattern_extraction.extract_all_patterns(now=now)

        # --- Phase 3: LLM ship list generation ----------------------------
        ship_stats = ship_list.generate_ship_list(
            week_of=week_of,
            now=now,
            generated_by_run_id=run_id,
        )

        # --- Phase 4: persist observability --------------------------------
        duration_s = max(0, int((datetime.now(timezone.utc) - started_at).total_seconds()))
        final_status = _map_ship_status_to_run_status(ship_stats.get("status"))
        update = {
            "status": final_status,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_s": duration_s,
            "candidate_pattern_count": ship_stats.get("candidate_pattern_count", 0),
            "prior_outcome_count": ship_stats.get("prior_outcome_count", 0),
            "patterns_found": pattern_stats.get("patterns_found", 0),
            "patterns_persisted": pattern_stats.get("patterns_persisted", 0),
            "ship_list_item_count": ship_stats.get("items_accepted", 0),
            "items_rejected_shape": ship_stats.get("items_rejected_shape", 0),
            "items_rejected_citation": ship_stats.get("items_rejected_citation", 0),
            "retries": ship_stats.get("retries", 0),
            "llm_cost_cents": ship_stats.get("llm_cost_cents", 0),
            "input_tokens": ship_stats.get("input_tokens", 0),
            "output_tokens": ship_stats.get("output_tokens", 0),
            "error": ship_stats.get("error"),
        }
        db.table("synthesis_runs").update(update).eq("id", run_id).execute()

        _alert_on_final_status(
            final_status, week_of, pattern_stats, ship_stats, stale_sources=[],
        )

    except Exception as e:
        log.exception("Synthesis run failed")
        try:
            db.table("synthesis_runs").update({
                "status": "failed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "duration_s": max(0, int((datetime.now(timezone.utc) - started_at).total_seconds())),
                "error": str(e)[:500],
            }).eq("id", run_id).execute()
        except Exception:
            log.exception("Failed to update synthesis_runs row on error path")
        send_alert(f"Synthesis run failed for week of {week_of}: {e}")


# --- Helpers ----------------------------------------------------------------


def _claim_or_create_run(week_of: date, trigger: str, started_iso: str) -> str | None:
    """Claim a pending row if one exists, else insert a fresh running row.

    Returns the run id or None on catastrophic DB failure.
    """
    db = get_db()

    if trigger == "manual":
        pending = (
            db.table("synthesis_runs")
            .select("id")
            .eq("status", "pending")
            .limit(1)
            .execute()
        )
        if pending.data:
            run_id = pending.data[0]["id"]
            try:
                db.table("synthesis_runs").update({
                    "status": "running",
                    "started_at": started_iso,
                    "week_of": week_of.isoformat(),
                    "trigger": "manual",
                }).eq("id", run_id).execute()
                return run_id
            except Exception:
                log.exception("Failed to claim pending synthesis_runs row")
                return None

    try:
        created = (
            db.table("synthesis_runs")
            .insert({
                "status": "running",
                "week_of": week_of.isoformat(),
                "trigger": trigger,
                "started_at": started_iso,
            })
            .execute()
        )
        return created.data[0]["id"] if created.data else None
    except Exception:
        log.exception("Failed to insert new synthesis_runs row")
        return None


def _finish_aborted_stale(run_id: str, stale_sources: list[dict], started_at: datetime) -> None:
    """Mark the run aborted_stale and alert with the list of stale sources."""
    db = get_db()
    completed_at = datetime.now(timezone.utc)
    duration_s = max(0, int((completed_at - started_at).total_seconds()))
    try:
        db.table("synthesis_runs").update({
            "status": "aborted_stale",
            "completed_at": completed_at.isoformat(),
            "duration_s": duration_s,
            "stale_sources": stale_sources,
            "error": _build_stale_error_message(stale_sources),
        }).eq("id", run_id).execute()
    except Exception:
        log.exception("Failed to update synthesis_runs row on aborted_stale")

    send_alert(
        f"Synthesis aborted: {len(stale_sources)} stale source(s). "
        f"Ship list not generated this week until data recovers."
    )


def _map_ship_status_to_run_status(ship_status: str | None) -> str:
    """Translate ship_list.generate_ship_list's status into a synthesis_runs status."""
    mapping = {
        "completed": "completed",
        "empty": "empty",
        "failed": "failed",
    }
    return mapping.get(ship_status or "", "failed")


def _alert_on_final_status(
    final_status: str,
    week_of: date,
    pattern_stats: dict,
    ship_stats: dict,
    *,
    stale_sources: list[dict],
) -> None:
    """Send one alert per run outcome with enough context to be useful."""
    if final_status == "completed":
        item_count = ship_stats.get("items_accepted", 0)
        cost_cents = ship_stats.get("llm_cost_cents", 0)
        send_alert(
            f"Ship list ready for week of {week_of}: "
            f"{item_count} item(s), "
            f"{pattern_stats.get('patterns_persisted', 0)} patterns mined, "
            f"cost {cost_cents}¢"
        )
    elif final_status == "empty":
        send_alert(
            f"No ship list this week ({week_of}): "
            f"{pattern_stats.get('patterns_persisted', 0)} pattern(s) found, "
            f"none rose to the bar. Empty is honest."
        )
    elif final_status == "failed":
        err = ship_stats.get("error") or "unknown"
        send_alert(f"Ship list generation failed for week of {week_of}: {err}")


# --- Pure helpers (unit-testable) -------------------------------------------


def _compute_week_of(now: datetime) -> date:
    """Return the Monday of the calendar week containing `now` (UTC)."""
    today = now.astimezone(timezone.utc).date()
    # Python: Monday=0 .. Sunday=6. weekday() returns that directly.
    return today - timedelta(days=today.weekday())


def _build_stale_error_message(stale_sources: list[dict]) -> str:
    """Short-string summary of stale sources for synthesis_runs.error."""
    if not stale_sources:
        return "no stale sources"
    by_source: dict[str, int] = {}
    for row in stale_sources:
        src = row.get("source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1
    parts = [f"{src}={count}" for src, count in sorted(by_source.items())]
    return "stale: " + ", ".join(parts)


# --- Worker-restart recovery hook -------------------------------------------


def cleanup_stale_synthesis_runs() -> int:
    """Mark any 'running' synthesis_runs row as 'failed'.

    Called from loop.cleanup_stale_jobs() at worker startup. If a run was
    in flight when the worker died, its row would stay at 'running' forever
    and the schedule guard would skip re-running it this week. This sweep
    fixes that so the next tick can retry.
    """
    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        res = (
            db.table("synthesis_runs")
            .update({
                "status": "failed",
                "completed_at": now_iso,
                "error": "Worker restarted — synthesis was interrupted",
            })
            .eq("status", "running")
            .execute()
        )
    except Exception:
        log.exception("cleanup_stale_synthesis_runs: update failed")
        return 0
    count = len(res.data) if res.data else 0
    if count:
        log.warning("Cleaned up %d stale synthesis_runs from previous worker", count)
    return count


__all__ = [
    "maybe_run_synthesis",
    "cleanup_stale_synthesis_runs",
    "_compute_week_of",
    "_build_stale_error_message",
    "_map_ship_status_to_run_status",
]
