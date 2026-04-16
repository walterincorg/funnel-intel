"""Compare a new scan run against a baseline and detect changes.

Uses LLM-based semantic comparison via Haiku. Aligns steps by topic,
classifies rewording as cosmetic, and flags A/B-tested option lists
as low severity instead of false positives.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
from dotenv import dotenv_values

log = logging.getLogger(__name__)

DIFF_MODEL = os.getenv("DIFF_MODEL", "claude-haiku-4-5-20251001")

# Read API key from .env directly — os.getenv may be empty when the worker
# was started with `env -u ANTHROPIC_API_KEY` to let dotenv handle config.py,
# but load_dotenv(override=False) won't overwrite the now-empty var.
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
_DOTENV = dotenv_values(_ENV_PATH) if _ENV_PATH.exists() else {}
_API_KEY = os.getenv("ANTHROPIC_API_KEY") or _DOTENV.get("ANTHROPIC_API_KEY", "")


@dataclass
class Change:
    severity: str  # critical, high, medium, low
    category: str  # funnel, pricing, structural
    step_number: int | None
    description: str


@dataclass
class DiffResult:
    changes: list[Change] = field(default_factory=list)
    drift_level: str = "none"  # none, minor, major

    @property
    def has_changes(self) -> bool:
        return len(self.changes) > 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deduplicate_steps(steps: list[dict]) -> list[dict]:
    """Keep one row per step_number — the one with the longest question_text."""
    seen: dict[int, dict] = {}
    for s in steps:
        num = s.get("step_number")
        if num is None:
            continue
        if not isinstance(num, int):
            try:
                num = int(num)
            except (ValueError, TypeError):
                continue
        existing = seen.get(num)
        if existing is None or len(s.get("question_text") or "") > len(existing.get("question_text") or ""):
            s = dict(s, step_number=num)
            seen[num] = s
    return sorted(seen.values(), key=lambda s: s.get("step_number", 0))


def _normalize_price(price) -> str:
    if price is None:
        return ""
    return str(price).strip().lstrip("$").strip()


def _plan_key(plan: dict) -> str:
    return " ".join(str(plan.get("name", "")).strip().lower().split())


# ---------------------------------------------------------------------------
# Pricing diff (string comparison is fine for concrete numbers)
# ---------------------------------------------------------------------------

def _diff_pricing(base: dict, new: dict, result: DiffResult):
    base_plans = {_plan_key(p): p for p in (base.get("plans") or []) if _plan_key(p)}
    new_plans = {_plan_key(p): p for p in (new.get("plans") or []) if _plan_key(p)}

    for key in set(base_plans.keys()) | set(new_plans.keys()):
        bp = base_plans.get(key)
        np = new_plans.get(key)
        display_name = (np or bp or {}).get("name", key)

        if bp and not np:
            result.changes.append(Change("high", "pricing", None, f"Plan '{display_name}' removed"))
        elif np and not bp:
            price = np.get("price", "?")
            result.changes.append(Change("high", "pricing", None, f"New plan '{display_name}' at {price}"))
        elif bp and np:
            old_price = _normalize_price(bp.get("price"))
            new_price = _normalize_price(np.get("price"))
            if old_price and new_price and old_price != new_price:
                result.changes.append(Change(
                    "high", "pricing", None,
                    f"Plan '{display_name}' price changed: {bp.get('price')} -> {np.get('price')}",
                ))

    base_discounts = base.get("discounts") or []
    new_discounts = new.get("discounts") or []
    if base_discounts != new_discounts:
        if new_discounts and not base_discounts:
            result.changes.append(Change("high", "pricing", None, "New discount(s) detected"))
        elif base_discounts and not new_discounts:
            result.changes.append(Change("high", "pricing", None, "Discount(s) removed"))
        else:
            result.changes.append(Change("high", "pricing", None, "Discount details changed"))


# ---------------------------------------------------------------------------
# Semantic differ (LLM-based via Haiku tool_use)
# ---------------------------------------------------------------------------

DIFF_TOOL = {
    "name": "save_diff_result",
    "description": "Save the structured diff comparing baseline and latest funnel steps.",
    "input_schema": {
        "type": "object",
        "properties": {
            "alignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "baseline_step": {
                            "type": ["integer", "null"],
                            "description": "Baseline step number. null if this is a NEW step only in latest.",
                        },
                        "latest_step": {
                            "type": ["integer", "null"],
                            "description": "Latest step number. null if this step was REMOVED.",
                        },
                        "question_verdict": {
                            "type": "string",
                            "enum": ["SAME", "COSMETIC", "DIFFERENT", "NEW", "REMOVED"],
                        },
                        "options_verdict": {
                            "type": "string",
                            "enum": ["SAME", "VARIABLE", "CHANGED", "N_A"],
                        },
                        "explanation": {
                            "type": "string",
                            "description": "One-sentence explanation.",
                        },
                    },
                    "required": ["baseline_step", "latest_step", "question_verdict",
                                 "options_verdict", "explanation"],
                },
            },
        },
        "required": ["alignments"],
    },
}


def _format_steps(steps: list[dict], label: str) -> str:
    lines = [f"{label} ({len(steps)} steps):"]
    for s in steps:
        num = s.get("step_number", "?")
        q = (s.get("question_text") or "[info/loading screen]")[:200]
        stype = s.get("step_type", "")
        opts = s.get("answer_options")
        opts_str = ""
        if opts and isinstance(opts, list):
            labels = []
            for o in opts[:10]:
                if isinstance(o, dict):
                    labels.append(str(o.get("label") or o.get("value") or o))
                else:
                    labels.append(str(o))
            opts_str = f" | Options: {', '.join(labels)}"
            if len(opts) > 10:
                opts_str += f" (+{len(opts) - 10} more)"
        lines.append(f"  Step {num} [{stype}]: {q}{opts_str}")
    return "\n".join(lines)


def _build_diff_prompt(baseline_steps: list[dict], new_steps: list[dict]) -> str:
    bl_text = _format_steps(baseline_steps, "BASELINE")
    lt_text = _format_steps(new_steps, "LATEST")

    return (
        "Compare these two traversals of the SAME quiz funnel on the SAME website.\n\n"
        "1. ALIGN steps by topic/meaning, NOT by step number. Steps may be reordered.\n"
        "   Match each baseline step to the latest step asking the same question.\n\n"
        "2. Classify the QUESTION:\n"
        '   - SAME: identical text\n'
        '   - COSMETIC: same meaning, different wording. Examples:\n'
        '     "How old are you?" vs "What\'s your age?" = COSMETIC\n'
        '     "May 5" vs "May 15" in date fields = COSMETIC\n'
        '     Info screen label vs actual page text = COSMETIC\n'
        '     Number format "2.5M" vs "2,500,000" = COSMETIC\n'
        '   - DIFFERENT: substantively different question intent. Example:\n'
        '     "Have you worked with a psychologist?" vs\n'
        '     "Did a psychologist suggest our app?" = DIFFERENT\n\n'
        "3. Classify the OPTIONS:\n"
        "   - SAME: identical option lists (order doesn't matter)\n"
        "   - VARIABLE: question is SAME/COSMETIC but options differ (A/B testing)\n"
        "   - CHANGED: question is DIFFERENT and options also differ\n"
        "   - N_A: one or both steps have no options\n\n"
        "4. Unmatched baseline steps -> REMOVED. Unmatched latest steps -> NEW.\n\n"
        "5. Every baseline step and every latest step must appear exactly once.\n\n"
        f"{bl_text}\n\n{lt_text}\n\n"
        "Use the save_diff_result tool."
    )


def _parse_alignments(alignments: list[dict]) -> list[Change]:
    changes: list[Change] = []
    for a in alignments:
        q = a.get("question_verdict", "SAME")
        o = a.get("options_verdict", "N_A")
        explanation = a.get("explanation", "")
        bl = a.get("baseline_step")
        lt = a.get("latest_step")
        step_num = lt or bl

        if q == "REMOVED":
            changes.append(Change(
                "medium", "funnel", bl,
                f"Step {bl} removed: {explanation}",
            ))
        elif q == "NEW":
            changes.append(Change(
                "medium", "funnel", lt,
                f"New step {lt}: {explanation}",
            ))
        elif q == "DIFFERENT":
            changes.append(Change(
                "high", "funnel", step_num,
                f"Step changed (baseline {bl} -> latest {lt}): {explanation}",
            ))
        elif q == "COSMETIC":
            changes.append(Change(
                "low", "funnel", step_num,
                f"Step reworded (baseline {bl} -> latest {lt}): {explanation}",
            ))

        if q in ("SAME", "COSMETIC"):
            if o == "VARIABLE":
                changes.append(Change(
                    "low", "funnel", step_num,
                    f"Step {step_num} options vary (likely A/B test)",
                ))
            elif o == "CHANGED":
                changes.append(Change(
                    "medium", "funnel", step_num,
                    f"Step {step_num} answer options changed",
                ))

    return changes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def diff_runs(baseline_steps: list[dict], new_steps: list[dict],
              baseline_pricing: dict | None, new_pricing: dict | None) -> DiffResult:
    baseline_steps = _deduplicate_steps(baseline_steps)
    new_steps = _deduplicate_steps(new_steps)

    result = DiffResult()

    if len(baseline_steps) != len(new_steps):
        diff = len(new_steps) - len(baseline_steps)
        direction = "more" if diff > 0 else "fewer"
        result.changes.append(Change(
            severity="medium", category="structural", step_number=None,
            description=f"Funnel now has {abs(diff)} {direction} steps "
                        f"({len(baseline_steps)} -> {len(new_steps)})",
        ))

    if baseline_steps and new_steps:
        prompt = _build_diff_prompt(baseline_steps, new_steps)
        log.info("Running semantic diff: %d baseline vs %d new steps via %s",
                 len(baseline_steps), len(new_steps), DIFF_MODEL)
        diff_start = time.perf_counter()
        client = anthropic.Anthropic(api_key=_API_KEY)
        response = client.messages.create(
            model=DIFF_MODEL,
            max_tokens=8192,
            temperature=0,
            tools=[DIFF_TOOL],
            messages=[{"role": "user", "content": prompt}],
        )
        diff_duration_ms = (time.perf_counter() - diff_start) * 1000
        log.info("Semantic diff completed in %.1fs", diff_duration_ms / 1000,
                 extra={"duration_ms": round(diff_duration_ms)})

        tool_input = None
        for block in response.content:
            if block.type == "tool_use" and block.name == "save_diff_result":
                tool_input = block.input
                break

        if not tool_input:
            raise ValueError("LLM did not return save_diff_result tool call")

        result.changes.extend(_parse_alignments(tool_input.get("alignments", [])))

    elif not new_steps:
        for s in baseline_steps:
            result.changes.append(Change(
                "medium", "funnel", s.get("step_number"),
                f"Step {s.get('step_number')} removed",
            ))
    elif not baseline_steps:
        for s in new_steps:
            q = (s.get("question_text") or "unknown")[:60]
            result.changes.append(Change(
                "medium", "funnel", s.get("step_number"),
                f"New step {s.get('step_number')}: '{q}'",
            ))

    if baseline_pricing and new_pricing:
        _diff_pricing(baseline_pricing, new_pricing, result)
    elif new_pricing and not baseline_pricing:
        result.changes.append(Change("high", "pricing", None,
                                     "Pricing now visible (was not captured before)"))
    elif baseline_pricing and not new_pricing:
        result.changes.append(Change("high", "pricing", None,
                                     "Pricing no longer visible"))

    major_count = sum(
        1 for c in result.changes
        if c.category == "funnel" and c.severity in ("high", "critical")
    )
    if major_count >= 3:
        result.drift_level = "major"
    elif result.has_changes:
        result.drift_level = "minor"

    return result
