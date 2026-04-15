"""Regression tests for the scan run differ."""

from backend.worker.differ import diff_runs


def test_diff_handles_mixed_int_and_string_step_numbers():
    """Baseline from DB has int step_numbers; LLM-parsed new steps sometimes
    arrive as strings ("36"). `sorted()` over the union used to raise
    TypeError — now both sides are coerced to int."""
    baseline_steps = [
        {"step_number": 1, "question_text": "Goal?", "answer_options": None},
        {"step_number": 2, "question_text": "Age?", "answer_options": None},
    ]
    new_steps = [
        {"step_number": "1", "question_text": "Goal?", "answer_options": None},
        {"step_number": "2", "question_text": "Age?", "answer_options": None},
        {"step_number": "3", "question_text": "Weight?", "answer_options": None},
    ]

    result = diff_runs(baseline_steps, new_steps, None, None)

    assert result.has_changes
    descriptions = [c.description for c in result.changes]
    assert any("1 more steps" in d or "more steps" in d for d in descriptions)
    assert any("New step 3" in d for d in descriptions)


def test_diff_tolerates_missing_or_invalid_step_number():
    baseline_steps = [{"step_number": 1, "question_text": "Goal?"}]
    new_steps = [
        {"step_number": 1, "question_text": "Goal?"},
        {"question_text": "Orphan"},
        {"step_number": "not-a-number", "question_text": "Garbage"},
    ]

    result = diff_runs(baseline_steps, new_steps, None, None)
    assert result.drift_level in ("none", "minor")
