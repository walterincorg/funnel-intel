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

from browser_use import Agent, Browser, BrowserProfile, Tools
from browser_use.agent.views import ActionResult
from pydantic import BaseModel, Field
from backend.config import get_llm
from backend.worker.strategies import build_traversal_prompt, build_guided_prompt

log = logging.getLogger(__name__)

_PALM_IMAGE_PATH = Path(__file__).resolve().parents[1] / "assets" / "nebula_palm.png"


# --- Pydantic param models for custom actions ---

class ClickByTextParams(BaseModel):
    text: str = Field(description="Visible text on the element to click (e.g. 'Mid-sized', 'Yes', 'Continue')")


class FillInputParams(BaseModel):
    selector: str = Field(description="CSS selector for the input. Common: 'input[type=email]', 'input[name=age]'")
    value: str = Field(description="Value to fill (e.g. 'jane.doe@example.com', '30')")


def _build_tools() -> Tools:
    """Build a Tools instance with coordinate clicking + locator-based fallbacks.

    These extra actions exist because browser-use's DOM heuristics sometimes
    miss interactable elements on custom React widgets (BetterMe answer
    buttons, custom-styled email inputs, etc.). When that happens, the
    indexed click/input actions can't find the target. Coordinate clicking
    and CSS-selector fallbacks let the LLM still complete the funnel.
    """
    tools = Tools()

    # 1. Enable built-in coordinate clicking. Once enabled, the click action
    #    accepts coordinate_x / coordinate_y from the LLM screenshot. browser-use
    #    auto-rescales coords to viewport.
    try:
        tools.set_coordinate_clicking(True)
    except Exception as e:
        log.warning("set_coordinate_clicking unavailable: %s", e)

    # 2. Custom action: click any element by visible text. Bypasses the indexed
    #    element list entirely. Works when browser-use only saw a Continue
    #    button but the page actually has answer-option buttons.
    @tools.registry.action(
        "Click any visible element by its text content. Use ONLY when the indexed "
        "click action can't reach the element (e.g. no index for an answer option). "
        "Tries button/role=button/label/div with the given text, in that order.",
        param_model=ClickByTextParams,
    )
    async def click_by_text(params: ClickByTextParams, browser_session):
        page = await browser_session.get_current_page()
        if not page:
            return ActionResult(error="No active page")
        # Try multiple selector patterns; first match wins.
        text = params.text.replace('"', '\\"')
        candidates = [
            f'button:has-text("{text}")',
            f'[role="button"]:has-text("{text}")',
            f'label:has-text("{text}")',
            f'div[role]:has-text("{text}")',
            f'a:has-text("{text}")',
            f'*:has-text("{text}")',  # last resort
        ]
        for sel in candidates:
            try:
                elements = await page.get_elements_by_css_selector(sel)
                if elements:
                    await elements[0].click()
                    msg = f"Clicked element matching '{params.text}' via selector {sel}"
                    log.info("[click_by_text] %s", msg)
                    return ActionResult(extracted_content=msg)
            except Exception as e:
                log.debug("[click_by_text] selector %s failed: %s", sel, e)
                continue
        return ActionResult(error=f"No clickable element with text '{params.text}' found")

    # 3. Custom action: fill an input by CSS selector. Bypasses the indexed
    #    element list. Works for email/text inputs that browser-use missed.
    @tools.registry.action(
        "Fill a form input by CSS selector. Use ONLY when the indexed input action "
        "can't reach the field (e.g. no index for an email/text input). "
        "Common selectors: 'input[type=email]', 'input[name=age]', 'input[placeholder*=name]'.",
        param_model=FillInputParams,
    )
    async def fill_input(params: FillInputParams, browser_session):
        page = await browser_session.get_current_page()
        if not page:
            return ActionResult(error="No active page")
        try:
            elements = await page.get_elements_by_css_selector(params.selector)
            if not elements:
                return ActionResult(error=f"No element matched selector {params.selector}")
            await elements[0].fill(params.value)
            msg = f"Filled '{params.value[:40]}' into {params.selector}"
            log.info("[fill_input] %s", msg)
            return ActionResult(extracted_content=msg)
        except Exception as e:
            return ActionResult(error=f"fill_input failed: {e}")

    return tools


