"""Convert captured traversal steps into a replay-able action log.

The primary D1 path parses a Playwright trace.zip produced during the first-run
LLM traversal. Since browser-use drives Chromium directly via CDP and attaching
tracing to its own Playwright context is a validated-on-the-fly concern, we
also support the documented fallback: derive the action log from the step
records the traversal callback already captures.

Action log schema (one entry per recorded step):

    {
      "step_number": int,
      "step_type": "question|input|info|pricing|discount|unknown",
      "action_type": "click|fill|navigate|extract|stop",
      "target_text": str | None,        # button/option label to re-click
      "selector": str | None,           # CSS selector if known (from trace)
      "selector_strategy": "css|text|role|label|auto",
      "input_value": str | None,        # for fill actions
      "url_before": str | None,
      "url_after": str | None,
      "question_text": str | None,
      "action_description": str | None, # human-readable, mirrors action_taken
    }
"""

from __future__ import annotations
import json
import logging
import re
import zipfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# Regex helpers for parsing the loose "action_taken" strings the agent emits.
# Examples we've seen in the wild:
#   "clicked 'Lose Weight'"
#   "Selected “30-39”"
#   "entered 'jane.doe@example.com' in email"
#   "filled age with 34"
#   "typed 165 into weight field"
_QUOTE = r"['\"‘’“”]"
_CLICK_RE = re.compile(
    rf"(?:click(?:ed|s)?|select(?:ed|s)?|tap(?:ped|s)?|press(?:ed|es)?)\s+(?:on\s+)?{_QUOTE}([^'\"‘’“”]{{1,120}}){_QUOTE}",
    re.IGNORECASE,
)
_FILL_QUOTED_RE = re.compile(
    rf"(?:enter(?:ed|s)?|typ(?:ed|es)?|input(?:ted|s)?|fill(?:ed|s)?)\s+{_QUOTE}([^'\"‘’“”]{{0,200}}){_QUOTE}",
    re.IGNORECASE,
)
_FILL_BARE_RE = re.compile(
    r"(?:enter(?:ed|s)?|typ(?:ed|es)?|input(?:ted|s)?|fill(?:ed|s)?)\s+([A-Za-z0-9@._\-+/]{1,80})\s+(?:in|into|as|for)\s+",
    re.IGNORECASE,
)
_FILL_WITH_RE = re.compile(
    r"(?:fill(?:ed|s)?)\s+\w+\s+with\s+([A-Za-z0-9@._\-+/]{1,80})",
    re.IGNORECASE,
)


def _infer_action_type(step: dict) -> str:
    step_type = (step.get("step_type") or "").lower()
    if step_type == "pricing":
        return "extract"
    action = (step.get("action_taken") or "").lower()
    if step_type == "input" or any(kw in action for kw in ("enter", "typ", "fill", "input")):
        return "fill"
    if any(kw in action for kw in ("navigate", "go to", "visit")):
        return "navigate"
    return "click"


def _extract_target_text(action: str | None) -> str | None:
    if not action:
        return None
    m = _CLICK_RE.search(action)
    if m:
        return m.group(1).strip()
    return None


def _extract_input_value(action: str | None) -> str | None:
    if not action:
        return None
    for rx in (_FILL_QUOTED_RE, _FILL_BARE_RE, _FILL_WITH_RE):
        m = rx.search(action)
        if m:
            return m.group(1).strip()
    return None


def steps_to_action_log(steps: list[dict]) -> list[dict]:
    """Convert the traversal's captured steps into a replay action log.

    We don't have CSS selectors (browser-use hides them behind DOM indices)
    so the replay engine relies on target_text + question_text with Playwright
    text/role/label locators. That's brittle on its own, which is why the
    patch path exists.
    """
    if not steps:
        return []

    ordered = sorted(
        (s for s in steps if s.get("step_number") is not None),
        key=lambda s: s["step_number"],
    )
    action_log: list[dict] = []
    for i, step in enumerate(ordered):
        next_url = ordered[i + 1].get("url") if i + 1 < len(ordered) else None
        action_type = _infer_action_type(step)
        action_taken = step.get("action_taken")
        entry = {
            "step_number": step.get("step_number"),
            "step_type": step.get("step_type") or "unknown",
            "action_type": action_type,
            "target_text": _extract_target_text(action_taken),
            "selector": None,
            "selector_strategy": "auto",
            "input_value": _extract_input_value(action_taken) if action_type == "fill" else None,
            "url_before": step.get("url"),
            "url_after": next_url,
            "question_text": step.get("question_text"),
            "action_description": action_taken,
        }
        action_log.append(entry)
    return action_log


