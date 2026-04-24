"""Single-step LLM patch for scripted replay failures.

When the Playwright replay engine can't find the selector for step N, we hand
the live browser over to a browser-use Agent configured with `max_steps=1`.
The agent does exactly one decision — looks at the page, makes the matching
choice — and returns. The replay engine patches the corresponding entry in the
action log with whatever the agent did, bumps patch_count, and resumes scripted
playback on step N+1.
"""

from __future__ import annotations
import logging
import re
from typing import Any

from browser_use import Agent

from backend.config import get_llm
from backend.worker.strategies import build_single_step_patch_prompt

log = logging.getLogger(__name__)


async def patch_step(
    shared_browser: Any,
    action: dict,
    current_url: str | None = None,
) -> dict | None:
    """Run a 1-step browser-use Agent to recover from a failed scripted step.

    Parameters
    ----------
    shared_browser : browser_use.Browser
        Already attached to the live Chromium process driving the replay.
    action : dict
        The action-log entry that just failed.
    current_url : str | None
        The URL of the page as the replay engine saw it before handing off.

    Returns
    -------
    patched action dict, or None if the agent couldn't recover. The replay
    engine decides whether None means "skip" or "escalate".
    """
    prompt = build_single_step_patch_prompt(
        recorded_intent=action.get("action_description") or action.get("action_type", ""),
        recorded_question_text=action.get("question_text"),
        recorded_target_text=action.get("target_text"),
        recorded_input_value=action.get("input_value"),
        current_url=current_url,
    )

    log.info(
        "Invoking LLM patch for step %s (question=%r)",
        action.get("step_number"),
        action.get("question_text"),
    )

    try:
        agent = Agent(
            task=prompt,
            llm=get_llm(),
            browser=shared_browser,
            llm_timeout=120,
        )
        result = await agent.run(max_steps=1)
    except TypeError:
        # Older browser-use versions accept max_steps on the Agent, not run()
        agent = Agent(
            task=prompt,
            llm=get_llm(),
            browser=shared_browser,
            llm_timeout=120,
            max_steps=1,
        )
        result = await agent.run()
    except Exception as exc:
        log.warning("Patch agent failed for step %s: %s", action.get("step_number"), exc)
        return None

    memory = _extract_memory(result)
    patched = dict(action)

    target_text = _parse_target_from_memory(memory)
    input_value = _parse_input_from_memory(memory)

    if target_text:
        patched["target_text"] = target_text
        patched["action_type"] = "click"
    if input_value:
        patched["input_value"] = input_value
        patched["action_type"] = "fill"

    patched["action_description"] = memory.splitlines()[0][:200] if memory else patched.get("action_description")
    patched["patched"] = True

    if not target_text and not input_value:
        log.info("Patch completed but produced no new selector hint for step %s", action.get("step_number"))
    return patched


def _extract_memory(result: Any) -> str:
    try:
        history = getattr(result, "history", None)
        if history:
            last = history[-1]
            model_output = getattr(last, "model_output", None)
            if model_output and getattr(model_output, "memory", None):
                return model_output.memory
    except Exception:
        pass
    try:
        return "\n".join(s for s in result.extracted_content() if isinstance(s, str))
    except Exception:
        return ""


_QUOTE_RX = re.compile(r"['\"‘’“”]([^'\"‘’“”]{1,120})['\"‘’“”]")


def _parse_target_from_memory(memory: str) -> str | None:
    if not memory:
        return None
    for line in memory.splitlines():
        low = line.lower()
        if any(kw in low for kw in ("click", "select", "tap", "press")):
            m = _QUOTE_RX.search(line)
            if m:
                return m.group(1).strip()
    return None


def _parse_input_from_memory(memory: str) -> str | None:
    if not memory:
        return None
    for line in memory.splitlines():
        low = line.lower()
        if any(kw in low for kw in ("enter", "type", "fill", "input")):
            m = _QUOTE_RX.search(line)
            if m:
                return m.group(1).strip()
    return None
