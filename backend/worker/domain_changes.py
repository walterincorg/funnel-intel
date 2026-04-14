"""Fingerprint change detection — compare current vs previous extraction."""

from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone

from backend.db import get_db
from backend.worker.alerts import send_alert

log = logging.getLogger(__name__)


def detect_changes(competitor_id: str, competitor_name: str) -> int:
    """Compare current fingerprints against previous snapshot for one competitor.

    Returns number of changes detected.
    """
    db = get_db()
    changes = 0

    # Get current fingerprints
    current = (
        db.table("domain_fingerprints")
        .select("fingerprint_type, fingerprint_value")
        .eq("competitor_id", competitor_id)
        .execute()
        .data
    )
    current_set = {(r["fingerprint_type"], r["fingerprint_value"]) for r in current}

    # Get previous changes to determine what was there before
    # We compare against what we had last time by looking at domain_changes history
    # and the current state. Simpler approach: store "previous" in metadata.
    # For now, we detect changes by looking at what's in the DB that wasn't there
    # before the current extraction run.
    #
    # Since fingerprints are upserted (not replaced), we detect changes by:
    # 1. New codes = fingerprints captured_at within last hour that didn't exist before
    # 2. Removed codes = we need to track what was there. Use a simple approach:
    #    after extraction, any fingerprint NOT refreshed (captured_at older than today)
    #    for this competitor means it was removed.

    today = datetime.now(timezone.utc).date().isoformat()

    # Find stale fingerprints (not refreshed today) = potentially removed
    all_fps = (
        db.table("domain_fingerprints")
        .select("id, fingerprint_type, fingerprint_value, captured_at")
        .eq("competitor_id", competitor_id)
        .execute()
        .data
    )

    for fp in all_fps:
        captured = fp.get("captured_at", "")
        if captured and captured[:10] < today:
            # This fingerprint was not refreshed in the latest run = removed
            changes += 1
            try:
                db.table("domain_changes").insert({
                    "competitor_id": competitor_id,
                    "fingerprint_type": fp["fingerprint_type"],
                    "change_type": "code_removed",
                    "old_value": fp["fingerprint_value"],
                    "new_value": None,
                }).execute()

                # Alert on high-value removals (GA/Pixel)
                if fp["fingerprint_type"] in ("google_analytics", "facebook_pixel"):
                    send_alert(
                        f"Domain Intel: {competitor_name} removed {fp['fingerprint_type']} "
                        f"{fp['fingerprint_value']}"
                    )

                # Delete the stale fingerprint
                db.table("domain_fingerprints").delete().eq("id", fp["id"]).execute()

            except Exception:
                log.exception("Failed to record change for %s", competitor_name)

    if changes:
        log.info("  %s: %d fingerprint changes detected", competitor_name, changes)

    return changes


def cleanup_old_changes():
    """Delete domain_changes older than 12 months."""
    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    try:
        db.table("domain_changes").delete().lt("detected_at", cutoff).execute()
        log.info("Cleaned up domain_changes older than 12 months")
    except Exception:
        log.exception("Failed to clean up old domain_changes")
