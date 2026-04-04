"""Browser-use funnel traversal engine."""

from __future__ import annotations
import asyncio
import json
import logging
import os
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
        match = re.search(r'\{.*\}', line)
        if match:
            try:
                results.append(json.loads(match.group()))
            except json.JSONDecodeError:
                continue
    return results


def _extract_all_content(result) -> str:
    """Extract all text content from agent result for JSON parsing."""
    lines = []
    try:
        extracted = result.extracted_content()
        if extracted:
            for item in extracted:
                if isinstance(item, str):
                    lines.append(item)
    except Exception:
        pass
    return "\n".join(lines)


async def run_traversal(
    competitor_name: str,
    funnel_url: str,
    config: dict | None = None,
    baseline_steps: list[dict] | None = None,
    on_progress: callable | None = None,
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

    headless = os.getenv("BROWSER_HEADLESS", "true").lower() != "false"
    browser = Browser(
        browser_profile=BrowserProfile(
            headless=headless,
            chromium_sandbox=False,
            args=["--disable-dev-shm-usage", "--disable-gpu"],
            is_local=True,
            wait_for_network_idle_page_load_time=2.0,
            wait_between_actions=0.5,
        ),
    )

    try:
        agent = Agent(
            task=prompt,
            llm=get_llm(),
            browser=browser,
            llm_timeout=180,
        )
        result = await agent.run()
        raw = _extract_all_content(result)
    finally:
        await browser.stop()

    # Parse structured output from extracted content
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

        # Fire progress callback for each meaningful step
        if on_progress and "step_number" in obj and not obj.get("summary"):
            log_msg = obj.get("log")
            if not log_msg:
                parts = []
                if obj.get("question_text"):
                    parts.append(obj["question_text"])
                if obj.get("action_taken"):
                    parts.append(f"→ {obj['action_taken']}")
                log_msg = " ".join(parts) if parts else None
            if log_msg:
                on_progress({
                    "step": obj.get("step_number", 0),
                    "type": obj.get("step_type", "unknown"),
                    "message": log_msg,
                })

    # If no parsed steps, build steps from agent history as fallback
    if not steps:
        try:
            for i, item in enumerate(result.history, 1):
                model_output = item.model_output
                if model_output:
                    step = {
                        "step_number": i,
                        "step_type": "info",
                        "question_text": getattr(model_output, 'evaluation_previous_goal', None),
                        "action_taken": getattr(model_output, 'next_goal', None),
                        "log": getattr(model_output, 'memory', None),
                    }
                    # Get URL from action results
                    if item.result:
                        for r in item.result:
                            if hasattr(r, 'extracted_content') and r.extracted_content:
                                if '🔗 Navigated to' in r.extracted_content:
                                    step["url"] = r.extracted_content.replace('🔗 Navigated to ', '')
                    steps.append(step)

                    if on_progress and step.get("log"):
                        on_progress({
                            "step": i,
                            "type": "info",
                            "message": step["log"],
                        })
        except Exception as e:
            log.warning("Failed to extract steps from history: %s", e)

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
    on_progress: callable | None = None,
) -> dict:
    """Synchronous wrapper for run_traversal."""
    return asyncio.run(run_traversal(competitor_name, funnel_url, config, baseline_steps, on_progress))
