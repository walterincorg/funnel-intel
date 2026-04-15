"""Tests for the ship list generator's pure logic.

Out of scope (needs DB / network):
  - generate_ship_list orchestration end-to-end
  - load_candidate_patterns / load_prior_outcomes
  - persist_ship_list

In scope:
  - format_patterns_section / format_prior_outcomes_section / build_prompt
  - validate_item_shape — every rejection branch
  - resolve_citations
  - rank_and_dedupe
  - _filter_items (integration of shape + citation)
  - _retry_prompt construction
  - LLM service cost math
"""

import pytest

from backend.services.llm import (
    _round_up_cents,
    projected_max_cost_cents,
    usage_cost_cents,
)
from backend.worker.ship_list import (
    MAX_SHIP_ITEMS,
    SHIP_LIST_TOOL,
    _filter_items,
    _retry_prompt,
    build_prompt,
    format_patterns_section,
    format_prior_outcomes_section,
    rank_and_dedupe,
    resolve_citations,
    validate_item_shape,
)


# --- format_patterns_section ---


class TestFormatPatternsSection:
    def test_empty_list_returns_placeholder(self):
        out = format_patterns_section([])
        assert "no patterns" in out.lower()

    def test_single_pattern_renders_all_fields(self):
        patterns = [{
            "id": "abc-123",
            "pattern_type": "proven_winner",
            "headline": "Cluster X running 60d with 5 variants",
            "confidence": 8.5,
            "observed_in_competitors": ["comp-1"],
            "observation_count": 3,
        }]
        out = format_patterns_section(patterns)
        assert "abc-123" in out
        assert "proven_winner" in out
        assert "8.5" in out
        assert "Cluster X running 60d with 5 variants" in out
        assert "observations=3" in out

    def test_multiple_patterns_numbered(self):
        patterns = [
            {"id": "a", "pattern_type": "price_move", "headline": "h1", "confidence": 7, "observed_in_competitors": [], "observation_count": 1},
            {"id": "b", "pattern_type": "funnel_change", "headline": "h2", "confidence": 9, "observed_in_competitors": [], "observation_count": 2},
        ]
        out = format_patterns_section(patterns)
        assert "1." in out
        assert "2." in out

    def test_missing_headline_renders_placeholder(self):
        patterns = [{"id": "a", "pattern_type": "launch_signal", "confidence": 6, "observed_in_competitors": [], "observation_count": 1}]
        out = format_patterns_section(patterns)
        assert "no headline" in out


# --- format_prior_outcomes_section ---


class TestFormatPriorOutcomesSection:
    def test_empty_returns_placeholder(self):
        assert "no prior outcomes" in format_prior_outcomes_section([]).lower()

    def test_renders_outcome_and_headline(self):
        outcomes = [
            {"outcome": "won", "headline": "Try goal-first question 4", "notes": "lifted CVR 12%"},
            {"outcome": "lost", "headline": "Test $7 trial", "notes": None},
        ]
        out = format_prior_outcomes_section(outcomes)
        assert "[won]" in out
        assert "[lost]" in out
        assert "Try goal-first question 4" in out
        assert "lifted CVR 12%" in out


# --- build_prompt ---


class TestBuildPrompt:
    def test_substitutes_both_sections(self):
        patterns = [{
            "id": "abc", "pattern_type": "proven_winner",
            "headline": "PATTERN_FIXTURE_MARKER", "confidence": 9,
            "observed_in_competitors": ["x"], "observation_count": 1,
        }]
        outcomes = [{"outcome": "won", "headline": "OUTCOME_FIXTURE_MARKER", "notes": None}]
        prompt = build_prompt(patterns, outcomes)
        assert "PATTERN_FIXTURE_MARKER" in prompt
        assert "OUTCOME_FIXTURE_MARKER" in prompt
        # Template structural checks
        assert "save_ship_list" in prompt
        assert "Hard rules" in prompt


# --- validate_item_shape ---


def _valid_item():
    return {
        "rank": 1,
        "headline": "Replace question 4 with goal-first framing",
        "recommendation": "Swap demographic question for goal selection.",
        "test_plan": "Change step 4 copy and measure completion rate over 7 days.",
        "effort_estimate": "S",
        "confidence": 7.5,
        "pattern_ids": ["abc-123"],
    }


