"""Ship list API — the product surface for the weekly synthesis output.

Endpoints:
  GET  /api/ship-list                     latest week's items + run context
  GET  /api/ship-list?week=YYYY-MM-DD     specific week
  GET  /api/ship-list/weeks               list of weeks that have content
  POST /api/ship-list/{id}/status         update item status
  POST /api/ship-list/{id}/outcome        record outcome (won/lost/...)
  GET  /api/ship-list/synthesis-runs      last N runs for observability
  POST /api/ship-list/synthesis/trigger   manually trigger a synthesis run
  GET  /api/ship-list/freshness           current freshness dashboard

All endpoints degrade gracefully if the migrations haven't been applied
yet (the synthesis-layer tables may not exist in dev). Errors return
empty results plus a status field the frontend can surface.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.db import get_db
from backend.worker import freshness

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ship-list", tags=["ship-list"])


# --- Request models ---------------------------------------------------------


class StatusUpdate(BaseModel):
    status: str = Field(..., pattern=r"^(proposed|shipping|shipped|skipped|expired)$")


class OutcomeCreate(BaseModel):
    outcome: str = Field(..., pattern=r"^(won|lost|inconclusive|not_tested)$")
    notes: str | None = None


# --- GET ship list by week --------------------------------------------------


@router.get("")
def get_ship_list(week: str | None = None) -> dict[str, Any]:
    """Return the ship list for the given week, or the latest week with
    content if no `week` param. Also includes run context so the frontend
    can render loading/empty/stale/success states honestly.

    Response shape:
      {
        "week_of": "2026-04-13" | null,
        "items": [...],
        "run": { id, status, completed_at, stale_sources, error, ... } | null,
        "available_weeks": [...],   # recent weeks that have items
        "is_stale": bool             # any tracked source stale right now
      }
    """
    db = get_db()

    # 1. Resolve target week.
    target_week: str | None = week
    if target_week is None:
        target_week = _latest_week_with_items()

    # 2. Pull items for that week (ordered by rank).
    items: list[dict] = []
    if target_week is not None:
        try:
            res = (
                db.table("ship_list_items")
                .select("*")
                .eq("week_of", target_week)
                .order("rank")
                .execute()
            )
            items = res.data or []
        except Exception:
            log.exception("get_ship_list: items query failed")

    # 3. Pull the most recent synthesis_run for this week (if any).
    run: dict | None = None
    if target_week is not None:
        try:
            run_res = (
                db.table("synthesis_runs")
                .select("*")
                .eq("week_of", target_week)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if run_res.data:
                run = run_res.data[0]
        except Exception:
            log.exception("get_ship_list: synthesis_runs query failed")

    # 4. Freshness snapshot — affects banner on the frontend.
    try:
        stale = freshness.get_stale_sources()
    except Exception:
        log.exception("get_ship_list: freshness query failed")
        stale = []
    is_stale = bool(stale)

    # 5. Recent weeks that have items (for nav).
    available_weeks = _available_weeks(limit=12)

    return {
        "week_of": target_week,
        "items": items,
        "run": run,
        "available_weeks": available_weeks,
        "is_stale": is_stale,
        "stale_source_count": len(stale),
    }


@router.get("/weeks")
def list_weeks() -> list[dict]:
    """Return recent weeks with item counts so the UI can build a week picker."""
    return _available_weeks(limit=52)


# --- POST status / outcome --------------------------------------------------


@router.post("/{item_id}/status", status_code=200)
def update_item_status(item_id: str, body: StatusUpdate) -> dict:
    """Transition an item's status (the 'Shipping this' button path)."""
    db = get_db()

    update: dict[str, Any] = {"status": body.status}
    if body.status == "shipped":
        update["shipped_at"] = datetime.now(timezone.utc).isoformat()

    try:
        res = (
            db.table("ship_list_items")
            .update(update)
            .eq("id", item_id)
            .execute()
        )
    except Exception as e:
        log.exception("update_item_status: update failed")
        raise HTTPException(500, f"update failed: {e}")

    if not res.data:
        raise HTTPException(404, "ship list item not found")
    return res.data[0]


