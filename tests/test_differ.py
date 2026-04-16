"""Tests for the semantic scan run differ."""

from unittest.mock import MagicMock, patch

import pytest

from backend.worker.differ import (
    Change,
    DiffResult,
    _deduplicate_steps,
    diff_runs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _step(num, q=None, opts=None, stype="question"):
    return {"step_number": num, "question_text": q, "answer_options": opts, "step_type": stype}


def _mock_tool_response(alignments):
    """Mock Call 1 (step alignment) response."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "save_diff_result"
    block.input = {"alignments": alignments}
    resp = MagicMock()
    resp.content = [block]
    return resp


def _mock_eval_response(
    drift_level="none",
    pricing_changed=False,
    pricing_summary="No change",
    alert_worthy_changes=None,
    changes=None,
):
    """Mock Call 2 (evaluation) response."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "save_evaluation"
    block.input = {
        "drift_level": drift_level,
        "pricing_changed": pricing_changed,
        "pricing_summary": pricing_summary,
        "alert_worthy_changes": alert_worthy_changes or [],
        "changes": changes or [],
    }
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
# Full diff_runs with mocked LLM (two calls)
# ---------------------------------------------------------------------------

class TestDiffRuns:
    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_no_changes(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.side_effect = [
            _mock_tool_response([
                {"baseline_step": 1, "latest_step": 1,
                 "question_verdict": "SAME", "options_verdict": "SAME",
                 "explanation": "identical"},
            ]),
            _mock_eval_response(drift_level="none"),
        ]

        result = diff_runs([_step(1, "Age?")], [_step(1, "Age?")], None, None)

        assert mock_client.messages.create.call_count == 2
        assert result.drift_level == "none"
        assert result.changes == []

    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_genuinely_new_question_is_high(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.side_effect = [
            _mock_tool_response([
                {"baseline_step": 1, "latest_step": 1,
                 "question_verdict": "SAME", "options_verdict": "SAME",
                 "explanation": "identical"},
                {"baseline_step": None, "latest_step": 2,
                 "question_verdict": "NEW", "options_verdict": "N_A",
                 "explanation": "Gender selection added"},
            ]),
            _mock_eval_response(
                drift_level="major",
                alert_worthy_changes=["New question: Gender selection added"],
                changes=[{
                    "baseline_step": None, "latest_step": 2,
                    "final_severity": "high", "category": "funnel",
                    "description": "New question: Gender selection added",
                }],
            ),
        ]

        result = diff_runs(
            [_step(1, "Age?")],
            [_step(1, "Age?"), _step(2, "Gender?")],
            None, None,
        )

        assert result.drift_level == "major"
        high = [c for c in result.changes if c.severity == "high"]
        assert len(high) == 1
        assert result.alert_worthy_changes == ["New question: Gender selection added"]

    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_cosmetic_reword_is_low(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.side_effect = [
            _mock_tool_response([
                {"baseline_step": 1, "latest_step": 1,
                 "question_verdict": "COSMETIC", "options_verdict": "SAME",
                 "explanation": "Same age question, reworded"},
            ]),
            _mock_eval_response(
                drift_level="none",
                changes=[{
                    "baseline_step": 1, "latest_step": 1,
                    "final_severity": "low", "category": "funnel",
                    "description": "Step reworded: same age question",
                }],
            ),
        ]

        result = diff_runs(
            [_step(1, "How old are you?")],
            [_step(1, "What's your age?")],
            None, None,
        )

        assert result.drift_level == "none"
        assert all(c.severity == "low" for c in result.changes)
        assert result.alert_worthy_changes == []

    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_pricing_change_detected_by_llm(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.side_effect = [
            _mock_tool_response([
                {"baseline_step": 1, "latest_step": 1,
                 "question_verdict": "SAME", "options_verdict": "SAME",
                 "explanation": "identical"},
            ]),
            _mock_eval_response(
                drift_level="major",
                pricing_changed=True,
                pricing_summary="Monthly plan price increased from $29.99 to $39.99",
                alert_worthy_changes=["Monthly plan price increased from $29.99 to $39.99"],
                changes=[{
                    "baseline_step": None, "latest_step": None,
                    "final_severity": "high", "category": "pricing",
                    "description": "Monthly plan price increased from $29.99 to $39.99",
                }],
            ),
        ]

        bl_p = {"plans": [{"name": "Monthly", "price": "$29.99"}]}
        lt_p = {"plans": [{"name": "Monthly", "price": "$39.99"}]}
        result = diff_runs([_step(1, "Age?")], [_step(1, "Age?")], bl_p, lt_p)

        assert result.pricing_changed is True
        assert result.drift_level == "major"
        pricing = [c for c in result.changes if c.category == "pricing"]
        assert len(pricing) == 1
        assert pricing[0].severity == "high"
        assert len(result.alert_worthy_changes) == 1

    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_pricing_format_noise_ignored_by_llm(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.side_effect = [
            _mock_tool_response([
                {"baseline_step": 1, "latest_step": 1,
                 "question_verdict": "SAME", "options_verdict": "SAME",
                 "explanation": "identical"},
            ]),
            _mock_eval_response(
                drift_level="none",
                pricing_changed=False,
                pricing_summary="No change",
            ),
        ]

        bl_p = {"plans": [{"name": "Monthly", "price": "29.99"}]}
        lt_p = {"plans": [{"name": "Monthly", "price": "$29.99"}]}
        result = diff_runs([_step(1, "Age?")], [_step(1, "Age?")], bl_p, lt_p)

        assert result.pricing_changed is False
        assert result.drift_level == "none"

    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_drift_level_from_llm(self, mock_cls):
        """drift_level comes from Call 2, not threshold counting."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.side_effect = [
            _mock_tool_response([
                {"baseline_step": 1, "latest_step": 1,
                 "question_verdict": "DIFFERENT", "options_verdict": "CHANGED",
                 "explanation": "Question reframed"},
            ]),
            _mock_eval_response(
                drift_level="minor",
                changes=[{
                    "baseline_step": 1, "latest_step": 1,
                    "final_severity": "low", "category": "funnel",
                    "description": "Question slightly reframed but same intent",
                }],
            ),
        ]

        result = diff_runs(
            [_step(1, "Worked with therapist?")],
            [_step(1, "Did therapist suggest us?")],
            None, None,
        )

        # LLM says minor even though old code would have said high
        assert result.drift_level == "minor"
        assert all(c.severity == "low" for c in result.changes)

    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_none_severity_filtered(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.side_effect = [
            _mock_tool_response([
                {"baseline_step": 1, "latest_step": 1,
                 "question_verdict": "COSMETIC", "options_verdict": "SAME",
                 "explanation": "Trivial rewording"},
            ]),
            _mock_eval_response(
                drift_level="none",
                changes=[{
                    "baseline_step": 1, "latest_step": 1,
                    "final_severity": "none", "category": "funnel",
                    "description": "Not a real change",
                }],
            ),
        ]

        result = diff_runs([_step(1, "Age?")], [_step(1, "Your age?")], None, None)

        assert result.changes == []
        assert result.drift_level == "none"

    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_call1_api_error_propagates(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("rate limited")

        with pytest.raises(Exception, match="rate limited"):
            diff_runs([_step(1, "Age?")], [_step(1, "Age?")], None, None)

    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_call1_no_tool_use_raises(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_text_response()

        with pytest.raises(ValueError, match="save_diff_result"):
            diff_runs([_step(1, "Age?")], [_step(1, "Age?")], None, None)

    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_call2_no_tool_use_raises(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.side_effect = [
            _mock_tool_response([
                {"baseline_step": 1, "latest_step": 1,
                 "question_verdict": "SAME", "options_verdict": "SAME",
                 "explanation": "identical"},
            ]),
            _mock_text_response(),
        ]

        with pytest.raises(ValueError, match="save_evaluation"):
            diff_runs([_step(1, "Age?")], [_step(1, "Age?")], None, None)

    @patch("backend.worker.differ.anthropic.Anthropic")
    @patch("backend.worker.differ._API_KEY", "sk-test")
    def test_call2_api_error_propagates(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.side_effect = [
            _mock_tool_response([
                {"baseline_step": 1, "latest_step": 1,
                 "question_verdict": "SAME", "options_verdict": "SAME",
                 "explanation": "identical"},
            ]),
            Exception("timeout"),
        ]

        with pytest.raises(Exception, match="timeout"):
            diff_runs([_step(1, "Age?")], [_step(1, "Age?")], None, None)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_baseline(self):
        result = diff_runs([], [_step(1, "Age?")], None, None)
        assert result.has_changes
        assert result.drift_level == "minor"

    def test_empty_latest(self):
        result = diff_runs([_step(1, "Age?")], [], None, None)
        assert result.has_changes
        assert result.drift_level == "minor"

    def test_both_empty(self):
        result = diff_runs([], [], None, None)
        assert result.drift_level == "none"

    def test_duplicate_steps_deduped(self):
        bl = [_step(1, "Age?"), _step(1, "Age?"), _step(1, "Age?")]
        deduped = _deduplicate_steps(bl)
        assert len(deduped) == 1

    def test_mixed_step_number_types(self):
        steps = [{"step_number": "1", "question_text": "Goal?"},
                 {"step_number": 2, "question_text": "Age?"},
                 {"step_number": "3", "question_text": "Weight?"}]
        deduped = _deduplicate_steps(steps)
        assert len(deduped) == 3
        assert all(isinstance(s["step_number"], int) for s in deduped)
