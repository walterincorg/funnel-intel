"""Shared Anthropic LLM service.

Wraps `anthropic.Anthropic(...).messages.create(...)` with the patterns every
synthesis-layer caller wants:
  - tool_use for guaranteed JSON shape (Anthropic enforces the schema)
  - actual usage reporting (input/output tokens + cost in cents)
  - hard per-call cost cap so a runaway prompt never burns the budget
  - a single error type (LLMError) so callers don't juggle SDK exceptions

Pricing:
  Costs are evaluated from MODEL_COSTS_USD_PER_M_TOKENS. Override a model's
  price at runtime by adding an entry. If a model isn't listed we fall back
  to Sonnet 4 pricing (a conservative-ish default).

Ad analysis uses a narrower pattern (single tool, short prompt). Ship list
generation uses this service because it needs cost tracking + structured
errors. Both could be unified later; for now the existing ad_analysis.py
stays on its direct SDK path.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import anthropic

from backend.config import SYNTHESIS_COST_CAP_USD

log = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DEFAULT_MODEL = os.getenv("SYNTHESIS_MODEL", "claude-sonnet-4-20250514")

# USD per 1M tokens. Input, output.
MODEL_COSTS_USD_PER_M_TOKENS: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-opus-4-20250514": (15.0, 75.0),
    "claude-3-5-sonnet-20241022": (3.0, 15.0),
    "claude-3-5-haiku-20241022": (0.80, 4.0),
}
_FALLBACK_COST = (3.0, 15.0)


class LLMError(Exception):
    """Uniform error surface for LLM call failures."""


class LLMCostCapExceeded(LLMError):
    """Projected cost of a call exceeds SYNTHESIS_COST_CAP_USD."""


@dataclass
class LLMUsage:
    """Structured usage report returned alongside every call."""

    input_tokens: int
    output_tokens: int
    cost_cents: int
    model: str

    @classmethod
    def from_anthropic(cls, usage: Any, model: str) -> "LLMUsage":
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=usage_cost_cents(input_tokens, output_tokens, model),
            model=model,
        )


# --- Cost math (pure, testable) ---------------------------------------------


def _model_prices(model: str) -> tuple[float, float]:
    return MODEL_COSTS_USD_PER_M_TOKENS.get(model, _FALLBACK_COST)


def usage_cost_cents(input_tokens: int, output_tokens: int, model: str) -> int:
    """Cost of a completed call, rounded up to cents."""
    in_per_m, out_per_m = _model_prices(model)
    usd = (input_tokens / 1_000_000) * in_per_m + (output_tokens / 1_000_000) * out_per_m
    return _round_up_cents(usd)


def projected_max_cost_cents(
    prompt_chars: int,
    max_output_tokens: int,
    model: str,
) -> int:
    """Upper-bound cost estimate for a call before it's dispatched.

    Uses a coarse chars-per-token ratio of 4 for the input estimate (Anthropic
    averages roughly this for English prose). Output is bounded by
    max_output_tokens, so that number is used directly.
    """
    estimated_input_tokens = max(1, prompt_chars // 4)
    return usage_cost_cents(estimated_input_tokens, max_output_tokens, model)


def _round_up_cents(usd: float) -> int:
    cents = int(usd * 100)
    if (usd * 100) > cents:
        cents += 1
    return cents


# --- The call ----------------------------------------------------------------


def call_claude_with_tool(
    prompt: str,
    tool: dict[str, Any],
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
    system: str | None = None,
    client: anthropic.Anthropic | None = None,
    cost_cap_usd: float | None = None,
) -> tuple[dict[str, Any], LLMUsage]:
    """Call Claude with a single tool definition and return its tool_use input.

    This is the structured-output path: the tool's input_schema is enforced
    by Anthropic, so the returned dict is guaranteed to match the schema
    shape (but NOT semantic correctness — callers must still validate IDs,
    enums, ranges, etc.).

    Args:
        prompt: the user message.
        tool: a single Anthropic tool definition (name, description, input_schema).
        model: override DEFAULT_MODEL.
        max_tokens: upper bound on generated output.
        system: optional system prompt.
        client: inject an anthropic.Anthropic instance (tests pass a fake).
        cost_cap_usd: override SYNTHESIS_COST_CAP_USD for this call.

    Returns:
        (tool_input_dict, usage_report)

    Raises:
        LLMError if the API key is missing, the call fails, or the model
        doesn't emit a tool_use block for the expected tool.
        LLMCostCapExceeded if the projected cost exceeds the cap.
    """
    if not ANTHROPIC_API_KEY and client is None:
        raise LLMError("ANTHROPIC_API_KEY not configured")

    cap_usd = cost_cap_usd if cost_cap_usd is not None else SYNTHESIS_COST_CAP_USD
    projected = projected_max_cost_cents(len(prompt), max_tokens, model)
    if projected > int(cap_usd * 100):
        raise LLMCostCapExceeded(
            f"Projected call cost {projected}¢ exceeds cap {int(cap_usd * 100)}¢ "
            f"(prompt {len(prompt)} chars, max_output {max_tokens} tokens, model {model})"
        )

    if client is None:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "tools": [tool],
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    try:
        response = client.messages.create(**kwargs)
    except anthropic.APIError as e:
        raise LLMError(f"Anthropic API error: {e}") from e
    except Exception as e:
        raise LLMError(f"Unexpected LLM failure: {e}") from e

    tool_input = _extract_tool_input(response, tool.get("name", ""))
    if tool_input is None:
        raise LLMError(
            f"Model did not emit a tool_use block for {tool.get('name')!r}. "
            f"Stop reason: {getattr(response, 'stop_reason', 'unknown')}"
        )

    usage = LLMUsage.from_anthropic(getattr(response, "usage", None), model)

    return tool_input, usage


def _extract_tool_input(response: Any, expected_tool_name: str) -> dict[str, Any] | None:
    """Pull the first matching tool_use block's input. Tolerates missing
    attributes so tests can construct minimal fake responses."""
    content = getattr(response, "content", None) or []
    for block in content:
        block_type = getattr(block, "type", None)
        block_name = getattr(block, "name", None)
        if block_type == "tool_use" and block_name == expected_tool_name:
            return getattr(block, "input", None)
    return None
