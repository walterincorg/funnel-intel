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
    pricing_changed: bool = False
    pricing_summary: str = ""
    alert_worthy_changes: list[str] = field(default_factory=list)

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


EVAL_TOOL = {
    "name": "save_evaluation",
    "description": "Save the final evaluation of funnel changes, pricing, drift level, and alert-worthy items.",
    "input_schema": {
        "type": "object",
        "properties": {
            "drift_level": {
                "type": "string",
                "enum": ["none", "minor", "major"],
                "description": (
                    "'none' if nothing meaningful changed (cosmetic rewording, A/B test "
                    "variations, step reordering are NOT meaningful). 'minor' for small real "
                    "changes. 'major' for significant changes like genuinely new questions, "
                    "removed questions, or pricing changes."
                ),
            },
            "pricing_changed": {
                "type": "boolean",
                "description": (
                    "True only if actual price amounts, plan lineup, or discount terms "
                    "genuinely changed. Format differences ('$29.99' vs '29.99'), whitespace, "
                    "and currency symbol presence are NOT changes."
                ),
            },
            "pricing_summary": {
                "type": "string",
                "description": "One-sentence summary of what changed in pricing, or 'No change'.",
            },
            "alert_worthy_changes": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Short descriptions of changes that warrant an alert. Only include: "
                    "genuine pricing changes OR genuinely new questions that were not present "
                    "before in any form. Exclude cosmetic rewording, A/B test variations, "
                    "and reordered steps."
                ),
            },
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "baseline_step": {"type": ["integer", "null"]},
                        "latest_step": {"type": ["integer", "null"]},
                        "final_severity": {
                            "type": "string",
                            "enum": ["high", "medium", "low", "none"],
                            "description": (
                                "'high' only for genuine pricing changes or genuinely new "
                                "questions. 'low' for cosmetic, rewords, A/B tests. "
                                "'none' to suppress a false positive."
                            ),
                        },
                        "category": {
                            "type": "string",
                            "enum": ["funnel", "pricing"],
                        },
                        "description": {
                            "type": "string",
                            "description": "One-sentence description of this change.",
                        },
                    },
                    "required": ["baseline_step", "latest_step", "final_severity",
                                 "category", "description"],
                },
                "description": (
                    "One entry per real change. Skip SAME-verdict pairs with no change. "
                    "Use final_severity='none' to suppress false positives."
                ),
            },
        },
        "required": ["drift_level", "pricing_changed", "pricing_summary",
                      "alert_worthy_changes", "changes"],
    },
}


def _build_eval_prompt(
    alignments: list[dict],
    baseline_pricing: dict | None,
    new_pricing: dict | None,
) -> str:
    import json
    alignment_json = json.dumps(alignments, indent=2)
    bl_pricing_json = json.dumps(baseline_pricing, indent=2) if baseline_pricing else "null"
    lt_pricing_json = json.dumps(new_pricing, indent=2) if new_pricing else "null"

    return (
        "You are evaluating changes detected in a website's quiz funnel between two scan runs.\n\n"
        "## Step Alignments from prior analysis\n"
        f"{alignment_json}\n\n"
        "## Pricing\n"
        f"BASELINE PRICING:\n{bl_pricing_json}\n\n"
        f"LATEST PRICING:\n{lt_pricing_json}\n\n"
        "## Your task\n"
        "Evaluate everything together and decide:\n\n"
        "1. **drift_level**: 'none' if nothing meaningful changed (cosmetic rewording, A/B test "
        "variations, and step reordering are NOT meaningful). 'minor' for small real changes. "
        "'major' for significant changes like genuinely new questions, removed questions, or "
        "pricing changes.\n\n"
        "2. **pricing_changed**: true ONLY if actual prices, plan lineup, or discount terms "
        "genuinely changed. Format differences ('$29.99' vs '29.99'), whitespace, and currency "
        "symbol presence are NOT changes. If one side is null (not captured), that alone is not "
        "a pricing change.\n\n"
        "3. **pricing_summary**: describe what changed, or 'No change'.\n\n"
        "4. **alert_worthy_changes**: list ONLY changes that matter enough to send a notification. "
        "This means: genuine pricing changes (price went up/down, plan added/removed) OR "
        "genuinely new questions that weren't asked before in any form. Do NOT include: "
        "cosmetic rewording, A/B test option variations, step reordering, or questions that "
        "are just rephrased versions of existing ones.\n\n"
        "5. **changes**: for each alignment that represents a real change (skip SAME+SAME pairs), "
        "assign a final_severity:\n"
        "   - 'high': only for genuine pricing changes or genuinely new questions\n"
        "   - 'low': for everything else (cosmetic, rewords, A/B tests, reordering)\n"
        "   - 'none': if on reflection this is not a real change at all\n\n"
        "Use the save_evaluation tool."
    )


def _run_evaluation(
    alignments: list[dict],
    baseline_pricing: dict | None,
    new_pricing: dict | None,
) -> dict:
    prompt = _build_eval_prompt(alignments, baseline_pricing, new_pricing)
    log.info("Running change evaluation via %s", DIFF_MODEL)
    eval_start = time.perf_counter()
    client = anthropic.Anthropic(api_key=_API_KEY)
    response = client.messages.create(
        model=DIFF_MODEL,
        max_tokens=4096,
        temperature=0,
        tools=[EVAL_TOOL],
        messages=[{"role": "user", "content": prompt}],
    )
    eval_duration_ms = (time.perf_counter() - eval_start) * 1000
    log.info("Change evaluation completed in %.1fs", eval_duration_ms / 1000,
             extra={"duration_ms": round(eval_duration_ms)})

    tool_input = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "save_evaluation":
            tool_input = block.input
            break

    if not tool_input:
        raise ValueError("LLM did not return save_evaluation tool call")

    return tool_input


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



# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def diff_runs(baseline_steps: list[dict], new_steps: list[dict],
              baseline_pricing: dict | None, new_pricing: dict | None) -> DiffResult:
    baseline_steps = _deduplicate_steps(baseline_steps)
    new_steps = _deduplicate_steps(new_steps)

    result = DiffResult()

    # Edge cases: one or both sides empty — no LLM needed
    if not baseline_steps and not new_steps:
        return result

    if not new_steps:
        for s in baseline_steps:
            result.changes.append(Change(
                "medium", "funnel", s.get("step_number"),
                f"Step {s.get('step_number')} removed",
            ))
        result.drift_level = "minor"
        return result

    if not baseline_steps:
        for s in new_steps:
            q = (s.get("question_text") or "unknown")[:60]
            result.changes.append(Change(
                "medium", "funnel", s.get("step_number"),
                f"New step {s.get('step_number')}: '{q}'",
            ))
        result.drift_level = "minor"
        return result

    # --- Call 1: Step alignment ---
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

    alignments = tool_input.get("alignments", [])

    # --- Call 2: Evaluation (severity, pricing, drift, alerts) ---
    evaluation = _run_evaluation(alignments, baseline_pricing, new_pricing)

    result.drift_level = evaluation.get("drift_level", "none")
    result.pricing_changed = evaluation.get("pricing_changed", False)
    result.pricing_summary = evaluation.get("pricing_summary", "")
    result.alert_worthy_changes = evaluation.get("alert_worthy_changes", [])

    for change in evaluation.get("changes", []):
        sev = change.get("final_severity", "low")
        if sev == "none":
            continue
        result.changes.append(Change(
            severity=sev,
            category=change.get("category", "funnel"),
            step_number=change.get("latest_step") or change.get("baseline_step"),
            description=change.get("description", ""),
        ))

    return result
