"""LLM-powered ad analysis per competitor.

After each scrape, analyzes top-performing ads and produces a structured
strategy summary using the Anthropic API with tool_use for guaranteed JSON.
"""

from __future__ import annotations
import logging
import os
from datetime import date

import anthropic

from backend.db import get_db
from backend.worker.ad_signals import _days_active

log = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANALYSIS_MODEL = os.getenv("AD_ANALYSIS_MODEL", "claude-sonnet-4-20250514")

ANALYSIS_TOOL = {
    "name": "save_analysis",
    "description": "Save the structured analysis of a competitor's ad strategy.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "2-3 sentence analysis of overall ad strategy. Be specific about what creative approaches, copy patterns, and targeting signals are working.",
            },
            "top_ads": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "meta_ad_id": {"type": "string", "description": "The meta_ad_id of the ad"},
                        "reason": {"type": "string", "description": "Why this ad is a top performer (1 sentence)"},
                    },
                    "required": ["meta_ad_id", "reason"],
                },
                "description": "Top 3-5 ads with reasons why they're winning.",
            },
            "strategy_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3-6 short tags describing the strategy (e.g. 'long-form copy', 'UGC video', 'discount-driven', 'social proof').",
            },
        },
        "required": ["summary", "top_ads", "strategy_tags"],
    },
}


def run_competitor_analysis(competitor_id: str, today: date) -> bool:
    """Run LLM analysis for a competitor's ads. Returns True on success."""
    if not ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY not configured, skipping analysis")
        return False

    try:
        return _run_analysis(competitor_id, today)
    except Exception:
        log.exception("Analysis failed for competitor %s", competitor_id)
        return False


def _run_analysis(competitor_id: str, today: date) -> bool:
    db = get_db()

    # Get competitor name
    comp = (
        db.table("competitors")
        .select("name")
        .eq("id", competitor_id)
        .single()
        .execute()
    )
    comp_name = comp.data["name"] if comp.data else "Unknown"

    # Get top 20 active ads by days_active
    ads_res = (
        db.table("ads")
        .select("id, meta_ad_id, status, media_type, landing_page_url")
        .eq("competitor_id", competitor_id)
        .eq("status", "ACTIVE")
        .execute()
    )

    if not ads_res.data:
        log.info("No active ads for %s, skipping analysis", comp_name)
        return False

    # Get latest snapshot for each ad (for headline, body_text, start_date)
    ad_ids = [a["id"] for a in ads_res.data]
    snaps_res = (
        db.table("ad_snapshots")
        .select("ad_id, headline, body_text, start_date")
        .in_("ad_id", ad_ids)
        .order("captured_date", desc=True)
        .execute()
    )

    # Dedupe to latest snapshot per ad
    latest_snaps = {}
    for snap in snaps_res.data:
        if snap["ad_id"] not in latest_snaps:
            latest_snaps[snap["ad_id"]] = snap

    # Build ranked list by days_active
    ranked = []
    for ad in ads_res.data:
        snap = latest_snaps.get(ad["id"], {})
        days = _days_active(snap.get("start_date"), today)
        if days is None:
            continue
        ranked.append({
            "meta_ad_id": ad["meta_ad_id"],
            "headline": snap.get("headline") or "",
            "body_text": (snap.get("body_text") or "")[:300],
            "media_type": ad.get("media_type") or "unknown",
            "days_active": days,
        })

    ranked.sort(key=lambda x: x["days_active"], reverse=True)
    top_ads = ranked[:20]

    if not top_ads:
        log.info("No rankable ads for %s, skipping analysis", comp_name)
        return False

    # Build prompt
    ads_text = "\n".join(
        f"- [{a['meta_ad_id']}] {a['media_type']} | {a['days_active']}d active | "
        f"Headline: {a['headline'][:100]} | Body: {a['body_text'][:200]}"
        for a in top_ads
    )

    prompt = (
        f"Analyze these top ads for {comp_name}. What creative strategies are working? "
        f"What copy patterns appear in long-running ads? What's the overall ad strategy?\n\n"
        f"Ads (sorted by days active, most to least):\n{ads_text}\n\n"
        f"Use the save_analysis tool to record your analysis."
    )

    # Call Anthropic with tool_use
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=1024,
        tools=[ANALYSIS_TOOL],
        messages=[{"role": "user", "content": prompt}],
    )

    # Extract tool_use result
    tool_input = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "save_analysis":
            tool_input = block.input
            break

    if not tool_input:
        log.warning("LLM did not use save_analysis tool for %s", comp_name)
        return False

    # Map meta_ad_id -> DB UUID in top_ads
    meta_to_id = {a["meta_ad_id"]: a["id"] for a in ads_res.data}
    enriched_top_ads = []
    for item in tool_input.get("top_ads", []):
        ad_uuid = meta_to_id.get(item["meta_ad_id"])
        enriched_top_ads.append({
            "ad_id": ad_uuid,
            "meta_ad_id": item["meta_ad_id"],
            "reason": item["reason"],
        })

    # Upsert into competitor_analyses
    db.table("competitor_analyses").upsert({
        "competitor_id": competitor_id,
        "analysis_date": today.isoformat(),
        "summary": tool_input["summary"],
        "top_ads": enriched_top_ads,
        "strategy_tags": tool_input.get("strategy_tags", []),
    }, on_conflict="competitor_id,analysis_date").execute()

    log.info("Analysis saved for %s: %d top ads, %d tags",
             comp_name, len(enriched_top_ads), len(tool_input.get("strategy_tags", [])))
    return True
