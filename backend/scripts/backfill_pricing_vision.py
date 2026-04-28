"""Backfill the vision pricing extractor against existing pricing snapshots.

Why this exists: the pricing-history page already has hundreds of snapshots
from the freeform agent that mash intro + renewal + per-day prices into a
single field. Re-running every funnel from scratch would take hours of
browser work; instead we re-process the screenshots that are already in
Supabase storage with the new extractor, then patch the DB row.

Usage::

    python -m backend.scripts.backfill_pricing_vision \\
        --slug mad-muscles \\
        --slug betterme-wall-pilates \\
        --slug bioma-health \\
        --max 25 \\
        --dry-run

Without ``--dry-run`` the script writes ``metadata.vision`` and refreshes
``plans``/``discounts``/``trial_info`` for each row that the extractor was
able to read.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

from backend.config import SUPABASE_STORAGE_BUCKET
from backend.db import get_db
from backend.worker.pricing_extractor import (
    PRICING_EXTRACTOR_VERSION,
    extract_from_path,
    vision_to_legacy,
)

log = logging.getLogger("backfill_pricing_vision")


def _resolve_competitor_ids(db, slugs: list[str]) -> dict[str, str]:
    rows = db.table("competitors").select("id,name,slug").execute().data
    out = {}
    for row in rows:
        if row["slug"] in slugs:
            out[row["slug"]] = row
    missing = sorted(set(slugs) - set(out.keys()))
    if missing:
        raise SystemExit(f"Unknown slugs: {', '.join(missing)}")
    return out


def _list_snapshots(db, competitor_id: str, limit: int) -> list[dict]:
    return (
        db.table("pricing_snapshots")
        .select("id,run_id,competitor_id,url,screenshot_path,plans,discounts,trial_info,created_at")
        .eq("competitor_id", competitor_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


def _fetch_screenshot(db, path: str, dest_dir: Path) -> Path | None:
    try:
        data = db.storage.from_(SUPABASE_STORAGE_BUCKET).download(path)
    except Exception:
        log.warning("download failed for %s", path, exc_info=True)
        return None
    out = dest_dir / Path(path).name
    out.write_bytes(data)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", action="append", required=True,
                        help="Competitor slug (repeatable)")
    parser.add_argument("--max", type=int, default=20,
                        help="Most recent N snapshots per competitor (default 20)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without writing")
    parser.add_argument("--include-existing", action="store_true",
                        help="Re-extract even snapshots that already have metadata.vision")
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )

    db = get_db()
    comps = _resolve_competitor_ids(db, args.slug)
    log.info("Found %d competitor(s): %s", len(comps), ", ".join(c['name'] for c in comps.values()))

    overall = {"snapshots": 0, "extracted": 0, "skipped": 0, "errors": 0, "updated": 0}
    with tempfile.TemporaryDirectory(prefix="backfill-") as tmpdir:
        tmp = Path(tmpdir)
        for slug, comp in comps.items():
            print(f"\n=== {comp['name']} ({slug}) ===")
            snaps = _list_snapshots(db, comp["id"], args.max)
            print(f"  {len(snaps)} snapshots (most recent {args.max})")
            for snap in snaps:
                overall["snapshots"] += 1
                if not snap.get("screenshot_path"):
                    print(f"  - {snap['created_at'][:19]}  no screenshot, skipping")
                    overall["skipped"] += 1
                    continue
                if not args.include_existing and (snap.get("trial_info") or {}).get("_vision"):
                    print(f"  - {snap['created_at'][:19]}  already has vision data, skipping")
                    overall["skipped"] += 1
                    continue

                path = _fetch_screenshot(db, snap["screenshot_path"], tmp)
                if not path:
                    overall["errors"] += 1
                    continue
                t0 = time.perf_counter()
                try:
                    vision = extract_from_path(
                        path,
                        competitor_name=comp["name"],
                        url=snap.get("url"),
                    )
                except Exception:
                    log.exception("extractor failed for %s", snap["id"])
                    overall["errors"] += 1
                    continue
                dt = time.perf_counter() - t0
                overall["extracted"] += 1
                legacy = vision_to_legacy(vision)
                plans = legacy.get("plans") or []
                discounts = legacy.get("discounts") or []
                trial_info = legacy.get("trial_info") or {}
                print(f"  + {snap['created_at'][:19]}  plans={len(plans)} discounts={len(discounts)} trial={trial_info.get('has_trial')} ({dt:.1f}s)")
                for p in plans:
                    print(f"      {p.get('plan_id'):14}  {p['price_kind']:7}  {p['price']} {p['currency']}  {(p.get('name') or '')[:32]}")

                if args.dry_run:
                    continue

                if not plans and not discounts and not trial_info.get("has_trial"):
                    log.warning("Vision extractor returned nothing usable for snapshot %s; skipping write", snap["id"])
                    overall["skipped"] += 1
                    continue

                # Stash the rich payload inside the existing trial_info jsonb
                # column under underscore-prefixed keys (the planned dedicated
                # metadata column has not been applied to the live DB yet).
                trial_info["_vision"] = vision
                trial_info["_pricing_extractor_version"] = PRICING_EXTRACTOR_VERSION
                trial_info["_legacy_plans_pre_vision"] = snap.get("plans") or []
                trial_info["_legacy_discounts_pre_vision"] = snap.get("discounts") or []
                trial_info["_backfilled_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

                db.table("pricing_snapshots").update({
                    "plans": plans,
                    "discounts": discounts,
                    "trial_info": trial_info,
                }).eq("id", snap["id"]).execute()
                overall["updated"] += 1

    print("\n=== Summary ===")
    for k, v in overall.items():
        print(f"  {k:12s}  {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
