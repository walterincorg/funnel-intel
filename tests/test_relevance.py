"""Tests for discovered domain relevance scoring."""

from backend.worker.domain_reverse_lookup import _score_relevance


class TestScoreRelevance:
    def test_known_competitor_is_high(self):
        assert _score_relevance("betterme.com", is_known_competitor=True) == "high"

    def test_active_unknown_registrant_is_medium(self):
        assert _score_relevance("gutfix-daily.com", is_known_competitor=False) == "medium"

    def test_staging_domain_is_low(self):
        assert _score_relevance("staging.betterme.dev", is_known_competitor=False) == "low"

    def test_dev_domain_is_low(self):
        assert _score_relevance("dev.example.com", is_known_competitor=False) == "low"

    def test_test_domain_is_low(self):
        assert _score_relevance("test.mysite.com", is_known_competitor=False) == "low"

    def test_localhost_is_low(self):
        assert _score_relevance("localhost.run", is_known_competitor=False) == "low"

    def test_preview_domain_is_low(self):
        assert _score_relevance("preview.app.dev", is_known_competitor=False) == "low"

    def test_parked_domain_is_low(self):
        assert _score_relevance("parked-domain.com", is_known_competitor=False) == "low"

    def test_normal_domain_is_medium(self):
        assert _score_relevance("competitor-brand.com", is_known_competitor=False) == "medium"

    def test_known_competitor_overrides_staging_pattern(self):
        # Even if domain looks like staging, if it's a known competitor, it's high
        assert _score_relevance("staging.competitor.com", is_known_competitor=True) == "high"

    def test_case_insensitive(self):
        assert _score_relevance("STAGING.example.com", is_known_competitor=False) == "low"

    def test_forsale_is_low(self):
        assert _score_relevance("forsale-domain.com", is_known_competitor=False) == "low"