# ---------------------------------------------------------------------------
# Playwright trace.zip parser — used when tracing was attached to the shared
# Playwright context. The trace bundle contains a .trace NDJSON file with
# per-action events. We extract the richest selector we can find per event.
# ---------------------------------------------------------------------------

_PLAYWRIGHT_ACTION_TYPES = {
    "click": "click",
    "dblclick": "click",
    "fill": "fill",
    "type": "fill",
    "press": "click",
    "check": "click",
    "uncheck": "click",
    "selectOption": "fill",
    "goto": "navigate",
}


def parse_trace_zip(trace_zip: Path) -> list[dict]:
    """Parse a Playwright trace bundle into a flat action list.

    Returns an empty list if the trace file is missing or malformed; callers
    should fall back to steps_to_action_log().
    """
    if not trace_zip.exists():
        return []

    events: list[dict] = []
    try:
        with zipfile.ZipFile(trace_zip) as zf:
            candidates = [n for n in zf.namelist() if n.endswith(".trace")]
            for name in candidates:
                with zf.open(name) as fh:
                    for raw_line in fh:
                        line = raw_line.decode("utf-8", errors="ignore").strip()
                        if not line:
                            continue
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
    except (zipfile.BadZipFile, OSError) as exc:
        log.warning("Failed to read trace.zip %s: %s", trace_zip, exc)
        return []

    actions: list[dict] = []
    url_before = None
    step_number = 0
    for event in events:
        ev_type = event.get("type")
        if ev_type in ("frame-navigated", "navigated"):
            url_before = event.get("url") or url_before
            continue
        if ev_type != "action":
            continue

        apiName = event.get("apiName") or event.get("method") or ""
        short = apiName.split(".")[-1] if apiName else ""
        action_type = _PLAYWRIGHT_ACTION_TYPES.get(short)
        if not action_type:
            continue

        selector = event.get("selector") or event.get("params", {}).get("selector")
        params = event.get("params") or {}
        value = params.get("value") or params.get("text")
        step_number += 1
        actions.append({
            "step_number": step_number,
            "step_type": "input" if action_type == "fill" else "question",
            "action_type": action_type,
            "target_text": None,
            "selector": selector,
            "selector_strategy": "css" if selector else "auto",
            "input_value": value if action_type == "fill" else None,
            "url_before": url_before,
            "url_after": event.get("afterSnapshot") and url_before,
            "question_text": None,
            "action_description": f"{short} {selector or ''}".strip(),
        })
    return actions


def merge_trace_and_steps(
    trace_actions: list[dict],
    step_actions: list[dict],
) -> list[dict]:
    """Prefer step-derived entries (they carry question_text + human descriptions)
    but enrich them with selectors from the trace when the order lines up.
    """
    if not step_actions:
        return trace_actions
    if not trace_actions:
        return step_actions

    merged: list[dict] = []
    ti = 0
    for step in step_actions:
        while ti < len(trace_actions) and trace_actions[ti]["action_type"] not in (
            step["action_type"],
            "click",
            "fill",
        ):
            ti += 1
        if ti < len(trace_actions):
            trace = trace_actions[ti]
            merged.append({
                **step,
                "selector": step.get("selector") or trace.get("selector"),
                "selector_strategy": trace.get("selector_strategy") or step.get("selector_strategy"),
                "input_value": step.get("input_value") or trace.get("input_value"),
            })
            ti += 1
        else:
            merged.append(step)
    return merged


# ---------------------------------------------------------------------------
# Cost estimation — exposed on the recording so the dashboard can render the
# cost-delta card promised in the preview mockup.
# ---------------------------------------------------------------------------

FULL_LLM_COST_USD = 14.00  # baseline full-traversal cost (claude-opus-4-5)
LLM_PATCH_COST_USD = 0.30  # one browser-use step on opus
HAIKU_PRICING_COST_USD = 0.01


def estimate_replay_cost(patch_count: int, has_pricing: bool) -> dict[str, Any]:
    patches = LLM_PATCH_COST_USD * patch_count
    haiku = HAIKU_PRICING_COST_USD if has_pricing else 0.0
    total = round(patches + haiku, 2)
    saved = round(max(0.0, FULL_LLM_COST_USD - total), 2)
    pct = round((saved / FULL_LLM_COST_USD) * 100, 1) if FULL_LLM_COST_USD else 0.0
    return {
        "total_usd": total,
        "baseline_usd": FULL_LLM_COST_USD,
        "saved_usd": saved,
        "saved_pct": pct,
        "patches": patch_count,
        "patch_cost_usd": round(patches, 2),
        "pricing_extract_usd": haiku,
    }
