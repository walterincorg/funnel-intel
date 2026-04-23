"""Browser-use funnel traversal engine."""

from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import time
import tempfile
import shutil
from pathlib import Path

from browser_use import Agent, Browser, BrowserProfile
from backend.config import get_llm
from backend.worker.strategies import build_traversal_prompt, build_guided_prompt

log = logging.getLogger(__name__)

_PALM_IMAGE_PATH = Path(__file__).resolve().parents[1] / "assets" / "nebula_palm.png"


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
                obj = json.loads(match.group())
            except json.JSONDecodeError:
                continue
            # The LLM occasionally writes step_number as a string ("36").
            # Downstream sorts and diffs fail on mixed int/str keys.
            if "step_number" in obj:
                try:
                    obj["step_number"] = int(obj["step_number"])
                except (TypeError, ValueError):
                    continue
            results.append(obj)
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


def _parse_funnel_step_from_memory(memory: str, step_num: int) -> dict | None:
    """
    Try to extract step details from the agent's memory text for a given step number.

    The agent writes memory like:
      "Step 36: 'Got it! And what's your goal weight?' - entered 130 lbs."
    """
    # Match: Step N: 'Question' - action
    pattern = (
        r'[Ss]tep\s+' + str(step_num) + r':\s+["\u201c\u2018]([^"\u201d\u2019]+)["\u201d\u2019]'
        r'(?:\s*[-–]\s*(.{0,120}))?'
    )
    m = re.search(pattern, memory)
    if m:
        question = m.group(1).strip()
        action_hint = (m.group(2) or "").strip().rstrip('.,')
        return {
            "step_number": step_num,
            "step_type": "question",
            "question_text": question,
            "action_taken": action_hint or "completed",
            "log": f"Step {step_num}: {question}" + (f" → {action_hint}" if action_hint else ""),
        }
    return None


