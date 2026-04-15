"""Ship list generator — the weekly Monday output of the synthesis layer.

Flow:
  1. Load high-confidence patterns from the patterns table (filtered by
     lookback window and min confidence).
  2. Load prior ship_list_outcomes for confidence weighting.
  3. Build the LLM prompt by formatting ship_list_v1.md.
  4. Call Claude with SHIP_LIST_TOOL (Anthropic-enforced JSON shape).
  5. Validate each returned item's shape (defensive: tool_use is not 100%).
  6. Resolve every cited pattern_id against the real patterns table. Any
     hallucinated ID rejects that item.
  7. Upsert surviving items into ship_list_items for the target week,
     ranked 1..N, replacing any existing items for the same week.
  8. Return a stats dict suitable for synthesis_runs logging.

The whole module is built so a clustering failure, a malformed LLM response,
an unreachable LLM, or a missing database never crashes the worker — every
integration point has a clear failure mode that returns a dud-but-honest
result rather than raising upward.

Hallucination guardrails:
  - Anthropic tool_use enforces JSON shape
  - validate_item_shape() double-checks the shape defensively
  - resolve_citations() rejects any item citing a pattern_id not in the
    actual database set loaded in step 1
  - One retry on zero-validated-items with an appended "cite only real IDs"
    nudge, then give up with status=failed
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from backend.db import get_db
from backend.services.llm import (
    LLMCostCapExceeded,
    LLMError,
    LLMUsage,
    call_claude_with_tool,
)

log = logging.getLogger(__name__)

# --- Config ------------------------------------------------------------------

PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "ship_list_v1.md"
PROMPT_VERSION = "ship_list_v1"

# Input filtering
LOOKBACK_DAYS = 30           # only patterns seen in this window are eligible
MIN_PATTERN_CONFIDENCE = 6.0  # patterns below this are noise
MAX_PATTERNS_IN_PROMPT = 30   # cap input size (cost + prompt clarity)

# Output constraints
MAX_SHIP_ITEMS = 5
PRIOR_OUTCOMES_WEEKS = 8
LLM_MAX_TOKENS = 4096

VALID_EFFORT = {"XS", "S", "M", "L"}
VALID_STATUS = {"proposed", "shipping", "shipped", "skipped", "expired"}


# --- Tool schema (Anthropic-enforced) ---------------------------------------

SHIP_LIST_TOOL: dict[str, Any] = {
    "name": "save_ship_list",
    "description": (
        "Save the weekly ship list for a DTC operator. Return 0-5 items, "
        "quality over quantity. Every item must cite at least one pattern_id "
        "from the patterns provided in the prompt."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "maxItems": MAX_SHIP_ITEMS,
                "items": {
                    "type": "object",
                    "properties": {
                        "rank": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_SHIP_ITEMS,
                        },
                        "headline": {
                            "type": "string",
                            "description": "Short, grepable title (under 100 chars).",
                        },
                        "recommendation": {
                            "type": "string",
                            "description": "Specific change the founder should make, 2-4 sentences.",
                        },
                        "test_plan": {
                            "type": "string",
                            "description": (
                                "Exactly how to run the test: what to change, how to "
                                "measure, how long to run."
                            ),
                        },
                        "effort_estimate": {
                            "type": "string",
                            "enum": sorted(VALID_EFFORT),
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 10,
                        },
                        "pattern_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": "1-3 pattern UUIDs from the prompt. MUST be exact IDs.",
                        },
                        "swipe_file_refs": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string"},
                                    "id": {"type": "string"},
                                    "label": {"type": "string"},
                                },
                                "required": ["type", "id"],
                            },
                            "description": (
                                "Optional creative/funnel-step references the founder "
                                "can inspect directly."
                            ),
                        },
                    },
                    "required": [
                        "rank", "headline", "recommendation", "test_plan",
                        "effort_estimate", "confidence", "pattern_ids",
                    ],
                },
            },
        },
        "required": ["items"],
    },
}


# --- Data loaders -----------------------------------------------------------


def load_candidate_patterns(
    *,
    lookback_days: int = LOOKBACK_DAYS,
    min_confidence: float = MIN_PATTERN_CONFIDENCE,
    limit: int = MAX_PATTERNS_IN_PROMPT,
    now: datetime | None = None,
) -> list[dict]:
    """Pull fresh, high-confidence patterns sorted by confidence desc."""
    now = now or datetime.now(timezone.utc)
    cutoff_iso = _iso_subtract_days(now, lookback_days)
    try:
        res = (
            get_db()
            .table("patterns")
            .select(
                "id,pattern_type,headline,confidence,observed_in_competitors,"
                "evidence_refs,metadata,first_seen_at,last_seen_at,observation_count"
            )
            .gte("last_seen_at", cutoff_iso)
            .gte("confidence", min_confidence)
            .order("confidence", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception:
        log.exception("load_candidate_patterns: DB query failed")
        return []
    return res.data or []


def load_prior_outcomes(
    *,
    weeks_back: int = PRIOR_OUTCOMES_WEEKS,
    now: datetime | None = None,
) -> list[dict]:
    """Pull prior ship_list_outcomes joined with their items.

    Returns a list of dicts with outcome + item headline + pattern_ids so
    the prompt can reason about "this kind of recommendation won/lost."
    """
    now = now or datetime.now(timezone.utc)
    cutoff_iso = _iso_subtract_days(now, weeks_back * 7)
    db = get_db()

    try:
        outcomes_res = (
            db.table("ship_list_outcomes")
            .select("id,ship_list_item_id,outcome,notes,recorded_at")
            .gte("recorded_at", cutoff_iso)
            .execute()
        )
    except Exception:
        log.exception("load_prior_outcomes: outcomes query failed")
        return []

    outcomes = outcomes_res.data or []
    if not outcomes:
        return []

    item_ids = [o["ship_list_item_id"] for o in outcomes]

    try:
        items_res = (
            db.table("ship_list_items")
            .select("id,headline,pattern_ids")
            .in_("id", item_ids)
            .execute()
        )
    except Exception:
        log.exception("load_prior_outcomes: items join failed")
        return []

    items_by_id = {row["id"]: row for row in (items_res.data or [])}

    joined: list[dict] = []
    for o in outcomes:
        item = items_by_id.get(o["ship_list_item_id"])
        if not item:
            continue
        joined.append({
            "outcome": o["outcome"],
            "notes": o.get("notes"),
            "recorded_at": o.get("recorded_at"),
            "headline": item.get("headline"),
            "pattern_ids": item.get("pattern_ids") or [],
        })
    return joined


# --- Prompt construction (pure) ---------------------------------------------


def format_patterns_section(patterns: list[dict]) -> str:
    """Format a list of pattern dicts into a numbered block for the prompt."""
    if not patterns:
        return "(no patterns with confidence >= 6 in the last 30 days)"
    lines: list[str] = []
    for i, p in enumerate(patterns, start=1):
        pid = p.get("id", "")
        ptype = p.get("pattern_type", "")
        headline = p.get("headline") or "(no headline)"
        confidence = p.get("confidence", 0)
        competitors = p.get("observed_in_competitors") or []
        obs_count = p.get("observation_count", 1)
        lines.append(
            f"{i}. [{ptype}] id={pid} confidence={confidence:.1f} "
            f"observed_in={len(competitors)} observations={obs_count}\n"
            f"   {headline}"
        )
    return "\n".join(lines)


def format_prior_outcomes_section(outcomes: list[dict]) -> str:
    """Format prior outcomes into a compact context block."""
    if not outcomes:
        return "(no prior outcomes recorded)"
    lines = ["Recent outcomes:"]
    for o in outcomes:
        notes = f" — {o['notes']}" if o.get("notes") else ""
        lines.append(f"  - [{o['outcome']}] {o.get('headline') or '(unknown)'}{notes}")
    return "\n".join(lines)


def build_prompt(patterns: list[dict], prior_outcomes: list[dict]) -> str:
    """Load the prompt template and fill in the context sections."""
    template = _load_prompt_template()
    return template.format(
        patterns_section=format_patterns_section(patterns),
        prior_outcomes_section=format_prior_outcomes_section(prior_outcomes),
    )


def _load_prompt_template() -> str:
    try:
        return PROMPT_FILE.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise LLMError(f"Prompt template not found: {PROMPT_FILE}") from e


# --- Shape validation (pure) ------------------------------------------------


def validate_item_shape(item: Any) -> list[str]:
    """Return a list of shape errors for a ship list item. Empty = valid.

    Defensive — Anthropic tool_use is not 100% reliable, and even if it were
    we want a single surface to reject malformed items. Checks required
    fields, types, enums, and ranges.
    """
    errors: list[str] = []

    if not isinstance(item, dict):
        return ["item is not a dict"]

    required = {
        "rank": int,
        "headline": str,
        "recommendation": str,
        "test_plan": str,
        "effort_estimate": str,
        "confidence": (int, float),
        "pattern_ids": list,
    }
    for field, expected_type in required.items():
        if field not in item:
            errors.append(f"missing field: {field}")
            continue
        if not isinstance(item[field], expected_type):
            errors.append(f"field {field} has wrong type: {type(item[field]).__name__}")

    if "rank" in item and isinstance(item["rank"], int):
        if not (1 <= item["rank"] <= MAX_SHIP_ITEMS):
            errors.append(f"rank {item['rank']} out of range 1..{MAX_SHIP_ITEMS}")

    if "effort_estimate" in item and item["effort_estimate"] not in VALID_EFFORT:
        errors.append(f"effort_estimate {item['effort_estimate']!r} not in {sorted(VALID_EFFORT)}")

    if "confidence" in item and isinstance(item["confidence"], (int, float)):
        if not (0 <= item["confidence"] <= 10):
            errors.append(f"confidence {item['confidence']} out of range 0..10")

    if "pattern_ids" in item and isinstance(item["pattern_ids"], list):
        if len(item["pattern_ids"]) == 0:
            errors.append("pattern_ids is empty")
        for pid in item["pattern_ids"]:
            if not isinstance(pid, str):
                errors.append(f"pattern_ids contains non-string: {pid!r}")

    if "headline" in item and isinstance(item["headline"], str):
        if not item["headline"].strip():
            errors.append("headline is empty")

    return errors


# --- Citation validation (pure) ---------------------------------------------


def resolve_citations(item: dict, known_pattern_ids: set[str]) -> list[str]:
    """Return a list of cited pattern_ids that do NOT exist in known_pattern_ids.

    An empty return value means every citation resolves.
    """
    cited = item.get("pattern_ids") or []
    return [pid for pid in cited if pid not in known_pattern_ids]


# --- Deduplication (pure) ---------------------------------------------------


def rank_and_dedupe(items: list[dict]) -> list[dict]:
    """Ensure distinct ranks 1..N and re-sort by (confidence desc, rank asc).

    The LLM is instructed to rank items, but may duplicate or skip ranks.
    Re-normalize to 1..N by confidence desc.
    """
    if not items:
        return []
    sorted_items = sorted(
        items,
        key=lambda x: (-float(x.get("confidence", 0)), int(x.get("rank", MAX_SHIP_ITEMS))),
    )
    for i, item in enumerate(sorted_items, start=1):
        item["rank"] = i
    return sorted_items


# --- Persistence ------------------------------------------------------------


def persist_ship_list(
    week_of: date,
    items: list[dict],
    *,
    replace_existing: bool = True,
    generated_by_run_id: str | None = None,
) -> int:
    """Upsert ship_list_items for a given week. Returns rows written.

    Behavior:
      - With replace_existing=True (default), deletes any rows for week_of
        first so the new list fully supersedes. This matches the product
        model: each Monday a fresh list lands.
      - A zero-item list still deletes the prior week's rows, so empty state
        is honest ("no strong signal this week").
    """
    db = get_db()

    if replace_existing:
        try:
            db.table("ship_list_items").delete().eq("week_of", week_of.isoformat()).execute()
        except Exception:
            log.exception("persist_ship_list: delete of existing week failed")
            return 0

    if not items:
        return 0

    now_iso = datetime.now(timezone.utc).isoformat()
    rows = []
    for item in items:
        rows.append({
            "week_of": week_of.isoformat(),
            "rank": item["rank"],
            "headline": item["headline"],
            "recommendation": item["recommendation"],
            "test_plan": item["test_plan"],
            "effort_estimate": item["effort_estimate"],
            "confidence": float(item["confidence"]),
            "pattern_ids": item["pattern_ids"],
            "swipe_file_refs": item.get("swipe_file_refs"),
            "status": "proposed",
            "generated_by_run_id": generated_by_run_id,
            "created_at": now_iso,
        })

    try:
        db.table("ship_list_items").insert(rows).execute()
    except Exception:
        log.exception("persist_ship_list: batch insert failed")
        return 0

    return len(rows)


# --- Orchestration -----------------------------------------------------------


def generate_ship_list(
    week_of: date | None = None,
    *,
    now: datetime | None = None,
    generated_by_run_id: str | None = None,
    max_retries: int = 1,
) -> dict:
    """Top-level orchestrator. Returns a stats dict.

    Never raises — every failure path returns a structured result with
    status in {'completed', 'empty', 'failed'}.
    """
    now = now or datetime.now(timezone.utc)
    week_of = week_of or now.date()

    stats: dict[str, Any] = {
        "status": "pending",
        "week_of": week_of.isoformat(),
        "candidate_pattern_count": 0,
        "prior_outcome_count": 0,
        "items_proposed": 0,
        "items_accepted": 0,
        "items_rejected_shape": 0,
        "items_rejected_citation": 0,
        "retries": 0,
        "llm_cost_cents": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "error": None,
    }

    patterns = load_candidate_patterns(now=now)
    stats["candidate_pattern_count"] = len(patterns)
    if not patterns:
        stats["status"] = "empty"
        persist_ship_list(week_of, [], generated_by_run_id=generated_by_run_id)
        return stats

    prior_outcomes = load_prior_outcomes(now=now)
    stats["prior_outcome_count"] = len(prior_outcomes)
    known_ids = {str(p["id"]) for p in patterns if "id" in p}

    prompt = build_prompt(patterns, prior_outcomes)

    accepted_items: list[dict] = []
    attempt = 0
    total_usage = LLMUsage(input_tokens=0, output_tokens=0, cost_cents=0, model="")

    while attempt <= max_retries:
        attempt_prompt = prompt if attempt == 0 else _retry_prompt(prompt, known_ids)
        try:
            tool_input, usage = call_claude_with_tool(
                attempt_prompt,
                SHIP_LIST_TOOL,
                max_tokens=LLM_MAX_TOKENS,
            )
        except LLMCostCapExceeded as e:
            log.error("ship_list: cost cap exceeded: %s", e)
            stats["status"] = "failed"
            stats["error"] = f"cost_cap_exceeded: {e}"
            return stats
        except LLMError as e:
            log.exception("ship_list: LLM call failed")
            stats["status"] = "failed"
            stats["error"] = f"llm_error: {e}"
            return stats

        total_usage = LLMUsage(
            input_tokens=total_usage.input_tokens + usage.input_tokens,
            output_tokens=total_usage.output_tokens + usage.output_tokens,
            cost_cents=total_usage.cost_cents + usage.cost_cents,
            model=usage.model,
        )

        raw_items = list(tool_input.get("items") or [])
        stats["items_proposed"] = len(raw_items)
        accepted_items, shape_rejects, citation_rejects = _filter_items(raw_items, known_ids)
        stats["items_rejected_shape"] += shape_rejects
        stats["items_rejected_citation"] += citation_rejects

        if accepted_items or attempt >= max_retries:
            break
        log.warning(
            "ship_list: attempt %d produced 0 valid items (%d shape, %d citation), retrying",
            attempt, shape_rejects, citation_rejects,
        )
        attempt += 1
        stats["retries"] += 1

    stats["llm_cost_cents"] = total_usage.cost_cents
    stats["input_tokens"] = total_usage.input_tokens
    stats["output_tokens"] = total_usage.output_tokens

    accepted_items = rank_and_dedupe(accepted_items)
    stats["items_accepted"] = len(accepted_items)

    persisted = persist_ship_list(
        week_of, accepted_items, generated_by_run_id=generated_by_run_id,
    )

    if persisted == 0:
        stats["status"] = "empty" if accepted_items == [] else "failed"
    else:
        stats["status"] = "completed"

    return stats


# --- Internal helpers -------------------------------------------------------


def _filter_items(
    raw_items: list[Any],
    known_pattern_ids: set[str],
) -> tuple[list[dict], int, int]:
    """Apply shape validation + citation resolution. Returns (accepted, shape_reject_count, citation_reject_count)."""
    accepted: list[dict] = []
    shape_rejects = 0
    citation_rejects = 0

    for item in raw_items:
        shape_errors = validate_item_shape(item)
        if shape_errors:
            log.warning("ship_list: rejecting item for shape errors: %s", shape_errors)
            shape_rejects += 1
            continue
        unresolved = resolve_citations(item, known_pattern_ids)
        if unresolved:
            log.warning(
                "ship_list: rejecting item %r for hallucinated citations: %s",
                item.get("headline"), unresolved,
            )
            citation_rejects += 1
            continue
        accepted.append(item)

    return accepted, shape_rejects, citation_rejects


def _retry_prompt(original_prompt: str, known_ids: set[str]) -> str:
    """Append a correction note asking the model to cite only real IDs."""
    correction = (
        "\n\nIMPORTANT: Your previous attempt returned zero valid items. "
        "Every pattern_id you cite MUST match one of the exact UUIDs listed in the "
        "`## Patterns available this week` section above. Do not invent UUIDs. "
        "If fewer than 3 patterns meet the bar, return fewer items — empty is honest."
    )
    return original_prompt + correction


def _iso_subtract_days(now: datetime, days: int) -> str:
    from datetime import timedelta
    return (now - timedelta(days=days)).isoformat()