class TestValidateItemShape:
    def test_valid_item_passes(self):
        assert validate_item_shape(_valid_item()) == []

    def test_non_dict_rejected(self):
        errors = validate_item_shape("not a dict")
        assert any("not a dict" in e for e in errors)

    def test_missing_field(self):
        item = _valid_item()
        del item["headline"]
        errors = validate_item_shape(item)
        assert any("missing field: headline" in e for e in errors)

    def test_wrong_type(self):
        item = _valid_item()
        item["rank"] = "one"
        errors = validate_item_shape(item)
        assert any("rank" in e and "wrong type" in e for e in errors)

    def test_rank_out_of_range(self):
        item = _valid_item()
        item["rank"] = 99
        errors = validate_item_shape(item)
        assert any("rank 99" in e for e in errors)

    def test_rank_zero_rejected(self):
        item = _valid_item()
        item["rank"] = 0
        errors = validate_item_shape(item)
        assert any("rank 0" in e for e in errors)

    def test_invalid_effort_enum(self):
        item = _valid_item()
        item["effort_estimate"] = "HUGE"
        errors = validate_item_shape(item)
        assert any("effort_estimate" in e for e in errors)

    def test_confidence_above_ten(self):
        item = _valid_item()
        item["confidence"] = 11
        errors = validate_item_shape(item)
        assert any("confidence 11" in e for e in errors)

    def test_confidence_below_zero(self):
        item = _valid_item()
        item["confidence"] = -1
        errors = validate_item_shape(item)
        assert any("confidence -1" in e for e in errors)

    def test_empty_pattern_ids(self):
        item = _valid_item()
        item["pattern_ids"] = []
        errors = validate_item_shape(item)
        assert any("pattern_ids is empty" in e for e in errors)

    def test_non_string_pattern_id(self):
        item = _valid_item()
        item["pattern_ids"] = ["abc-123", 42]
        errors = validate_item_shape(item)
        assert any("non-string" in e for e in errors)

    def test_empty_headline(self):
        item = _valid_item()
        item["headline"] = "   "
        errors = validate_item_shape(item)
        assert any("headline is empty" in e for e in errors)


# --- resolve_citations ---


class TestResolveCitations:
    def test_all_citations_resolve(self):
        item = {"pattern_ids": ["a", "b"]}
        known = {"a", "b", "c"}
        assert resolve_citations(item, known) == []

    def test_one_hallucinated(self):
        item = {"pattern_ids": ["a", "fake"]}
        known = {"a", "b"}
        assert resolve_citations(item, known) == ["fake"]

    def test_all_hallucinated(self):
        item = {"pattern_ids": ["x", "y"]}
        known = {"a", "b"}
        assert set(resolve_citations(item, known)) == {"x", "y"}

    def test_missing_pattern_ids_key(self):
        # Shape validator should catch this, but resolve should handle it defensively.
        assert resolve_citations({}, {"a"}) == []


# --- rank_and_dedupe ---


class TestRankAndDedupe:
    def test_empty_returns_empty(self):
        assert rank_and_dedupe([]) == []

    def test_sorts_by_confidence_desc(self):
        items = [
            {**_valid_item(), "rank": 1, "confidence": 5, "headline": "low"},
            {**_valid_item(), "rank": 2, "confidence": 9, "headline": "high"},
            {**_valid_item(), "rank": 3, "confidence": 7, "headline": "mid"},
        ]
        out = rank_and_dedupe(items)
        assert [i["headline"] for i in out] == ["high", "mid", "low"]
        assert [i["rank"] for i in out] == [1, 2, 3]

    def test_duplicate_ranks_renormalized(self):
        items = [
            {**_valid_item(), "rank": 1, "confidence": 8, "headline": "a"},
            {**_valid_item(), "rank": 1, "confidence": 6, "headline": "b"},
        ]
        out = rank_and_dedupe(items)
        assert [i["rank"] for i in out] == [1, 2]

    def test_stable_under_confidence_tie(self):
        # Tie-break falls back to original rank.
        items = [
            {**_valid_item(), "rank": 2, "confidence": 7, "headline": "second"},
            {**_valid_item(), "rank": 1, "confidence": 7, "headline": "first"},
        ]
        out = rank_and_dedupe(items)
        assert [i["headline"] for i in out] == ["first", "second"]


# --- _filter_items (shape + citation together) ---