@router.post("/{item_id}/outcome", status_code=201)
def record_outcome(item_id: str, body: OutcomeCreate) -> dict:
    """Record a shipped item's outcome — fuels the feedback loop in STEP 10."""
    db = get_db()
    try:
        res = (
            db.table("ship_list_outcomes")
            .insert({
                "ship_list_item_id": item_id,
                "outcome": body.outcome,
                "notes": body.notes,
            })
            .execute()
        )
    except Exception as e:
        log.exception("record_outcome: insert failed")
        raise HTTPException(500, f"insert failed: {e}")
    return res.data[0] if res.data else {}


# --- GET synthesis runs (observability) -------------------------------------


@router.get("/synthesis-runs")
def list_synthesis_runs(limit: int = 20) -> list[dict]:
    try:
        return (
            get_db()
            .table("synthesis_runs")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )
    except Exception:
        log.exception("list_synthesis_runs: query failed")
        return []


@router.post("/synthesis/trigger", status_code=201)
def trigger_synthesis() -> dict:
    """Queue a manual synthesis run by inserting a pending row.

    Any existing pending/running row is cancelled first so the worker
    doesn't double-fire.
    """
    db = get_db()
    try:
        db.table("synthesis_runs").update({"status": "failed"}).in_(
            "status", ["pending", "running"]
        ).execute()
        week_of_today = _monday_of(date.today())
        res = (
            db.table("synthesis_runs")
            .insert({
                "status": "pending",
                "trigger": "manual",
                "week_of": week_of_today.isoformat(),
            })
            .execute()
        )
    except Exception as e:
        log.exception("trigger_synthesis: insert failed")
        raise HTTPException(500, f"trigger failed: {e}")

    return {"run_id": res.data[0]["id"] if res.data else None, "status": "pending"}


# --- GET freshness dashboard ------------------------------------------------


@router.get("/freshness")
def get_freshness() -> dict:
    """Return the full data_freshness table plus derived is_stale flags."""
    try:
        res = (
            get_db()
            .table("data_freshness")
            .select("*")
            .execute()
        )
    except Exception:
        log.exception("get_freshness: query failed")
        return {"rows": [], "stale_count": 0}

    rows = res.data or []
    try:
        stale = freshness.get_stale_sources()
    except Exception:
        stale = []
    stale_keys = {(r.get("source"), r.get("competitor_id")) for r in stale}
    enriched = [
        {**r, "is_stale": (r.get("source"), r.get("competitor_id")) in stale_keys}
        for r in rows
    ]
    return {"rows": enriched, "stale_count": len(stale)}


# --- Internal helpers -------------------------------------------------------


def _latest_week_with_items() -> str | None:
    """The most recent week_of that has at least one ship_list_items row."""
    try:
        res = (
            get_db()
            .table("ship_list_items")
            .select("week_of")
            .order("week_of", desc=True)
            .limit(1)
            .execute()
        )
    except Exception:
        log.exception("_latest_week_with_items: query failed")
        return None
    return res.data[0]["week_of"] if res.data else None


def _available_weeks(limit: int) -> list[dict]:
    """Distinct week_of values with item counts, newest first."""
    try:
        res = (
            get_db()
            .table("ship_list_items")
            .select("week_of")
            .order("week_of", desc=True)
            .execute()
        )
    except Exception:
        log.exception("_available_weeks: query failed")
        return []

    counts: dict[str, int] = {}
    for row in res.data or []:
        w = row.get("week_of")
        if not w:
            continue
        counts[w] = counts.get(w, 0) + 1

    ordered = sorted(counts.items(), key=lambda kv: kv[0], reverse=True)[:limit]
    return [{"week_of": w, "item_count": c} for w, c in ordered]


def _monday_of(d: date) -> date:
    from datetime import timedelta
    return d - timedelta(days=d.weekday())
