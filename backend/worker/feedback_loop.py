"""14-day outcome follow-up loop.

After an operator clicks "Shipping this" on a ship list item, the item
enters the shipping lifecycle and shipping_at is stamped. FEEDBACK_WAIT_DAYS
later (default 14), the feedback loop:

  1. Finds items where shipping_at <= now - FEEDBACK_WAIT_DAYS.
  2. Skips items that already have an outcome recorded.
  3. Skips items that were already alerted (outcome_alerted_at IS NOT NULL).
  4. Sends ONE alert summarizing the items needing outcomes.
  5. Stamps outcome_alerted_at so the worker never re-alerts.

Runs every poll cycle from the main worker loop. The query is cheap
(indexed predicate), and sending the alert is idempotent because alerted
items are filtered out.

Design notes:
  - Alerting is batched: all newly-due items get a single Telegram ping,
    not N pings. Reduces notification fatigue.
  - The outcome prompt lives in the frontend ShipList page — the alert
    is a nudge to go check it, not the primary UI.
  - filter_items_needing_outcome is a pure function so the scheduling
    and filtering logic is unit-testable without a database.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from backend.config import FEEDBACK_WAIT_DAYS
from backend.db import get_db
from backend.worker.alerts import send_alert

log = logging.getLogger(__name__)


# --- Pure helpers (unit-testable) -------------------------------------------


def is_item_due_for_outcome(
    item: dict,
    *,
    now: datetime,
    wait_days: int,
    items_with_outcomes: set[str],
) -> bool:
    """Return True if an item needs an outcome alert right now.

    Conditions (all must be true):
      - shipping_at is set and <= now - wait_days
      - outcome_alerted_at is null (not alerted yet)
      - item has no recorded outcome
      - status is 'shipping' or 'shipped'
    """
    if item.get("outcome_alerted_at"):
        return False
    if item.get("id") in items_with_outcomes:
        return False
    if item.get("status") not in ("shipping", "shipped"):
        return False

    shipping_at = item.get("shipping_at")
    if not shipping_at:
        return False

    try:
        s = shipping_at.replace("Z", "+00:00") if isinstance(shipping_at, str) else None
        if s is None:
            return False
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False

    cutoff = now - timedelta(days=wait_days)
    return dt <= cutoff


def filter_items_needing_outcome(
    items: list[dict],
    *,
    now: datetime,
    wait_days: int,
    items_with_outcomes: set[str],
) -> list[dict]:
    """Return the subset of items that need an outcome alert right now."""
    return [
        item for item in items
        if is_item_due_for_outcome(
            item, now=now, wait_days=wait_days, items_with_outcomes=items_with_outcomes,
        )
    ]


def format_outcome_prompt(items: list[dict], *, wait_days: int) -> str:
    """Build the alert string for a batch of due items.

    One line per item with headline + shipping age, capped at 10 items so
    the alert stays readable. Over 10 → summary line appended.
    """
    count = len(items)
    if count == 0:
        return ""

    plural = "s" if count != 1 else ""
    header = (
        f"{count} ship list item{plural} hit the {wait_days}-day mark. "
        f"Record outcomes in the Ship List page."
    )

    lines = [header, ""]
    visible = items[:10]
    for item in visible:
        headline = (item.get("headline") or "(no headline)")[:80]
        rank = item.get("rank", "?")
        lines.append(f"  #{rank}: {headline}")

    if count > len(visible):
        lines.append(f"  ...and {count - len(visible)} more")

    return "\n".join(lines)


# --- Public entry point -----------------------------------------------------


def maybe_run_feedback_check() -> None:
    """Find newly-due items, alert once, mark them alerted.

    Called from the main worker loop every poll cycle. The query is
    indexed (idx_ship_list_items_feedback_due) and cheap, so running it
    frequently is fine. The alert is deduplicated because alerted items
    are filtered out on the next pass.
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    cutoff_iso = (now - timedelta(days=FEEDBACK_WAIT_DAYS)).isoformat()

    # Load candidate items — the indexed predicate handles most of the filter.
    try:
        res = (
            db.table("ship_list_items")
            .select("id,rank,headline,status,shipping_at,outcome_alerted_at")
            .lte("shipping_at", cutoff_iso)
            .is_("outcome_alerted_at", "null")
            .in_("status", ["shipping", "shipped"])
            .execute()
        )
    except Exception:
        log.exception("feedback_loop: candidate query failed")
        return

    candidates = res.data or []
    if not candidates:
        return

    # Filter out items that already have an outcome (belt-and-suspenders —
    # the outcome_alerted_at column should handle this, but a race could
    # let a founder record an outcome between transitions).
    candidate_ids = [c["id"] for c in candidates]
    try:
        outcomes_res = (
            db.table("ship_list_outcomes")
            .select("ship_list_item_id")
            .in_("ship_list_item_id", candidate_ids)
            .execute()
        )
    except Exception:
        log.exception("feedback_loop: outcomes query failed")
        outcomes_res = None

    items_with_outcomes = {
        row["ship_list_item_id"]
        for row in ((outcomes_res.data if outcomes_res else None) or [])
    }

    due = filter_items_needing_outcome(
        candidates,
        now=now,
        wait_days=FEEDBACK_WAIT_DAYS,
        items_with_outcomes=items_with_outcomes,
    )

    if not due:
        return

    # One alert summarizing all newly-due items.
    try:
        send_alert(format_outcome_prompt(due, wait_days=FEEDBACK_WAIT_DAYS))
    except Exception:
        log.exception("feedback_loop: send_alert failed")

    # Stamp outcome_alerted_at so the next poll doesn't re-alert.
    now_iso = now.isoformat()
    for item in due:
        try:
            db.table("ship_list_items").update({
                "outcome_alerted_at": now_iso,
            }).eq("id", item["id"]).execute()
        except Exception:
            log.exception("feedback_loop: failed to stamp outcome_alerted_at for %s", item.get("id"))

    log.info("feedback_loop: alerted on %d items needing outcomes", len(due))


__all__ = [
    "maybe_run_feedback_check",
    "is_item_due_for_outcome",
    "filter_items_needing_outcome",
    "format_outcome_prompt",
]
