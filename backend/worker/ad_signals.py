"""Signal computation for Meta Ad tracking.

Compares today's ad data against historical state to derive actionable signals.
"""

from __future__ import annotations
import logging
from datetime import date, timedelta

from backend.db import get_db

log = logging.getLogger(__name__)


def _days_active(start_date_raw, reference_date: date) -> int | None:
    """Parse start_date and return days active, or None on failure."""
    try:
        start = date.fromisoformat(str(start_date_raw)[:10])
        return (reference_date - start).days
    except (ValueError, TypeError):
        return None


def compute_signals(
    competitor_id: str,
    today_ads: list[dict],
    today: date,
    ad_id_map: dict[str, str] | None = None,
) -> list[dict]:
    """Compute all signals for a competitor's ads on a given date.

    Args:
        competitor_id: UUID of the competitor.
        today_ads: List of normalized ad dicts from today's scrape.
        today: The date of the scrape.
        ad_id_map: Optional mapping of meta_ad_id -> DB UUID from batch upsert.
                   If provided, used instead of querying the ads table.

    Returns:
        List of signal dicts ready for insertion into ad_signals.
    """
    signals: list[dict] = []
    db = get_db()

    # Load existing ads for this competitor (or use provided map)
    if ad_id_map is not None:
        existing_map = ad_id_map
    else:
        existing_res = (
            db.table("ads")
            .select("id, meta_ad_id")
            .eq("competitor_id", competitor_id)
            .execute()
        )
        existing_map = {row["meta_ad_id"]: row["id"] for row in existing_res.data}

    # Load yesterday's snapshots for diff
    yesterday = today - timedelta(days=1)
    yesterday_res = (
        db.table("ad_snapshots")
        .select("ad_id, headline, body_text, start_date")
        .eq("competitor_id", competitor_id)
        .eq("captured_date", yesterday.isoformat())
        .execute()
    )
    yesterday_map = {row["ad_id"]: row for row in yesterday_res.data}

    # Load existing proven_winner signals to avoid duplicates
    existing_winners_res = (
        db.table("ad_signals")
        .select("ad_id")
        .eq("competitor_id", competitor_id)
        .eq("signal_type", "proven_winner")
        .execute()
    )
    existing_winner_ad_ids = {row["ad_id"] for row in existing_winners_res.data}

    # Load existing failed_test signals to avoid duplicates
    existing_failed_res = (
        db.table("ad_signals")
        .select("ad_id")
        .eq("competitor_id", competitor_id)
        .eq("signal_type", "failed_test")
        .execute()
    )
    existing_failed_ad_ids = {row["ad_id"] for row in existing_failed_res.data}

    for ad in today_ads:
        meta_ad_id = ad["meta_ad_id"]
        if not meta_ad_id:
            continue

        ad_db_id = existing_map.get(meta_ad_id)

        # --- Signal: new_ad ---
        is_new = meta_ad_id not in existing_map
        if is_new:
            signals.append({
                "competitor_id": competitor_id,
                "ad_id": ad_db_id,
                "signal_type": "new_ad",
                "severity": "medium",
                "title": f"New ad detected: {ad.get('headline') or meta_ad_id[:20]}",
                "detail": ad.get("body_text", "")[:300] if ad.get("body_text") else None,
                "metadata": {"meta_ad_id": meta_ad_id, "media_type": ad.get("media_type")},
                "signal_date": today.isoformat(),
            })

        # --- Signal: proven_winner ---
        if not is_new and (
            ad.get("status") == "ACTIVE"
            and ad.get("start_date")
            and ad_db_id
            and ad_db_id not in existing_winner_ad_ids
        ):
            days = _days_active(ad["start_date"], today)
            if days is not None and days >= 30:
                signals.append({
                    "competitor_id": competitor_id,
                    "ad_id": ad_db_id,
                    "signal_type": "proven_winner",
                    "severity": "high",
                    "title": f"Proven winner ({days}d active): {ad.get('headline') or meta_ad_id[:20]}",
                    "detail": f"Running since {str(ad['start_date'])[:10]}",
                    "metadata": {"meta_ad_id": meta_ad_id, "days_active": days},
                    "signal_date": today.isoformat(),
                })

        # --- Signal: copy_change ---
        if not is_new and ad_db_id and ad_db_id in yesterday_map:
            prev = yesterday_map[ad_db_id]
            changes = []
            if ad.get("headline") and prev.get("headline") and ad["headline"] != prev["headline"]:
                changes.append(f"headline: '{prev['headline'][:50]}' -> '{ad['headline'][:50]}'")
            if ad.get("body_text") and prev.get("body_text") and ad["body_text"] != prev["body_text"]:
                changes.append("body text changed")
            if changes:
                signals.append({
                    "competitor_id": competitor_id,
                    "ad_id": ad_db_id,
                    "signal_type": "copy_change",
                    "severity": "medium",
                    "title": f"Copy change: {ad.get('headline') or meta_ad_id[:20]}",
                    "detail": "; ".join(changes),
                    "metadata": {"meta_ad_id": meta_ad_id, "changes": changes},
                    "signal_date": today.isoformat(),
                })

    # --- Signal: failed_test (disappearance-based) ---
    # Ads in yesterday's scrape but absent from today's, started within 7 days
    today_meta_ids = {ad["meta_ad_id"] for ad in today_ads if ad.get("meta_ad_id")}
    id_to_meta = {v: k for k, v in existing_map.items()}

    for ad_db_id, snapshot in yesterday_map.items():
        if ad_db_id in existing_failed_ad_ids:
            continue
        meta_ad_id = id_to_meta.get(ad_db_id)
        if not meta_ad_id:
            continue
        if meta_ad_id in today_meta_ids:
            continue  # still present, not a disappearance
        days = _days_active(snapshot.get("start_date"), today)
        if days is not None and 0 <= days <= 7:
            signals.append({
                "competitor_id": competitor_id,
                "ad_id": ad_db_id,
                "signal_type": "failed_test",
                "severity": "medium",
                "title": f"Failed test ({days}d lifespan): {id_to_meta.get(ad_db_id, '')[:20]}",
                "detail": f"Started {str(snapshot.get('start_date', ''))[:10]}, disappeared from scrape",
                "metadata": {"meta_ad_id": meta_ad_id, "days_active": days},
                "signal_date": today.isoformat(),
            })

    # --- Signal: count_spike (competitor-level, not per-ad) ---
    signals.extend(_check_count_spike(competitor_id, today_ads, today))

    return signals


