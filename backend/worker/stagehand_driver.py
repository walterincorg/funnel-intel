"""
Stagehand driver — replaces browser-use for funnel traversals.

Two modes:

* `run_record(...)` — first-ever scan for a competitor. Stagehand's autonomous
  `agent()` drives the funnel end-to-end; we walk the resulting action history,
  pair each action with a structured extract snapshot captured against the
  page state, and return both the traversal payload and a `Recipe` that
  describes how to replay it deterministically.

* `run_replay(recipe, ...)` — every subsequent scan. For each recorded step we
  call `page.extract(schema=...)` to grab question/options/pricing live, then
  `page.act(step.observe_result)` — zero autonomous-agent LLM calls. On
  failure we self-heal by calling `page.observe(step.intent)`; on repeated
  failure in one run we raise `RecipeBrokenError` and the caller falls back
  to `run_record`.

API NOTE: Written against the Stagehand Python SDK v3.19 surface
(env="LOCAL", stagehand.page, page.observe / page.act / page.extract,
stagehand.agent(instruction=...).execute()). The record path reads the
agent-result action history, whose exact field names (`selector`, `method`,
`arguments`, `description`) must be validated against the installed package
on first run — see `_coerce_observe_result` and `_iter_agent_actions`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from backend.worker.extract_schemas import (
    PricingStep,
    QuestionStep,
    schema_for,
)
from backend.worker.recipes import Recipe, RecipeStep

log = logging.getLogger(__name__)

# ---------- constants ----------

SCAN_TIMEOUT = 45 * 60
"""Hard ceiling for a whole traversal (record or replay), in seconds."""

MAX_SELF_HEAL_PER_RUN = 3
"""Abort replay and rewrite the recipe if self-heal fires more than this many times."""

SELF_HEAL_ACT_TIMEOUT = 20
"""Seconds to wait on a single replay act() before treating it as broken."""

RECORD_AGENT_MAX_STEPS_DEFAULT = 100


# ---------- exceptions ----------

class RecipeBrokenError(RuntimeError):
    """Raised when replay exhausts self-heal budget and the caller must re-record."""


# ---------- result types ----------

@dataclass
class TraversalResult:
    """Contract returned from both record and replay. Matches the shape the
    legacy browser-use `run_traversal(...)` returned so `loop.process_job`
    doesn't need to care which path ran."""

    steps: list[dict] = field(default_factory=list)
    pricing: Optional[dict] = None
    summary: dict = field(default_factory=dict)
    raw_output: str = ""
    mode: str = "replay"  # "record" | "replay" | "record_fallback"

    def as_dict(self) -> dict:
        return {
            "steps": self.steps,
            "pricing": self.pricing,
            "summary": self.summary,
            "raw_output": self.raw_output,
            "mode": self.mode,
        }


# ---------- Stagehand lifecycle ----------

def _model_name() -> str:
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    model = os.getenv("LLM_MODEL", "claude-opus-4-5")
    # Stagehand expects "provider/model". If LLM_MODEL already includes a
    # slash, pass it through.
    return model if "/" in model else f"{provider}/{model}"


def _model_api_key() -> str:
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_API_KEY", "")
    if provider == "openai":
        return os.getenv("OPENAI_API_KEY", "")
    return ""


async def _open_stagehand():
    """Construct and init a Stagehand session pointed at local Chromium."""
    # Imported lazily so unit tests can mock the module without a real install.
    from stagehand import Stagehand, StagehandConfig  # type: ignore

    headless_env = os.getenv("BROWSER_HEADLESS", "true").lower()
    headless = headless_env not in ("false", "0", "no")

    config = StagehandConfig(
        env="LOCAL",
        model_name=_model_name(),
        model_api_key=_model_api_key(),
        local_browser_launch_options={
            "headless": headless,
            "args": ["--disable-dev-shm-usage", "--disable-gpu"],
        },
    )
    stagehand = Stagehand(config=config)
    await stagehand.init()
    return stagehand


async def _close_stagehand(stagehand) -> None:
    try:
        await stagehand.close()
    except Exception:  # pragma: no cover — cleanup best-effort
        log.debug("Stagehand close raised; ignoring", exc_info=True)


# ---------- observe-result normalisation ----------

