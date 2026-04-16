"""Tests for the semantic scan run differ."""

from unittest.mock import MagicMock, patch

import pytest

from backend.worker.differ import (
    Change,
    DiffResult,
    _deduplicate_steps,
    _normalize_price,
    _parse_alignments,
    diff_runs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _step(num, q=None, opts=None, stype="question"):
    return {"step_number": num, "question_text": q, "answer_options": opts, "step_type": stype}


def _mock_tool_response(alignments):
    block = MagicMock()
    block.type = "tool_use"
    block.name = "save_diff_result"
    block.input = {"alignments": alignments}
    resp = MagicMock()
    resp.content = [block]
    return resp


def _mock_text_response():
    """Response without tool_use — triggers error."""
    block = MagicMock()
    block.type = "text"
    block.text = "I cannot compare."
    resp = MagicMock()
    resp.content = [block]
    return resp


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_keeps_longest_question(self):
        steps = [
            _step(1, "Age?"),
            _step(1, "How old are you?"),
            _step(2, "Goal?"),
        ]
        result = _deduplicate_steps(steps)
        assert len(result) == 2
        assert result[0]["question_text"] == "How old are you?"

    def test_handles_none_question(self):
        steps = [
            _step(1, None),
            _step(1, "Age?"),
        ]
        result = _deduplicate_steps(steps)
        assert len(result) == 1
        assert result[0]["question_text"] == "Age?"

    def test_coerces_string_step_number(self):
        steps = [_step("3", "Weight?"), _step(1, "Age?")]
        result = _deduplicate_steps(steps)
        assert len(result) == 2
        assert result[0]["step_number"] == 1

    def test_skips_invalid_step_number(self):
        steps = [{"step_number": "not-a-number", "question_text": "Bad"}]
        result = _deduplicate_steps(steps)
        assert len(result) == 0

    def test_empty_input(self):
        assert _deduplicate_steps([]) == []


# ---------------------------------------------------------------------------
# Price normalization
# ---------------------------------------------------------------------------

class TestPriceNormalization:
    def test_strips_dollar(self):
        assert _normalize_price("$29.99") == "29.99"

    def test_strips_whitespace(self):
        assert _normalize_price("  29.99  ") == "29.99"

    def test_combined(self):
        assert _normalize_price(" $29.99 ") == "29.99"

    def test_none(self):
        assert _normalize_price(None) == ""

    def test_numeric(self):
        assert _normalize_price(29.99) == "29.99"


# ---------------------------------------------------------------------------
# Alignment parser
# ---------------------------------------------------------------------------

class TestParseAlignments:
    def test_same_question_no_changes(self):
        alignments = [
            {"baseline_step": 1, "latest_step": 1,
             "question_verdict": "SAME", "options_verdict": "SAME",
             "explanation": "Identical"},
        ]
        changes = _parse_alignments(alignments)
        assert len(changes) == 0

    def test_cosmetic_question(self):
        alignments = [
            {"baseline_step": 1, "latest_step": 2,
             "question_verdict": "COSMETIC", "options_verdict": "SAME",
             "explanation": "Same age question"},
        ]
        changes = _parse_alignments(alignments)
        assert len(changes) == 1
        assert changes[0].severity == "low"
        assert "reworded" in changes[0].description.lower()

    def test_different_question(self):
        alignments = [
            {"baseline_step": 4, "latest_step": 4,
             "question_verdict": "DIFFERENT", "options_verdict": "CHANGED",
             "explanation": "Different intent"},
        ]
        changes = _parse_alignments(alignments)
        assert len(changes) == 1
        assert changes[0].severity == "high"

    def test_removed_step(self):
        alignments = [
            {"baseline_step": 5, "latest_step": None,
             "question_verdict": "REMOVED", "options_verdict": "N_A",
             "explanation": "Name input removed"},
        ]
        changes = _parse_alignments(alignments)
        assert len(changes) == 1
        assert changes[0].severity == "medium"
        assert "removed" in changes[0].description.lower()

    def test_new_step(self):
        alignments = [
            {"baseline_step": None, "latest_step": 0,
             "question_verdict": "NEW", "options_verdict": "N_A",
             "explanation": "Gender selection added"},
        ]
        changes = _parse_alignments(alignments)
        assert len(changes) == 1
        assert changes[0].severity == "medium"
        assert "new" in changes[0].description.lower()

    def test_variable_options_ab_test(self):
        alignments = [
            {"baseline_step": 7, "latest_step": 7,
             "question_verdict": "SAME", "options_verdict": "VARIABLE",
             "explanation": "Same question, different influencer list"},
        ]
        changes = _parse_alignments(alignments)
        assert len(changes) == 1
        assert changes[0].severity == "low"
        assert "a/b" in changes[0].description.lower()

    def test_options_changed_on_same_question(self):
        alignments = [
            {"baseline_step": 5, "latest_step": 5,
             "question_verdict": "COSMETIC", "options_verdict": "CHANGED",
             "explanation": "Options restructured"},
        ]
        changes = _parse_alignments(alignments)
        assert len(changes) == 2
        assert changes[0].severity == "low"   # cosmetic question
        assert changes[1].severity == "medium"  # options changed

    def test_missing_fields_default_safe(self):
        alignments = [{"baseline_step": 1, "latest_step": 1}]
        changes = _parse_alignments(alignments)
        assert len(changes) == 0  # defaults to SAME + N_A


# ---------------------------------------------------------------------------
# Full diff_runs with mocked LLM
# ---------------------------------------------------------------------------

class TestDiffRuns:
    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_semantic_diff_works(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_tool_response([
            {"baseline_step": 1, "latest_step": 1,
             "question_verdict": "SAME", "options_verdict": "SAME",
             "explanation": "identical"},
        ])

        bl = [_step(1, "Age?")]
        lt = [_step(1, "Age?")]
        result = diff_runs(bl, lt, None, None)

        mock_client.messages.create.assert_called_once()
        assert result.drift_level == "none"

    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_detects_different_question(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_tool_response([
            {"baseline_step": 4, "latest_step": 4,
             "question_verdict": "DIFFERENT", "options_verdict": "CHANGED",
             "explanation": "Question reframed"},
        ])

        result = diff_runs([_step(4, "Worked with therapist?")],
                           [_step(4, "Did therapist suggest us?")], None, None)

        high = [c for c in result.changes if c.severity == "high" and c.category == "funnel"]
        assert len(high) == 1

    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_api_error_propagates(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("rate limited")

        with pytest.raises(Exception, match="rate limited"):
            diff_runs([_step(1, "Age?")], [_step(1, "Age?")], None, None)

    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_no_tool_use_raises(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_text_response()

        with pytest.raises(ValueError, match="save_diff_result"):
            diff_runs([_step(1, "Age?")], [_step(1, "Age?")], None, None)

    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_pricing_comparison(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_tool_response([
            {"baseline_step": 1, "latest_step": 1,
             "question_verdict": "SAME", "options_verdict": "SAME",
             "explanation": "identical"},
        ])

        bl_p = {"plans": [{"name": "Monthly", "price": "29.99"}]}
        lt_p = {"plans": [{"name": "Monthly", "price": "19.99"}]}
        result = diff_runs([_step(1, "Age?")], [_step(1, "Age?")], bl_p, lt_p)

        pricing_changes = [c for c in result.changes if c.category == "pricing"]
        assert len(pricing_changes) == 1
        assert "price changed" in pricing_changes[0].description.lower()

    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_pricing_format_noise_ignored(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_tool_response([
            {"baseline_step": 1, "latest_step": 1,
             "question_verdict": "SAME", "options_verdict": "SAME",
             "explanation": "identical"},
        ])

        bl_p = {"plans": [{"name": "Monthly", "price": "29.99"}]}
        lt_p = {"plans": [{"name": "Monthly", "price": "$29.99"}]}
        result = diff_runs([_step(1, "Age?")], [_step(1, "Age?")], bl_p, lt_p)

        pricing_changes = [c for c in result.changes if c.category == "pricing"]
        assert len(pricing_changes) == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_baseline(self):
        result = diff_runs([], [_step(1, "Age?")], None, None)
        assert result.has_changes

    def test_empty_latest(self):
        result = diff_runs([_step(1, "Age?")], [], None, None)
        assert result.has_changes

    def test_both_empty(self):
        result = diff_runs([], [], None, None)
        assert result.drift_level == "none"

    def test_duplicate_steps_deduped(self):
        bl = [_step(1, "Age?"), _step(1, "Age?"), _step(1, "Age?")]
        lt = [_step(1, "Age?")]
        # With no API key, this will raise — but dedup itself should work
        deduped = _deduplicate_steps(bl)
        assert len(deduped) == 1

    def test_mixed_step_number_types(self):
        steps = [{"step_number": "1", "question_text": "Goal?"},
                 {"step_number": 2, "question_text": "Age?"},
                 {"step_number": "3", "question_text": "Weight?"}]
        deduped = _deduplicate_steps(steps)
        assert len(deduped) == 3
        assert all(isinstance(s["step_number"], int) for s in deduped)
