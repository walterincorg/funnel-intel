"""Tests for pattern extraction pure logic.

The detectors themselves hit the database, so they're out of scope for unit
tests. This file covers the pure helpers that the detectors depend on:
  - score_confidence
  - compute_signature (stability + discrimination)
  - _extract_min_price (heterogeneous plans blob parsing)
  - _pct_change
  - _days_between
  - pattern_to_json
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.worker.pattern_extraction import (
    PATTERN_AD_ANGLE_SHIFT,
    PATTERN_PRICE_MOVE,
    PATTERN_PROVEN_WINNER,
    VALID_PATTERN_TYPES,
    _days_between,
    _extract_min_price,
    _pct_change,
    compute_signature,
    pattern_to_json,
    score_confidence,
)


class TestScoreConfidence:
    def test_single_competitor_minimal_evidence_short_window(self):
        # Base 5, no bumps.
        score = score_confidence(competitor_count=1, evidence_count=0, time_window_days=7)
        assert score == 5.0

    def test_two_competitors_bumps_by_two(self):
        score = score_confidence(competitor_count=2, evidence_count=0, time_window_days=7)
        assert score == 7.0

    def test_three_competitors_bumps_by_three(self):
        # +2 for 2+, +1 for 3+
        score = score_confidence(competitor_count=3, evidence_count=0, time_window_days=7)
        assert score == 8.0

    def test_long_window_bumps_by_one(self):
        score = score_confidence(competitor_count=1, evidence_count=0, time_window_days=14)
        assert score == 6.0

    def test_evidence_volume_bumps_gradually(self):
        # +1.0 for 5 pieces, +2.0 cap.
        assert score_confidence(1, evidence_count=5, time_window_days=0) == 6.0
        assert score_confidence(1, evidence_count=10, time_window_days=0) == 7.0
        assert score_confidence(1, evidence_count=100, time_window_days=0) == 7.0  # capped

    def test_full_stack_caps_at_ten(self):
        # Everything maxed: base 5 + 2 + 1 + 1 + 2 = 11, capped at 10.
        score = score_confidence(competitor_count=5, evidence_count=100, time_window_days=90)
        assert score == 10.0

    def test_custom_base(self):
        # Winners get base=6 per the module.
        score = score_confidence(1, 0, 0, base=6.0)
        assert score == 6.0


class TestComputeSignature:
    def test_valid_type_returns_32_char_hex(self):
        sig = compute_signature(PATTERN_PROVEN_WINNER, ["cluster-abc"])
        assert len(sig) == 32
        assert all(c in "0123456789abcdef" for c in sig)

    def test_deterministic(self):
        a = compute_signature(PATTERN_PROVEN_WINNER, ["cluster-abc"])
        b = compute_signature(PATTERN_PROVEN_WINNER, ["cluster-abc"])
        assert a == b

    def test_order_independent(self):
        # Evidence ordering should not affect signature — patterns are a set.
        a = compute_signature(PATTERN_AD_ANGLE_SHIFT, ["comp-1", "old-cluster", "new-cluster"])
        b = compute_signature(PATTERN_AD_ANGLE_SHIFT, ["new-cluster", "comp-1", "old-cluster"])
        assert a == b

    def test_different_types_different_signatures(self):
        # Same evidence, different pattern_type must collide to different sigs.
        a = compute_signature(PATTERN_PROVEN_WINNER, ["cluster-abc"])
        b = compute_signature("killed_test", ["cluster-abc"])
        assert a != b

    def test_different_evidence_different_signatures(self):
        a = compute_signature(PATTERN_PROVEN_WINNER, ["cluster-abc"])
        b = compute_signature(PATTERN_PROVEN_WINNER, ["cluster-def"])
        assert a != b

    def test_rejects_unknown_pattern_type(self):
        with pytest.raises(ValueError):
            compute_signature("made_up_pattern", ["x"])

    def test_all_known_types_produce_signatures(self):
        # Smoke check — every valid pattern type hashes without error.
        for t in VALID_PATTERN_TYPES:
            sig = compute_signature(t, ["x"])
            assert len(sig) == 32


class TestExtractMinPrice:
    def test_none_returns_none(self):
        assert _extract_min_price(None) is None

    def test_empty_dict_returns_none(self):
        assert _extract_min_price({}) is None

    def test_single_numeric_value(self):
        assert _extract_min_price({"price": 49.99}) == 49.99

    def test_picks_minimum_across_plans(self):
        plans = {
            "monthly": {"price": 49.99},
            "quarterly": {"price": 29.99},
            "annual": {"price": 19.99},
        }
        assert _extract_min_price(plans) == 19.99

    def test_parses_string_price_with_dollar_sign(self):
        assert _extract_min_price({"label": "$49.99"}) == 49.99

    def test_parses_european_comma_decimal(self):
        assert _extract_min_price({"label": "49,99 €"}) == 49.99

    def test_ignores_out_of_range_numbers(self):
        # 999999 is too big to be a price, 0 too small.
        plans = {"weird": 999999, "also_weird": 0, "real": 9.99}
        assert _extract_min_price(plans) == 9.99

    def test_handles_list_of_plans(self):
        plans = [
            {"name": "basic", "price": 9.99},
            {"name": "pro", "price": 29.99},
        ]
        assert _extract_min_price(plans) == 9.99

    def test_handles_nested_structures(self):
        plans = {
            "tiers": [
                {"prices": {"monthly": 49.99, "trial": 1.00}},
                {"prices": {"monthly": 99.99}},
            ]
        }
        assert _extract_min_price(plans) == 1.00

    def test_missing_numeric_returns_none(self):
        plans = {"label": "free trial", "duration": "7 days"}
        assert _extract_min_price(plans) is None


class TestPctChange:
    def test_zero_from_returns_zero(self):
        assert _pct_change(0, 100) == 0.0

    def test_positive_change(self):
        assert _pct_change(50, 60) == pytest.approx(0.2)

    def test_negative_change(self):
        assert _pct_change(100, 80) == pytest.approx(-0.2)

    def test_no_change(self):
        assert _pct_change(50, 50) == 0.0


class TestDaysBetween:
    def test_none_returns_zero(self):
        now = datetime(2026, 4, 15, tzinfo=timezone.utc)
        assert _days_between(None, now) == 0

    def test_unparseable_returns_zero(self):
        now = datetime(2026, 4, 15, tzinfo=timezone.utc)
        assert _days_between("not a date", now) == 0

    def test_recent_timestamp(self):
        now = datetime(2026, 4, 15, tzinfo=timezone.utc)
        ten_days_ago = (now - timedelta(days=10)).isoformat()
        assert _days_between(ten_days_ago, now) == 10

    def test_handles_z_suffix(self):
        now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        ts = "2026-04-10T12:00:00Z"
        assert _days_between(ts, now) == 5

    def test_handles_naive_timestamp(self):
        # Postgres sometimes returns timestamps without an explicit tz.
        now = datetime(2026, 4, 15, tzinfo=timezone.utc)
        ts = "2026-04-10T00:00:00"
        assert _days_between(ts, now) == 5

    def test_future_timestamp_returns_zero(self):
        now = datetime(2026, 4, 15, tzinfo=timezone.utc)
        future = (now + timedelta(days=5)).isoformat()
        assert _days_between(future, now) == 0


class TestPatternToJson:
    def test_dumps_dict_to_indented_json(self):
        pattern = {
            "pattern_type": PATTERN_PRICE_MOVE,
            "confidence": 7.5,
            "evidence_refs": [{"type": "pricing_snapshot", "id": "abc"}],
        }
        out = pattern_to_json(pattern)
        assert '"pattern_type"' in out
        assert '"price_move"' in out
        assert "\n" in out  # indented

    def test_handles_datetime_fallback(self):
        # default=str in pattern_to_json lets datetime through.
        pattern = {"created_at": datetime(2026, 4, 15, tzinfo=timezone.utc)}
        out = pattern_to_json(pattern)
        assert "2026-04-15" in out