async def run_traversal(
    competitor_name: str,
    funnel_url: str,
    config: dict | None = None,
    baseline_steps: list[dict] | None = None,
    on_progress: callable | None = None,
    competitor_slug: str | None = None,
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
    available_file_paths: list[str] = []
    is_nebula = (
        (competitor_slug or "").lower() == "nebula"
        or "nebula" in (competitor_name or "").lower()
    )
    if is_nebula and _PALM_IMAGE_PATH.exists():
        available_file_paths.append(str(_PALM_IMAGE_PATH))

    if baseline_steps:
        # TODO: thread available_files into guided prompt too when Nebula baselines exist.
        prompt = build_guided_prompt(competitor_name, funnel_url, baseline_steps)
    else:
        prompt = build_traversal_prompt(
            competitor_name, funnel_url, config, available_files=available_file_paths,
        )

    log.info("Starting traversal for %s (%s) — mode=%s",
             competitor_name, funnel_url, "guided" if baseline_steps else "freeform")
    traversal_start = time.perf_counter()

    headless = os.getenv("BROWSER_HEADLESS", "true").lower() != "false"
    # Pydantic 2.12+ broke the field_validator that auto-creates user_data_dir,
    # so we must pass it explicitly. Each scan gets its own temp dir.
    _scan_user_data_dir = tempfile.mkdtemp(prefix="funnel-scan-")
    browser = Browser(
        browser_profile=BrowserProfile(
            headless=headless,
            chromium_sandbox=False,
            args=["--disable-dev-shm-usage", "--disable-gpu"],
            is_local=True,
            user_data_dir=_scan_user_data_dir,
            wait_for_network_idle_page_load_time=2.0,
            wait_between_actions=0.5,
        ),
    )

    # Per-step capture via callback — so we don't depend on the agent
    # calling extract_content for each step. The agent tracks progress in
    # its memory field; we read it after every LLM step.
    callback_steps: list[dict] = []
    _last_funnel_step: list[int] = [0]   # mutable for closure
    _last_memory: list[str] = [""]
    _last_url: list[str] = [funnel_url]

    def _step_callback(browser_state, agent_output, n_steps: int):
        memory = (agent_output.memory or "") if agent_output else ""
        url = browser_state.url if browser_state else _last_url[0]

        # 1. Try to parse structured JSON from memory first
        json_objs = _parse_json_lines(memory)
        new_json = [
            s for s in json_objs
            if "step_number" in s
            and not s.get("summary")
            and s["step_number"] > _last_funnel_step[0]
        ]
        if new_json:
            for s in sorted(new_json, key=lambda x: x["step_number"]):
                step_with_url = {**s, "url": s.get("url") or url}
                callback_steps.append(step_with_url)
                if on_progress:
                    msg = s.get("log") or s.get("question_text") or f"Step {s['step_number']}"
                    on_progress({"step": s["step_number"], "type": s.get("step_type", "unknown"), "message": msg})
                _last_funnel_step[0] = s["step_number"]
            _last_memory[0] = memory
            _last_url[0] = url
            return

        # 2. Text fallback: detect "Steps 1-N completed" transitions
        m = re.search(r'[Ss]teps?\s+1[-\u2013\u2014]\s*(\d+)\s+completed', memory)
        if m:
            completed_up_to = int(m.group(1))
            while _last_funnel_step[0] < completed_up_to:
                next_step = _last_funnel_step[0] + 1

                # Look for step details in the *previous* memory state
                # (when the step was active it was: "Step N: 'Q' - action")
                step_data = _parse_funnel_step_from_memory(_last_memory[0], next_step)
                if not step_data:
                    # Try current memory too (sometimes it mentions the completed step)
                    step_data = _parse_funnel_step_from_memory(memory, next_step)
                if not step_data:
                    step_data = {
                        "step_number": next_step,
                        "step_type": "question",
                        "question_text": None,
                        "action_taken": "completed",
                        "log": f"Step {next_step} completed",
                    }

                step_data["url"] = _last_url[0]
                callback_steps.append(step_data)
                if on_progress:
                    on_progress({"step": next_step, "type": "question", "message": step_data["log"]})
                _last_funnel_step[0] = next_step

        _last_memory[0] = memory
        _last_url[0] = url

    try:
        agent = Agent(
            task=prompt,
            llm=get_llm(),
            browser=browser,
            llm_timeout=180,
            register_new_step_callback=_step_callback,
            available_file_paths=available_file_paths or None,
        )
        result = await agent.run()
        raw = _extract_all_content(result)
    finally:
        await browser.stop()

    # Parse structured output from extracted content (highest quality, most structured)
    parsed = _parse_json_lines(raw)

    steps: list[dict] = []
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

        if on_progress and "step_number" in obj and not obj.get("summary"):
            log_msg = obj.get("log") or obj.get("question_text") or ""
            if log_msg:
                on_progress({
                    "step": obj.get("step_number", 0),
                    "type": obj.get("step_type", "unknown"),
                    "message": log_msg,
                })

    # Merge: prefer parsed (structured) steps; fill gaps with callback steps
    if steps:
        parsed_nums = {s["step_number"] for s in steps}
        for s in callback_steps:
            if s.get("step_number") not in parsed_nums:
                steps.append(s)
        steps.sort(key=lambda s: s.get("step_number", 0))
    elif callback_steps:
        # Nothing from extracted content — use what the callback captured
        steps = callback_steps
        # Also check if pricing was in memory
        for item in result.history if hasattr(result, 'history') else []:
            if item.model_output and item.model_output.memory:
                pricing_candidates = [
                    s for s in _parse_json_lines(item.model_output.memory)
                    if s.get("step_type") == "pricing"
                ]
                if pricing_candidates:
                    pricing = pricing_candidates[-1]
                    break

    if not summary:
        summary = {
            "total_steps": len(steps),
            "stop_reason": "unknown",
        }

    duration_ms = (time.perf_counter() - traversal_start) * 1000
    log.info("Traversal complete for %s: %d steps, stop=%s, pricing=%s (%.1fs)",
             competitor_name, len(steps), summary.get("stop_reason", "unknown"),
             "yes" if pricing else "no", duration_ms / 1000,
             extra={"step_count": len(steps), "duration_ms": round(duration_ms)})

    return {
        "steps": steps,
        "pricing": pricing,
        "summary": summary,
        "raw_output": raw,
    }


SCAN_TIMEOUT = 45 * 60  # 45 minutes — raise asyncio.TimeoutError if exceeded


def run_traversal_sync(
    competitor_name: str,
    funnel_url: str,
    config: dict | None = None,
    baseline_steps: list[dict] | None = None,
    on_progress: callable | None = None,
    competitor_slug: str | None = None,
) -> dict:
    """Synchronous wrapper for run_traversal."""
    coro = run_traversal(
        competitor_name, funnel_url, config, baseline_steps, on_progress, competitor_slug,
    )
    return asyncio.run(asyncio.wait_for(coro, timeout=SCAN_TIMEOUT))
