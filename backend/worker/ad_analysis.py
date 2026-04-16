"""Cross-competitor ad briefing for CEO view.

After each scrape run, produces a single briefing that summarises winner ads,
competitor moves, and a suggested action — across ALL competitors at once.
"""

from __future__ import annotations
import logging
import os
from datetime import date, timedelta

import anthropic

from backend.db import get_db
from backend.worker.ad_signals import _days_active

log = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANALYSIS_MODEL = os.getenv("AD_ANALYSIS_MODEL", "claude-sonnet-4-20250514")

BRIEFING_TOOL = {
    "name": "save_briefing",
    "description": "Save the cross-competitor ad intelligence briefing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {
                "type": "string",
                "description": "One bold sentence summarising the most important thing happening across competitors right now. Max 15 words.",
            },
            "summary": {
                "type": "string",
                "description": "2-4 sentence executive summary of what competitors are doing, what's working, and what changed. Be specific — name competitors and reference concrete ads.",
            },
            "suggested_action": {
                "type": "string",
                "description": "One concrete action the team should take based on competitor activity. Be specific and actionable.",
            },
            "winner_ad_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "meta_ad_ids of the top 3-5 winner ads across all competitors, ordered by importance. Pick the most instructive/interesting ones.",
            },
            "competitor_moves": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "competitor_name": {"type": "string"},
                        "move_summary": {
                            "type": "string",
                            "description": "One sentence: what this competitor did this period.",
                        },
                    },
                    "required": ["competitor_name", "move_summary"],
                },
                "description": "One-line summary per competitor of their recent activity.",
            },
        },
        "required": ["headline", "summary", "suggested_action", "winner_ad_ids", "competitor_moves"],
    },
}


def run_briefing(today: date) -> bool:
    """Generate a cross-competitor CEO briefing. Returns True on success."""
    if not ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY not configured, skipping briefing")
        return False

    try:
        return _run_briefing(today)
    except Exception:
        log.exception("Briefing generation failed")
        return False


def _run_briefing(today: date) -> bool:
    db = get_db()

    # Get all competitors
    comps = (
        db.table("competitors")
        .select("id, name")
        .execute()
        .data
    )
    if not comps:
        return False

    comp_names = {c["id"]: c["name"] for c in comps}

    # Gather top 5 winner ads per competitor (by days_active)
    all_winner_ads = []
    for comp in comps:
        ads_res = (
            db.table("ads")
            .select("id, meta_ad_id, media_type, landing_page_url")
            .eq("competitor_id", comp["id"])
            .eq("status", "ACTIVE")
            .order("last_seen_at", desc=True)
            .limit(100)
            .execute()
        )
        if not ads_res.data:
            continue

        # Get latest snapshots for these ads
        ad_ids = [a["id"] for a in ads_res.data]
        snaps = []
        for i in range(0, len(ad_ids), 50):
            batch = ad_ids[i:i + 50]
            batch_res = (
                db.table("ad_snapshots")
                .select("ad_id, headline, body_text, start_date")
                .in_("ad_id", batch)
                .order("captured_date", desc=True)
                .execute()
            )
            snaps.extend(batch_res.data)

        latest_snaps = {}
        for snap in snaps:
            if snap["ad_id"] not in latest_snaps:
                latest_snaps[snap["ad_id"]] = snap

        # Rank by days_active, take top 5
        ranked = []
        for ad in ads_res.data:
            snap = latest_snaps.get(ad["id"], {})
            days = _days_active(snap.get("start_date"), today)
            if days is None:
                continue
            ranked.append({
                "meta_ad_id": ad["meta_ad_id"],
                "competitor": comp["name"],
                "headline": snap.get("headline") or "",
                "body_text": (snap.get("body_text") or "")[:200],
                "media_type": ad.get("media_type") or "unknown",
                "days_active": days,
            })

        ranked.sort(key=lambda x: x["days_active"], reverse=True)
        all_winner_ads.extend(ranked[:5])

    # Gather signal counts per competitor for the last 7 days
    since = (today - timedelta(days=7)).isoformat()
    signals_res = (
        db.table("ad_signals")
        .select("competitor_id, signal_type")
        .gte("signal_date", since)
        .execute()
    )

    signal_counts: dict[str, dict[str, int]] = {}
    for row in signals_res.data:
        cid = row["competitor_id"]
        st = row["signal_type"]
        signal_counts.setdefault(cid, {})
        signal_counts[cid][st] = signal_counts[cid].get(st, 0) + 1

    if not all_winner_ads and not signal_counts:
        log.info("No ads or signals to brief on, skipping")
        return False

    # Build the prompt
    ads_text = "\n".join(
        f"- [{a['meta_ad_id']}] {a['competitor']} | {a['media_type']} | "
        f"{a['days_active']}d active | Headline: {a['headline'][:80]} | "
        f"Body: {a['body_text'][:150]}"
        for a in all_winner_ads
    )

    moves_text = ""
    for comp in comps:
        counts = signal_counts.get(comp["id"], {})
        if not counts:
            moves_text += f"- {comp['name']}: no new signals this week\n"
        else:
            parts = [f"{v} {k.replace('_', ' ')}" for k, v in counts.items()]
            moves_text += f"- {comp['name']}: {', '.join(parts)}\n"

    prompt = (
        f"You are an ad intelligence analyst briefing a CEO. Today is {today.isoformat()}.\n\n"
        f"Here are the top-performing competitor ads (sorted by days running, longest first):\n"
        f"{ads_text}\n\n"
        f"Signal activity this week per competitor:\n"
        f"{moves_text}\n"
        f"Produce a concise CEO briefing. Focus on what's actionable — what creative "
        f"approaches are working, what competitors are testing, and what we should try. "
        f"Use the save_briefing tool."
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=1024,
        tools=[BRIEFING_TOOL],
        messages=[{"role": "user", "content": prompt}],
    )

    tool_input = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "save_briefing":
            tool_input = block.input
            break

    if not tool_input:
        log.warning("LLM did not use save_briefing tool")
        return False

    # Resolve winner meta_ad_ids to DB UUIDs
    winner_meta_ids = tool_input.get("winner_ad_ids", [])
    winner_ads = []
    if winner_meta_ids:
        ads_lookup = (
            db.table("ads")
            .select("id, meta_ad_id, competitor_id")
            .in_("meta_ad_id", winner_meta_ids)
            .execute()
        )
        meta_to_row = {r["meta_ad_id"]: r for r in ads_lookup.data}
        for mid in winner_meta_ids:
            row = meta_to_row.get(mid)
            if row:
                winner_ads.append({
                    "ad_id": row["id"],
                    "meta_ad_id": mid,
                    "competitor_name": comp_names.get(row["competitor_id"], "Unknown"),
                })

    # Upsert briefing (one per date)
    db.table("ad_briefings").upsert({
        "briefing_date": today.isoformat(),
        "headline": tool_input["headline"],
        "summary": tool_input["summary"],
        "suggested_action": tool_input["suggested_action"],
        "winner_ads": winner_ads,
        "competitor_moves": tool_input.get("competitor_moves", []),
    }, on_conflict="briefing_date").execute()

    log.info("CEO briefing saved for %s: %d winner ads", today, len(winner_ads))
    return True