def _coerce_observe_result(raw: Any) -> Optional[dict]:
    """Normalise a Stagehand ObserveResult (object or dict) to a plain dict.

    The Python SDK has shipped as both a dataclass and a Pydantic model across
    versions; and the agent-action history sometimes embeds a similar shape
    under a different key. We try the usual fields and fall back to
    `model_dump()` / `__dict__`.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        d = raw
    elif hasattr(raw, "model_dump"):
        d = raw.model_dump()
    elif hasattr(raw, "__dict__"):
        d = dict(raw.__dict__)
    else:
        return None

    selector = d.get("selector") or d.get("xpath") or d.get("target")
    if not selector:
        return None

    method = d.get("method") or d.get("action") or "click"
    arguments = d.get("arguments") or d.get("args") or []
    description = d.get("description") or d.get("intent") or ""

    normalised = {
        "selector": selector,
        "method": method,
        "arguments": list(arguments),
        "description": description,
    }
    # Preserve playwright_arguments if the SDK provided them.
    if d.get("playwright_arguments") is not None:
        normalised["playwright_arguments"] = d["playwright_arguments"]
    return normalised


def _iter_agent_actions(agent_result: Any) -> list[dict]:
    """Pull an ordered list of normalised actions out of `agent.execute()` result.

    We look at `.actions`, then `.history`, then `.steps`; each entry is
    normalised via `_coerce_observe_result`. Entries without a selector are
    dropped — they're usually meta actions (navigate, wait, extract).
    """
    candidates: list[Any] = []
    for attr in ("actions", "history", "steps"):
        val = getattr(agent_result, attr, None)
        if val:
            candidates = list(val)
            break
    if not candidates and isinstance(agent_result, dict):
        for key in ("actions", "history", "steps"):
            if agent_result.get(key):
                candidates = list(agent_result[key])
                break

    actions: list[dict] = []
    for raw in candidates:
        normalised = _coerce_observe_result(raw)
        if normalised:
            actions.append(normalised)
    return actions


# ---------- extraction helpers ----------

def _schema_name(kind: Optional[str]) -> str:
    if kind in ("pricing", "discount"):
        return "pricing"
    return "question"


async def _safe_extract(page, kind: Optional[str]) -> Optional[dict]:
    """Run page.extract(schema=...) and return a dict, or None on failure.

    We swallow errors because extract is best-effort during replay — a blank
    page, a loading overlay, or a schema mismatch shouldn't kill the run.
    """
    if kind is None:
        return None
    schema_cls = schema_for(kind)
    try:
        extracted = await page.extract(schema=schema_cls)
    except Exception:
        log.exception("extract() failed for kind=%s", kind)
        return None
    if extracted is None:
        return None
    # Stagehand returns either a dict or an instance of the schema class.
    if isinstance(extracted, dict):
        return extracted
    if hasattr(extracted, "model_dump"):
        return extracted.model_dump()
    return None


def _infer_extract_kind(action: dict, url: str) -> str:
    """Guess whether the page *before* this action was a pricing screen or a
    question screen, based on the action's natural-language description and
    the URL path. Used at record time to tag recipe steps.
    """
    haystack = f"{action.get('description', '')} {url}".lower()
    if any(word in haystack for word in ("checkout", "payment", "pay", "subscribe", "plan", "pricing", "trial")):
        return "pricing"
    if any(word in haystack for word in ("type ", "enter ", "fill ", "input")):
        return "input"
    return "question"


def _infer_stop_reason(final_url: str, summary_text: str) -> str:
    hay = f"{final_url} {summary_text}".lower()
    if "checkout" in hay or "payment" in hay or "paywall" in hay:
        return "paywall"
    if "thank" in hay or "success" in hay:
        return "end_of_funnel"
    return "unknown"


# ---------- RECORD ----------

ProgressCb = Optional[Callable[[dict], None]]


async def run_record(
    funnel_url: str,
    competitor_name: str,
    config: dict | None,
    available_files: list[str] | None,
    on_progress: ProgressCb = None,
    competitor_id: Optional[str] = None,
) -> tuple[TraversalResult, Recipe]:
    """Drive the first scan with Stagehand's autonomous agent, capturing a
    replayable recipe as a side-effect.
    """
    from backend.worker.strategies import build_traversal_prompt  # local import avoids cycle

    prompt = build_traversal_prompt(
        competitor_name, funnel_url, config, available_files=available_files,
    )

    log.info("RECORD start for %s (%s)", competitor_name, funnel_url)
    t0 = time.perf_counter()

    stagehand = await _open_stagehand()
    try:
        page = stagehand.page
        await page.goto(funnel_url)

        # The agent is the LLM-driven traversal. We let it run to completion.
        agent = stagehand.agent(instruction=prompt)
        agent_result = await agent.execute()

        final_url = ""
        try:
            final_url = page.url  # Stagehand mirrors Playwright here.
        except Exception:
            pass

        # Best-effort final pricing pull — most funnels land on a pricing page.
        final_pricing = await _safe_extract(page, "pricing")

        actions = _iter_agent_actions(agent_result)
        summary_text = getattr(agent_result, "message", "") or str(getattr(agent_result, "output", "") or "")

    finally:
        await _close_stagehand(stagehand)

    # Build the recipe and the step payload side-by-side. Each action captured
    # by the agent becomes a recipe step; we tag the extract_kind heuristically.
    recipe_steps: list[RecipeStep] = []
    traversal_steps: list[dict] = []

    for idx, action in enumerate(actions, start=1):
        kind = _infer_extract_kind(action, funnel_url)
        recipe_steps.append(RecipeStep(
            step_number=idx,
            intent=action.get("description") or f"step {idx}",
            observe_result=action,
            extract_kind=kind,
            expected_url_pattern=None,
        ))
        traversal_steps.append({
            "step_number": idx,
            "step_type": kind,
            "question_text": None,
            "answer_options": [],
            "action_taken": action.get("description") or action.get("method"),
            "url": funnel_url,  # URL per-step not available post-hoc; replay fills these in.
            "log": f"Recorded step {idx}: {action.get('description') or action.get('method')}",
        })
        if on_progress:
            on_progress({
                "step": idx,
                "type": kind,
                "message": f"Recorded step {idx}: {action.get('description') or action.get('method')}",
            })

    # Tack on the final pricing snapshot if we got one.
    if final_pricing:
        traversal_steps.append({
            "step_number": len(actions) + 1,
            "step_type": "pricing",
            **final_pricing,
            "url": final_url or funnel_url,
            "log": "Captured final pricing screen",
        })

    stop_reason = _infer_stop_reason(final_url, summary_text)
    summary = {
        "total_steps": len(traversal_steps),
        "stop_reason": stop_reason,
    }

    recipe = Recipe(
        competitor_id=competitor_id or "00000000-0000-0000-0000-000000000000",  # caller overwrites
        version=1,
        start_url=funnel_url,
        steps=recipe_steps,
        stop_reason=stop_reason,
    )

    duration_ms = (time.perf_counter() - t0) * 1000
    log.info(
        "RECORD complete for %s: %d actions, stop=%s (%.1fs)",
        competitor_name, len(actions), stop_reason, duration_ms / 1000,
    )

    return (
        TraversalResult(
            steps=traversal_steps,
            pricing=final_pricing,
            summary=summary,
            raw_output=summary_text,
            mode="record",
        ),
        recipe,
    )


# ---------- REPLAY ----------

async def _self_heal_step(page, step: RecipeStep) -> tuple[bool, Optional[dict]]:
    """Try to recover a failing replay step. On success returns (True, new_observe).
    On failure returns (False, None). Does not mutate the step — caller does.
    """
    log.warning("Self-heal attempt for step %d: %s", step.step_number, step.intent)

    # Attempt 1: re-observe with the original intent, retry act with fresh selector.
    try:
        fresh = await page.observe(step.intent)
        if fresh:
            candidate = _coerce_observe_result(fresh[0])
            if candidate:
                await page.act(candidate)
                return True, candidate
    except Exception:
        log.debug("Self-heal observe/act failed", exc_info=True)

    # Attempt 2: single-shot LLM act, then capture the selector it used post-hoc.
    try:
        await page.act(step.intent)
        # Capture a fresh observe so the recipe stays deterministic next time.
        try:
            post = await page.observe(step.intent)
            if post:
                coerced = _coerce_observe_result(post[0])
                if coerced:
                    return True, coerced
        except Exception:
            pass
        # Act succeeded but we couldn't re-observe — keep the old selector,
        # we'll heal again next time if needed.
        return True, None
    except Exception:
        log.exception("Self-heal escalation act() failed for step %d", step.step_number)

    return False, None


async def run_replay(
    recipe: Recipe,
    on_progress: ProgressCb = None,
) -> tuple[TraversalResult, Recipe, bool]:
    """
    Replay a recorded recipe. Returns:

        (result, maybe_updated_recipe, recipe_dirty)

    If `recipe_dirty` is True the caller should persist the updated recipe
    (version bumped, old one retired). If replay can't be completed inside
    MAX_SELF_HEAL_PER_RUN escalations, RecipeBrokenError is raised.
    """
    log.info("REPLAY start (version=%d, %d steps, %s)",
             recipe.version, len(recipe.steps), recipe.start_url)
    t0 = time.perf_counter()

    traversal_steps: list[dict] = []
    final_pricing: Optional[dict] = None
    recipe_dirty = False
    heal_count = 0

    stagehand = await _open_stagehand()
    try:
        page = stagehand.page
        await page.goto(recipe.start_url)

        # Walk the recipe step-by-step.
        for step in recipe.steps:
            # 1. Extract live data *before* acting (captures question/options
            #    that the about-to-be-clicked page is showing).
            extracted = await _safe_extract(page, step.extract_kind)
            current_url = ""
            try:
                current_url = page.url
            except Exception:
                pass

            step_row: dict = {
                "step_number": step.step_number,
                "step_type": step.extract_kind or "info",
                "action_taken": step.intent,
                "url": current_url or recipe.start_url,
                "log": f"Replayed step {step.step_number}: {step.intent}",
            }
            if extracted:
                step_row.update(extracted)

            if step.extract_kind in ("pricing", "discount") and extracted:
                final_pricing = extracted

            # 2. Execute the cached observe result — no LLM.
            try:
                await asyncio.wait_for(
                    page.act(step.observe_result),
                    timeout=SELF_HEAL_ACT_TIMEOUT,
                )
            except Exception:
                # 3. Self-heal path.
                heal_count += 1
                if heal_count > MAX_SELF_HEAL_PER_RUN:
                    raise RecipeBrokenError(
                        f"Exceeded self-heal budget at step {step.step_number}"
                    )
                ok, new_observe = await _self_heal_step(page, step)
                if not ok:
                    raise RecipeBrokenError(
                        f"Self-heal failed at step {step.step_number}: {step.intent}"
                    )
                if new_observe:
                    step.observe_result = new_observe
                    recipe_dirty = True
                    step_row["log"] += "  (self-healed)"

            traversal_steps.append(step_row)
            if on_progress:
                on_progress({
                    "step": step.step_number,
                    "type": step_row["step_type"],
                    "message": step_row["log"],
                })

        # After the last recorded action, one final extract in case the funnel
        # lands on a pricing screen that wasn't tagged mid-flow.
        if not final_pricing:
            tail = await _safe_extract(page, "pricing")
            if tail and (tail.get("plans") or tail.get("discounts") or tail.get("trial_info")):
                final_pricing = tail
                traversal_steps.append({
                    "step_number": len(recipe.steps) + 1,
                    "step_type": "pricing",
                    **tail,
                    "url": getattr(page, "url", recipe.start_url),
                    "log": "Captured pricing screen after last replay step",
                })

    finally:
        await _close_stagehand(stagehand)

    summary = {
        "total_steps": len(traversal_steps),
        "stop_reason": recipe.stop_reason or "end_of_replay",
    }

    duration_ms = (time.perf_counter() - t0) * 1000
    log.info(
        "REPLAY complete: %d steps, heals=%d, dirty=%s (%.1fs)",
        len(traversal_steps), heal_count, recipe_dirty, duration_ms / 1000,
    )

    result = TraversalResult(
        steps=traversal_steps,
        pricing=final_pricing,
        summary=summary,
        raw_output="",
        mode="replay",
    )
    # Return a possibly-dirty copy for the caller to save.
    updated = recipe.model_copy(update={"steps": recipe.steps}) if recipe_dirty else recipe
    return result, updated, recipe_dirty