class TestFilterItems:
    def test_accepts_valid_cited_item(self):
        items = [_valid_item()]
        accepted, shape_rej, cite_rej = _filter_items(items, known_pattern_ids={"abc-123"})
        assert len(accepted) == 1
        assert shape_rej == 0
        assert cite_rej == 0

    def test_rejects_bad_shape_before_checking_citations(self):
        bad = _valid_item()
        del bad["headline"]
        accepted, shape_rej, cite_rej = _filter_items([bad], known_pattern_ids={"abc-123"})
        assert accepted == []
        assert shape_rej == 1
        assert cite_rej == 0  # short-circuited

    def test_rejects_hallucinated_citation(self):
        item = _valid_item()
        item["pattern_ids"] = ["fake-id"]
        accepted, shape_rej, cite_rej = _filter_items([item], known_pattern_ids={"real-id"})
        assert accepted == []
        assert shape_rej == 0
        assert cite_rej == 1

    def test_mixed_batch(self):
        good = _valid_item()
        bad_shape = _valid_item()
        del bad_shape["test_plan"]
        bad_cite = _valid_item()
        bad_cite["pattern_ids"] = ["nope"]

        accepted, shape_rej, cite_rej = _filter_items(
            [good, bad_shape, bad_cite],
            known_pattern_ids={"abc-123"},
        )
        assert len(accepted) == 1
        assert shape_rej == 1
        assert cite_rej == 1


# --- _retry_prompt ---


class TestRetryPrompt:
    def test_appends_correction_note(self):
        base = "original prompt body"
        retry = _retry_prompt(base, {"a", "b"})
        assert retry.startswith(base)
        assert "zero valid items" in retry
        assert "Do not invent UUIDs" in retry


# --- Tool schema sanity ---


class TestToolSchema:
    def test_tool_has_required_top_level_fields(self):
        assert "name" in SHIP_LIST_TOOL
        assert "input_schema" in SHIP_LIST_TOOL
        assert SHIP_LIST_TOOL["name"] == "save_ship_list"

    def test_tool_enforces_item_shape(self):
        schema = SHIP_LIST_TOOL["input_schema"]
        item_schema = schema["properties"]["items"]["items"]
        required = set(item_schema["required"])
        assert {
            "rank", "headline", "recommendation", "test_plan",
            "effort_estimate", "confidence", "pattern_ids",
        } <= required

    def test_tool_caps_item_count(self):
        assert SHIP_LIST_TOOL["input_schema"]["properties"]["items"]["maxItems"] == MAX_SHIP_ITEMS


# --- LLM cost math ---


class TestUsageCostCents:
    def test_zero_tokens_zero_cost(self):
        assert usage_cost_cents(0, 0, "claude-sonnet-4-20250514") == 0

    def test_sonnet_4_pricing(self):
        # 1M input tokens at $3/M = $3.00 = 300 cents
        assert usage_cost_cents(1_000_000, 0, "claude-sonnet-4-20250514") == 300
        # 1M output tokens at $15/M = $15.00 = 1500 cents
        assert usage_cost_cents(0, 1_000_000, "claude-sonnet-4-20250514") == 1500

    def test_unknown_model_falls_back(self):
        # Fallback is Sonnet 4 pricing.
        fallback_cost = usage_cost_cents(1_000_000, 0, "claude-unknown-model")
        sonnet_cost = usage_cost_cents(1_000_000, 0, "claude-sonnet-4-20250514")
        assert fallback_cost == sonnet_cost

    def test_small_calls_round_up(self):
        # 1000 input tokens × $3/M = $0.003 = 0.3 cents → rounds up to 1.
        assert usage_cost_cents(1000, 0, "claude-sonnet-4-20250514") == 1

    def test_opus_more_expensive_than_sonnet(self):
        opus = usage_cost_cents(1_000_000, 1_000_000, "claude-opus-4-20250514")
        sonnet = usage_cost_cents(1_000_000, 1_000_000, "claude-sonnet-4-20250514")
        assert opus > sonnet


class TestProjectedMaxCost:
    def test_scales_with_prompt_size(self):
        small = projected_max_cost_cents(prompt_chars=100, max_output_tokens=1000, model="claude-sonnet-4-20250514")
        big = projected_max_cost_cents(prompt_chars=100_000, max_output_tokens=1000, model="claude-sonnet-4-20250514")
        assert big > small

    def test_scales_with_max_output(self):
        short = projected_max_cost_cents(prompt_chars=1000, max_output_tokens=100, model="claude-sonnet-4-20250514")
        long = projected_max_cost_cents(prompt_chars=1000, max_output_tokens=10_000, model="claude-sonnet-4-20250514")
        assert long > short

    def test_tiny_prompt_never_zero(self):
        # Even an empty prompt should project at least the max output cost.
        assert projected_max_cost_cents(prompt_chars=0, max_output_tokens=1000, model="claude-sonnet-4-20250514") > 0


class TestRoundUpCents:
    def test_exact_integer_cents(self):
        assert _round_up_cents(1.00) == 100

    def test_fractional_rounds_up(self):
        assert _round_up_cents(0.001) == 1  # 0.1¢ → 1¢
        assert _round_up_cents(0.015) == 2  # 1.5¢ → 2¢

    def test_zero(self):
        assert _round_up_cents(0.0) == 0
