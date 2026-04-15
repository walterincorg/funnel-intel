"""Enqueue scan jobs for every competitor whose last scan is older than the cadence.

Run by the `funnel-scan-scheduler.timer` systemd unit on a short interval
(every ~5 minutes). The cadence window is `SCAN_INTERVAL_MINUTES` (default
90). Competitors with a pending/picked job or a recent scan are skipped.

Usage:
    python -m backend.scripts.enqueue_scheduled_scans
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from backend.db import get_db

SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "90"))


def main() -> int:
    db = get_db()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=SCAN_INTERVAL_MINUTES)).isoformat()

    competitors = db.table("competitors").select("id,name").execute().data
    enqueued = 0
    skipped_pending = 0
    skipped_recent = 0

    for comp in competitors:
        existing = (
            db.table("scan_jobs")
            .select("id")
            .eq("competitor_id", comp["id"])
            .in_("status", ["pending", "picked"])
            .limit(1)
            .execute()
            .data
        )
        if existing:
            skipped_pending += 1
            continue

        last = (
            db.table("scan_runs")
            .select("created_at")
            .eq("competitor_id", comp["id"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        if last and last[0]["created_at"] > cutoff:
            skipped_recent += 1
            continue

        db.table("scan_jobs").insert({
            "competitor_id": comp["id"],
            "priority": 0,
            "status": "pending",
        }).execute()
        enqueued += 1

    print(
        f"enqueued={enqueued} skipped_pending={skipped_pending} "
        f"skipped_recent={skipped_recent} total={len(competitors)} "
        f"cadence_min={SCAN_INTERVAL_MINUTES}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
