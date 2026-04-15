"""Tests for feedback loop pure helpers.

Out of scope (needs DB):
  - maybe_run_feedback_check orchestration

In scope:
  - is_item_due_for_outcome: every branch of the gate
  - filter_items_needing_outcome: batch filtering
  - format_outcome_prompt: empty, single, multi, truncation
"""

from datetime import datetime, timedelta, timezone

from backend.worker.feedback_loop import (
    filter_items_needing_outcome,
    format_outcome_prompt,
    is_item_due_for_outcome,
)

NOW = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)


def _item(**overrides) -> dict:
    base = {
        "id": "item-1",
        "rank": 1,
        "headline": "Replace question 4 with goal-first framing",
        "status": "shipping",
        "shipping_at": (NOW - timedelta(days=15)).isoformat(),
        "outcome_alerted_at": None,
    }
    base.update(overrides)
    return base


class TestIsItemDueForOutcome:
    def test_shipping_past_window_is_due(self):
        assert is_item_due_for_outcome(
            _item(), now=NOW, wait_days=14, items_with_outcomes=set()
        ) is True

    def test_exactly_fourteen_days_is_due(self):
        item = _item(shipping_at=(NOW - timedelta(days=14)).isoformat())
        assert is_item_due_for_outcome(
            item, now=NOW, wait_days=14, items_with_outcomes=set()
        ) is True

    def test_under_window_not_due(self):
        item = _item(shipping_at=(NOW - timedelta(days=13)).isoformat())
        assert is_item_due_for_outcome(
            item, now=NOW, wait_days=14, items_with_outcomes=set()
        ) is False

    def test_already_alerted_not_due(self):
        item = _item(outcome_alerted_at=(NOW - timedelta(days=1)).isoformat())
        assert is_item_due_for_outcome(
            item, now=NOW, wait_days=14, items_with_outcomes=set()
        ) is False

    def test_outcome_recorded_not_due(self):
        assert is_item_due_for_outcome(
            _item(), now=NOW, wait_days=14, items_with_outcomes={"item-1"},
        ) is False

    def test_proposed_status_not_due(self):
        item = _item(status="proposed", shipping_at=None)
        assert is_item_due_for_outcome(
            item, now=NOW, wait_days=14, items_with_outcomes=set()
        ) is False

    def test_skipped_status_not_due(self):
        item = _item(status="skipped")
        assert is_item_due_for_outcome(
            item, now=NOW, wait_days=14, items_with_outcomes=set()
        ) is False

    def test_shipped_status_is_due(self):
        # Founder marked it complete but never recorded outcome.
        item = _item(status="shipped")
        assert is_item_due_for_outcome(
            item, now=NOW, wait_days=14, items_with_outcomes=set()
        ) is True

    def test_null_shipping_at_not_due(self):
        item = _item(shipping_at=None)
        assert is_item_due_for_outcome(
            item, now=NOW, wait_days=14, items_with_outcomes=set()
        ) is False

    def test_unparseable_shipping_at_not_due(self):
        item = _item(shipping_at="not-a-date")
        assert is_item_due_for_outcome(
            item, now=NOW, wait_days=14, items_with_outcomes=set()
        ) is False

    def test_z_suffix_parses(self):
        item = _item(shipping_at="2026-04-05T12:00:00Z")
        assert is_item_due_for_outcome(
            item, now=NOW, wait_days=14, items_with_outcomes=set()
        ) is True

    def test_naive_timestamp_parses(self):
        # Postgres sometimes returns naive timestamps.
        item = _item(shipping_at="2026-04-05T12:00:00")
        assert is_item_due_for_outcome(
            item, now=NOW, wait_days=14, items_with_outcomes=set()
        ) is True

    def test_custom_wait_days(self):
        item = _item(shipping_at=(NOW - timedelta(days=8)).isoformat())
        assert is_item_due_for_outcome(
            item, now=NOW, wait_days=7, items_with_outcomes=set()
        ) is True
        assert is_item_due_for_outcome(
            item, now=NOW, wait_days=14, items_with_outcomes=set()
        ) is False


class TestFilterItemsNeedingOutcome:
    def test_empty_list(self):
        assert filter_items_needing_outcome(
            [], now=NOW, wait_days=14, items_with_outcomes=set()
        ) == []

    def test_mixed_batch(self):
        items = [
            _item(id="a", headline="due 1"),
            _item(id="b", headline="under window", shipping_at=(NOW - timedelta(days=5)).isoformat()),
            _item(id="c", headline="already alerted", outcome_alerted_at=NOW.isoformat()),
            _item(id="d", headline="due 2"),
            _item(id="e", headline="has outcome"),
        ]
        due = filter_items_needing_outcome(
            items,
            now=NOW,
            wait_days=14,
            items_with_outcomes={"e"},
        )
        assert [i["headline"] for i in due] == ["due 1", "due 2"]


class TestFormatOutcomePrompt:
    def test_empty_is_empty_string(self):
        assert format_outcome_prompt([], wait_days=14) == ""

    def test_single_item(self):
        items = [_item(rank=3, headline="Test a new hook")]
        out = format_outcome_prompt(items, wait_days=14)
        assert "1 ship list item" in out
        assert "14-day" in out
        assert "#3: Test a new hook" in out

    def test_multiple_items_uses_plural(self):
        items = [_item(id=f"i-{i}", rank=i, headline=f"Headline {i}") for i in range(3)]
        out = format_outcome_prompt(items, wait_days=14)
        assert "3 ship list items" in out
        assert "#0: Headline 0" in out
        assert "#2: Headline 2" in out

    def test_truncates_at_ten_items(self):
        items = [_item(id=f"i-{i}", rank=i, headline=f"H{i}") for i in range(15)]
        out = format_outcome_prompt(items, wait_days=14)
        # Should only render 10 of them plus a summary line.
        assert "and 5 more" in out
        for i in range(10):
            assert f"#{i}: H{i}" in out
        assert "#14:" not in out

    def test_long_headline_truncated(self):
        long = "a" * 200
        items = [_item(headline=long)]
        out = format_outcome_prompt(items, wait_days=14)
        # Should cap headline rendering at ~80 chars.
        assert "a" * 80 in out
        assert "a" * 120 not in out

    def test_missing_headline_fallback(self):
        items = [_item(headline=None)]
        out = format_outcome_prompt(items, wait_days=14)
        assert "(no headline)" in out

    def test_custom_wait_days_in_header(self):
        items = [_item()]
        out = format_outcome_prompt(items, wait_days=7)
        assert "7-day" in out
