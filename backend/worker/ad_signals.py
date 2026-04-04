"""Signal computation for Meta Ad tracking.

Compares today's ad data against historical state to derive actionable signals.
"""

from __future__ import annotations
import logging
from datetime import date, timedelta

from backend.db import get_db

log = logging.getLogger(__name__)


def compute_signals(competitor_id: str, today_ads: list[dict], today: date) -> list[dict]:
    """Compute all signals for a competitor's ads on a given date.

    Args:
        competitor_id: UUID of the competitor.
        today_ads: List of normalized ad dicts from today's scrape.
        today: The date of the scrape.

    Returns:
        List of signal dicts ready for insertion into ad_signals.
    """
    signals: list[dict] = []
    db = get_db()

    # Load existing ads for this competitor
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
        .select("ad_id, headline, body_text, platforms")
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

    for ad in today_ads:
        meta_ad_id = ad["meta_ad_id"]
        if not meta_ad_id:
            continue

        ad_db_id = existing_map.get(meta_ad_id)

        # --- Signal: new_ad ---
        if meta_ad_id not in existing_map:
            signals.append({
                "competitor_id": competitor_id,
                "signal_type": "new_ad",
                "severity": "medium",
                "title": f"New ad detected: {ad.get('headline') or meta_ad_id[:20]}",
                "detail": ad.get("body_text", "")[:300] if ad.get("body_text") else None,
                "metadata": {"meta_ad_id": meta_ad_id, "media_type": ad.get("media_type")},
                "signal_date": today.isoformat(),
            })
            continue  # skip other signals for brand-new ads

        # --- Signal: proven_winner ---
        if (
            ad.get("status") == "ACTIVE"
            and ad.get("start_date")
            and ad_db_id
            and ad_db_id not in existing_winner_ad_ids
        ):
            try:
                start = date.fromisoformat(str(ad["start_date"])[:10])
                if (today - start).days >= 30:
                    signals.append({
                        "competitor_id": competitor_id,
                        "ad_id": ad_db_id,
                        "signal_type": "proven_winner",
                        "severity": "high",
                        "title": f"Proven winner ({(today - start).days}d active): {ad.get('headline') or meta_ad_id[:20]}",
                        "detail": f"Running since {start.isoformat()}",
                        "metadata": {"meta_ad_id": meta_ad_id, "days_active": (today - start).days},
                        "signal_date": today.isoformat(),
                    })
            except (ValueError, TypeError):
                pass

        # --- Signal: copy_change ---
        if ad_db_id and ad_db_id in yesterday_map:
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

        # --- Signal: platform_expansion ---
        if ad_db_id and ad_db_id in yesterday_map:
            prev_platforms = set(yesterday_map[ad_db_id].get("platforms") or [])
            curr_platforms = set(ad.get("platforms") or [])
            new_platforms = curr_platforms - prev_platforms
            if new_platforms:
                signals.append({
                    "competitor_id": competitor_id,
                    "ad_id": ad_db_id,
                    "signal_type": "platform_expansion",
                    "severity": "low",
                    "title": f"Platform expansion: +{', '.join(new_platforms)}",
                    "detail": f"Ad {meta_ad_id[:20]} now on {', '.join(sorted(curr_platforms))}",
                    "metadata": {"meta_ad_id": meta_ad_id, "new_platforms": list(new_platforms)},
                    "signal_date": today.isoformat(),
                })

        # --- Signal: failed_test ---
        if (
            ad.get("status") != "ACTIVE"
            and ad.get("start_date")
        ):
            try:
                start = date.fromisoformat(str(ad["start_date"])[:10])
                if (today - start).days <= 7:
                    signals.append({
                        "competitor_id": competitor_id,
                        "ad_id": ad_db_id,
                        "signal_type": "failed_test",
                        "severity": "medium",
                        "title": f"Failed test ({(today - start).days}d lifespan): {ad.get('headline') or meta_ad_id[:20]}",
                        "detail": f"Started {start.isoformat()}, already inactive",
                        "metadata": {"meta_ad_id": meta_ad_id, "days_active": (today - start).days},
                        "signal_date": today.isoformat(),
                    })
            except (ValueError, TypeError):
                pass

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
    if avg > 0 and active_today > avg * 1.5:
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