def _parse_json_lines(text: str) -> list[dict]:
    """Extract JSON objects from agent output text.

    Handles three formats:
    1. One JSON object per line (legacy prompt format).
    2. A single JSON object spanning the whole text (newer prompt: done.text is JSON).
    3. JSON embedded inside markdown ```json fences.
    """
    results: list[dict] = []
    if not text:
        return results

    def _normalize(obj: dict) -> dict | None:
        # The LLM occasionally writes step_number as a string ("36").
        if "step_number" in obj:
            try:
                obj["step_number"] = int(obj["step_number"])
            except (TypeError, ValueError):
                return None
        return obj

    # Strip markdown code fences first (```json ... ```).
    cleaned = re.sub(r'^```(?:json)?\s*\n', '', text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r'\n```\s*$', '', cleaned, flags=re.MULTILINE)

    # 2. Try whole-text-as-JSON first (matches the new done.text prompt).
    stripped = cleaned.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                normalized = _normalize(obj)
                if normalized is not None:
                    results.append(normalized)
                    return results  # Whole-blob match wins
        except json.JSONDecodeError:
            pass

    # 1. Fall back to line-by-line parsing.
    for line in cleaned.split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        match = re.search(r'\{.*\}', line)
        if not match:
            continue
        try:
            obj = json.loads(match.group())
        except json.JSONDecodeError:
            continue
        normalized = _normalize(obj)
        if normalized is not None:
            results.append(normalized)
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
    traversal_model: str | None = None,
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

    log.info("Starting traversal for %s (%s) — mode=%s model=%s",
             competitor_name, funnel_url, "guided" if baseline_steps else "freeform",
             traversal_model or "env-default")
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
        next_goal = (agent_output.next_goal or "") if agent_output else ""
        eval_prev = (agent_output.evaluation_previous_goal or "") if agent_output else ""
        actions = list(agent_output.action) if agent_output and agent_output.action else []
        url = browser_state.url if browser_state else _last_url[0]

        # 1. Try to parse structured JSON from memory first (best signal — has
        # step_type, answer_options, pricing fields)
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
                step_data = _parse_funnel_step_from_memory(_last_memory[0], next_step) \
                    or _parse_funnel_step_from_memory(memory, next_step) \
                    or {
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
            return

        # 3. Always-on fallback: capture every browser-use step using agent_output
        # and browser_state directly. Doesn't depend on the model emitting any
        # specific JSON / memory format — works on any model. Fires once per
        # browser-use step. Smarter models will overwrite via path 1 above.
        if n_steps and n_steps > _last_funnel_step[0]:
            # Compose a short human log from what we have.
            action_summary = ""
            if actions:
                first = actions[0]
                if hasattr(first, "model_dump"):
                    d = first.model_dump(exclude_none=True)
                    if d:
                        name = next(iter(d.keys()))
                        params = d[name] if isinstance(d[name], dict) else {}
                        if name == "click" and "index" in params:
                            action_summary = f"clicked element {params['index']}"
                        elif name == "input_text" and "text" in params:
                            txt = str(params['text'])[:40]
                            action_summary = f"entered '{txt}'"
                        elif name == "scroll":
                            action_summary = "scrolled"
                        elif name == "go_to_url" and "url" in params:
                            action_summary = f"navigated to {params['url'][:60]}"
                        elif name == "done":
                            action_summary = "marked done"
                        else:
                            action_summary = name
            log_msg = (next_goal[:90] or eval_prev[:90] or memory[:90] or "step")
            if action_summary:
                log_msg = f"{log_msg} → {action_summary}"
            step_data = {
                "step_number": n_steps,
                "step_type": "question",
                "question_text": next_goal[:200] if next_goal else None,
                "action_taken": action_summary or "completed",
                "log": log_msg,
                "url": url,
            }
            callback_steps.append(step_data)
            if on_progress:
                on_progress({"step": n_steps, "type": "question", "message": log_msg})
            _last_funnel_step[0] = n_steps

        _last_memory[0] = memory
        _last_url[0] = url

    try:
        agent = Agent(
            task=prompt,
            llm=get_llm(traversal_model=traversal_model),
            browser=browser,
            tools=_build_tools(),
            llm_timeout=180,
            register_new_step_callback=_step_callback,
            available_file_paths=available_file_paths or None,
        )
        # Cap browser-use iterations. Real funnels are 25-50 question steps,
        # but the agent may burn extra cycles on retries / overlays. 250 gives
        # 5x headroom while still catching runaway loops within ~30-40 min.
        result = await agent.run(max_steps=250)
        raw = _extract_all_content(result)
    finally:
        try:
            await asyncio.wait_for(browser.stop(), timeout=15)
        except (asyncio.TimeoutError, Exception):
            log.warning("browser.stop() timed out or errored — Chrome process may be orphaned")
        # Clean up per-scan user data dir regardless of how the browser exited
        try:
            shutil.rmtree(_scan_user_data_dir, ignore_errors=True)
        except Exception:
            pass

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
            # Pricing JSON now embeds the stop_reason (from the new done.text format).
            # Promote it to a synthetic summary so downstream code (PR body, dashboard)
            # gets the right stop_reason instead of falling back to "unknown".
            if obj.get("stop_reason") and not summary:
                summary = {
                    "summary": True,
                    "total_steps": obj.get("total_steps"),
                    "stop_reason": obj.get("stop_reason"),
                }
            if "step_number" in obj:
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
    traversal_model: str | None = None,
) -> dict:
    """Synchronous wrapper for run_traversal."""
    coro = run_traversal(
        competitor_name, funnel_url, config, baseline_steps, on_progress, competitor_slug, traversal_model,
    )
    return asyncio.run(asyncio.wait_for(coro, timeout=SCAN_TIMEOUT))