def _check_count_spike(competitor_id: str, today_ads: list[dict], today: date) -> list[dict]:
    """Check if today's active ad count is a spike vs 28-day average."""
    db = get_db()
    active_today = sum(1 for a in today_ads if a.get("status") == "ACTIVE")
    if active_today < 5:
        return []  # too few ads to be meaningful

    # Get daily active counts for last 28 days from snapshots
    window_start = (today - timedelta(days=28)).isoformat()
    counts_res = (
        db.table("ad_snapshots")
        .select("captured_date, id")
        .eq("competitor_id", competitor_id)
        .eq("status", "ACTIVE")
        .gte("captured_date", window_start)
        .lt("captured_date", today.isoformat())
        .execute()
    )

    if not counts_res.data:
        return []

    # Group by date and count
    by_date: dict[str, int] = {}
    for row in counts_res.data:
        d = row["captured_date"]
        by_date[d] = by_date.get(d, 0) + 1

    if not by_date:
        return []

    avg = sum(by_date.values()) / len(by_date)
    if avg > 0 and active_today > avg * 2.0:
        return [{
            "competitor_id": competitor_id,
            "signal_type": "count_spike",
            "severity": "high",
            "title": f"Ad count spike: {active_today} active (avg {avg:.0f})",
            "detail": f"{active_today} active ads today vs {avg:.0f} average over {len(by_date)} days",
            "metadata": {"active_today": active_today, "avg_28d": round(avg, 1)},
            "signal_date": today.isoformat(),
        }]

    return []
