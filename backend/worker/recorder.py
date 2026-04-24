"""Record a successful first-run traversal into funnel_recordings.

Called from loop.py after traversal.run_traversal() succeeds and no prior
recording exists for the competitor. Does three things:
  1. Converts the captured steps into an action log via trace_parser.
  2. Optionally uploads a trace.zip to the funnel-recordings Supabase bucket
     if the traversal produced one (currently None — left as a seam for the
     CDP-sharing spike documented in the spec).
  3. Writes the funnel_recordings row (PK = competitor_id).

Per Q4 = B: record only on first success. Subsequent runs mutate action_log
in place via replay.persist_patched_action_log().
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from backend.db import get_db
from backend.worker.trace_parser import steps_to_action_log

log = logging.getLogger(__name__)

STORAGE_BUCKET = "funnel-recordings"
MIN_STEPS_FOR_RECORDING = 3


def has_recording(competitor_id: str) -> bool:
    db = get_db()
    res = (
        db.table("funnel_recordings")
        .select("competitor_id")
        .eq("competitor_id", competitor_id)
        .limit(1)
        .execute()
    )
    return bool(res.data)


def load_recording(competitor_id: str) -> dict | None:
    db = get_db()
    res = (
        db.table("funnel_recordings")
        .select("*")
        .eq("competitor_id", competitor_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    row = res.data[0]
    if row.get("is_stale"):
        log.info("Recording for %s is marked stale — skipping replay", competitor_id)
        return None
    return row


def save_recording(
    competitor_id: str,
    steps: list[dict],
    trace_path: Path | None = None,
) -> dict | None:
    """Insert a new funnel_recordings row from a successful first-run traversal.

    Returns the inserted row, or None if the run didn't meet the minimum-steps
    bar (matches the baseline-promotion rule in loop.py).
    """
    if len(steps) < MIN_STEPS_FOR_RECORDING:
        log.info(
            "Skipping recording for %s: only %d steps captured (need %d)",
            competitor_id, len(steps), MIN_STEPS_FOR_RECORDING,
        )
        return None

    action_log = steps_to_action_log(steps)
    if not action_log:
        log.info("Skipping recording for %s: no actionable steps derived", competitor_id)
        return None

    stored_trace_path = None
    if trace_path and trace_path.exists():
        stored_trace_path = _upload_trace(competitor_id, trace_path)

    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "competitor_id": competitor_id,
        "trace_path": stored_trace_path,
        "action_log": action_log,
        "captured_at": now_iso,
        "patch_count": 0,
        "is_stale": False,
        "updated_at": now_iso,
    }
    res = (
        db.table("funnel_recordings")
        .upsert(payload, on_conflict="competitor_id")
        .execute()
    )
    log.info(
        "Saved funnel recording for %s: %d actions (trace=%s)",
        competitor_id, len(action_log), stored_trace_path or "none",
    )
    return res.data[0] if res.data else payload


def persist_patched_action_log(
    competitor_id: str,
    action_log: list[dict],
    patches_applied: int,
) -> None:
    """Update an existing recording after replay applied LLM patches."""
    if patches_applied <= 0:
        return
    db = get_db()
    current = (
        db.table("funnel_recordings")
        .select("patch_count")
        .eq("competitor_id", competitor_id)
        .limit(1)
        .execute()
    )
    current_count = current.data[0]["patch_count"] if current.data else 0
    new_total = current_count + patches_applied
    try:
        db.table("funnel_recordings").update({
            "action_log": action_log,
            "patch_count": new_total,
            "is_stale": new_total >= 5,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("competitor_id", competitor_id).execute()
    except Exception:
        log.exception("Failed to persist %d patches for %s", patches_applied, competitor_id)


def mark_stale(competitor_id: str, reason: str) -> None:
    db = get_db()
    try:
        db.table("funnel_recordings").update({
            "is_stale": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("competitor_id", competitor_id).execute()
        log.warning("Marked recording for %s as stale: %s", competitor_id, reason)
    except Exception:
        log.exception("Failed to mark recording %s stale", competitor_id)


def _upload_trace(competitor_id: str, trace_path: Path) -> str | None:
    """Upload trace.zip to Supabase Storage. Returns the storage path or None."""
    db = get_db()
    remote_path = f"{competitor_id}/trace-{int(datetime.now().timestamp())}.zip"
    try:
        with open(trace_path, "rb") as fh:
            db.storage.from_(STORAGE_BUCKET).upload(
                remote_path,
                fh.read(),
                file_options={"content-type": "application/zip", "upsert": "true"},
            )
        return remote_path
    except Exception as exc:
        log.warning("Failed to upload trace.zip for %s: %s", competitor_id, exc)
        return None


def get_signed_trace_url(trace_path: str, expires_in: int = 3600) -> str | None:
    """Create a short-lived signed URL the dashboard can hand to the Playwright viewer."""
    if not trace_path:
        return None
    db = get_db()
    try:
        result = db.storage.from_(STORAGE_BUCKET).create_signed_url(trace_path, expires_in)
        if isinstance(result, dict):
            return result.get("signedURL") or result.get("signed_url")
        return result
    except Exception as exc:
        log.warning("Failed to sign trace URL %s: %s", trace_path, exc)
        return None


# Convenience: loaders used by loop.py at run-start branch.
def export_action_log(competitor_id: str) -> list[dict]:
    """Return the JSONB action_log for a competitor, or [] if missing/stale."""
    row = load_recording(competitor_id)
    if not row:
        return []
    log_field = row.get("action_log") or []
    if isinstance(log_field, str):
        try:
            return json.loads(log_field)
        except json.JSONDecodeError:
            log.warning("action_log for %s is not valid JSON", competitor_id)
            return []
    return log_field
