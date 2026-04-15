"""Tests for synthesis loop pure helpers.

Out of scope (needs DB):
  - maybe_run_synthesis, _run_synthesis, _claim_or_create_run,
    _finish_aborted_stale, cleanup_stale_synthesis_runs, _alert_on_final_status

In scope:
  - _compute_week_of (timezone + weekday edge cases)
  - _build_stale_error_message
  - _map_ship_status_to_run_status
"""

from datetime import date, datetime, timezone

from backend.worker.synthesis_loop import (
    _build_stale_error_message,
    _compute_week_of,
    _map_ship_status_to_run_status,
)


class TestComputeWeekOf:
    def test_monday_returns_same_day(self):
        # 2026-04-13 is a Monday.
        now = datetime(2026, 4, 13, 7, 0, 0, tzinfo=timezone.utc)
        assert _compute_week_of(now) == date(2026, 4, 13)

    def test_tuesday_returns_previous_monday(self):
        now = datetime(2026, 4, 14, 7, 0, 0, tzinfo=timezone.utc)
        assert _compute_week_of(now) == date(2026, 4, 13)

    def test_sunday_returns_previous_monday(self):
        # 2026-04-19 is a Sunday.
        now = datetime(2026, 4, 19, 23, 59, 59, tzinfo=timezone.utc)
        assert _compute_week_of(now) == date(2026, 4, 13)

    def test_saturday_returns_previous_monday(self):
        now = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)
        assert _compute_week_of(now) == date(2026, 4, 13)

    def test_early_morning_monday(self):
        # Edge case: Monday at 00:00:01 should still return Monday.
        now = datetime(2026, 4, 13, 0, 0, 1, tzinfo=timezone.utc)
        assert _compute_week_of(now) == date(2026, 4, 13)

    def test_non_utc_timezone_normalized(self):
        # A US-Pacific Monday at 22:00 local is already Tuesday 05:00 UTC —
        # still Monday's week (Monday UTC is Monday's week_of).
        from datetime import timedelta
        pacific = timezone(timedelta(hours=-7))
        # Monday 2026-04-13 22:00 Pacific = Tuesday 2026-04-14 05:00 UTC
        now = datetime(2026, 4, 13, 22, 0, 0, tzinfo=pacific)
        assert _compute_week_of(now) == date(2026, 4, 13)

    def test_week_boundary_rollover(self):
        # Monday 2026-04-20 is a new week.
        now = datetime(2026, 4, 20, 7, 0, 0, tzinfo=timezone.utc)
        assert _compute_week_of(now) == date(2026, 4, 20)


class TestBuildStaleErrorMessage:
    def test_empty_list(self):
        assert _build_stale_error_message([]) == "no stale sources"

    def test_single_source(self):
        stale = [{"source": "ad_scrape", "competitor_id": "abc"}]
        assert _build_stale_error_message(stale) == "stale: ad_scrape=1"

    def test_groups_by_source(self):
        stale = [
            {"source": "ad_scrape", "competitor_id": "a"},
            {"source": "ad_scrape", "competitor_id": "b"},
            {"source": "domain_intel", "competitor_id": "c"},
        ]
        msg = _build_stale_error_message(stale)
        # Sorted by source name.
        assert msg == "stale: ad_scrape=2, domain_intel=1"

    def test_unknown_source_bucketed(self):
        stale = [{"competitor_id": "a"}, {"source": "funnel_scan", "competitor_id": "b"}]
        msg = _build_stale_error_message(stale)
        assert "unknown=1" in msg
        assert "funnel_scan=1" in msg

    def test_output_sorted_alphabetically(self):
        stale = [
            {"source": "funnel_scan", "competitor_id": "a"},
            {"source": "ad_scrape", "competitor_id": "b"},
            {"source": "domain_intel", "competitor_id": "c"},
        ]
        msg = _build_stale_error_message(stale)
        # Alphabetical: ad_scrape, domain_intel, funnel_scan
        assert msg.index("ad_scrape") < msg.index("domain_intel") < msg.index("funnel_scan")


class TestMapShipStatusToRunStatus:
    def test_completed(self):
        assert _map_ship_status_to_run_status("completed") == "completed"

    def test_empty(self):
        assert _map_ship_status_to_run_status("empty") == "empty"

    def test_failed(self):
        assert _map_ship_status_to_run_status("failed") == "failed"

    def test_unknown_becomes_failed(self):
        assert _map_ship_status_to_run_status("garbage") == "failed"

    def test_none_becomes_failed(self):
        assert _map_ship_status_to_run_status(None) == "failed"

    def test_empty_string_becomes_failed(self):
        assert _map_ship_status_to_run_status("") == "failed"
