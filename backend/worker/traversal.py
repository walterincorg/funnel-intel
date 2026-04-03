"""Browser-use funnel traversal engine."""

from __future__ import annotations
import asyncio
import json
import logging
import re

from browser_use import Agent, Browser, BrowserProfile
from backend.config import get_llm
from backend.worker.strategies import build_traversal_prompt, build_guided_prompt

log = logging.getLogger(__name__)


def _parse_json_lines(text: str) -> list[dict]:
    """Extract JSON objects from agent output text."""
    results = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        # Try to find JSON in the line
        match = re.search(r'\{.*\}', line)
        if match:
            try:
                results.append(json.loads(match.group()))
            except json.JSONDecodeError:
                continue
    return results


async def run_traversal(
    competitor_name: str,
    funnel_url: str,
    config: dict | None = None,
    baseline_steps: list[dict] | None = None,
) -> dict:
    """
    Run a funnel traversal and return structured results.

    Returns:
        {
            "steps": [...],
            "pricing": {...} or None,
            "summary": {...},
            "raw_output": str,
        }
    """
    if baseline_steps:
        prompt = build_guided_prompt(competitor_name, funnel_url, baseline_steps)
    else:
        prompt = build_traversal_prompt(competitor_name, funnel_url, config)

    browser = Browser(
        browser_profile=BrowserProfile(headless=True)
    )

    try:
        agent = Agent(task=prompt, llm=get_llm(), browser=browser)
        result = await agent.run()
        raw = str(result)
    finally:
        await browser.stop()

    # Parse structured output
    parsed = _parse_json_lines(raw)

    steps = []
    pricing = None
    summary = None

    for obj in parsed:
        if obj.get("summary"):
            summary = obj
        elif obj.get("step_type") == "pricing":
            pricing = obj
            steps.append(obj)
        elif "step_number" in obj:
            steps.append(obj)

    if not summary:
        summary = {
            "total_steps": len(steps),
            "stop_reason": "unknown",
        }

    return {
        "steps": steps,
        "pricing": pricing,
        "summary": summary,
        "raw_output": raw,
    }


def run_traversal_sync(
    competitor_name: str,
    funnel_url: str,
    config: dict | None = None,
    baseline_steps: list[dict] | None = None,
) -> dict:
    """Synchronous wrapper for run_traversal."""
    return asyncio.run(run_traversal(competitor_name, funnel_url, config, baseline_steps))
