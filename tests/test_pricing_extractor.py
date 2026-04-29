"""Unit tests for the vision pricing extractor's pure helpers.

We don't exercise the real LLM API here — that's covered by the manual
end-to-end run in ``/tmp/test_extractor.py``. These tests just lock in the
schema and the legacy projection so future changes can't silently break the
pricing-history page.
"""

from backend.worker.pricing_extractor import (
    PRICING_EXTRACTOR_VERSION,
    _wrap,
    vision_to_legacy,
)


def test_wrap_computes_monthly_equivalents():
    raw = {
        "page_kind": "subscription_tiers",
        "currency": "USD",
        "plans": [
            {"plan_id": "1-week", "display_name": "1-Week Trial",
             "billing_cycle_weeks": 1,
             "intro": {"total_price": 6.93}, "renewal": {"total_price": 17.77}},
            {"plan_id": "4-week", "display_name": "4-Week Plan",
             "billing_cycle_weeks": 4,
             "intro": {"total_price": 15.19}, "renewal": {"total_price": 38.95}},
            {"plan_id": "12-week", "display_name": "12-Week Plan",
             "billing_cycle_weeks": 12,
             "intro": {"total_price": 36.99}},
        ],
        "trial": {"exists": True, "days": 7, "price": 6.93},
        "discounts": [],
    }
    wrapped = _wrap(raw, "claude-sonnet-4-6")
    assert wrapped["extractor_version"] == PRICING_EXTRACTOR_VERSION
    assert wrapped["extractor_model"] == "claude-sonnet-4-6"
    week_plan = wrapped["plans"][1]
    # 4-week cycle is treated as "monthly" billing → equivalent equals tile.
    assert abs(week_plan["monthly_equivalent"] - 15.19) < 0.05
    assert abs(week_plan["renewal_monthly_equivalent"] - 38.95) < 0.05
    # 12-week tile: $36.99 across 12 weeks = $36.99 × (4/12) = $12.33/mo
    twelve = wrapped["plans"][2]
    assert abs(twelve["monthly_equivalent"] - 12.33) < 0.05
    # 12-week renewal absent → no renewal_monthly_equivalent
    assert "renewal_monthly_equivalent" not in twelve


def test_vision_to_legacy_splits_intro_and_renewal():
    vision = {
        "currency": "USD",
        "plans": [
            {"plan_id": "4-week", "display_name": "4-WEEK PLAN",
             "billing_cycle_weeks": 4, "is_most_popular": True,
             "intro": {"total_price": 15.19, "label": "First 4 weeks"},
             "renewal": {"total_price": 38.95, "billed_every": "4 weeks"},
             "monthly_equivalent": 16.5, "renewal_monthly_equivalent": 42.3,
             "badges": ["MOST POPULAR"], "features": ["Workout plan"]},
        ],
        "trial": {"exists": True, "days": 7, "price": 6.93,
                  "renews_at": 39.99, "renews_every": "4 weeks"},
        "discounts": [
            {"type": "promo_code", "amount": "61%",
             "applies_to_plan_id": "4-week",
             "original_price": 38.95, "discounted_price": 15.19,
             "conditions": "Auto-applied"},
        ],
    }
    legacy = vision_to_legacy(vision)
    # Two rows: one intro, one renewal
    assert len(legacy["plans"]) == 2
    intro, renewal = legacy["plans"]
    assert intro["plan_id"] == renewal["plan_id"] == "4-week"
    assert intro["price_kind"] == "intro"
    assert renewal["price_kind"] == "renewal"
    assert intro["price"] == "15.19"
    assert renewal["price"] == "38.95"
    assert intro["monthly_equivalent"] == 16.5
    # Discount preserves original / discounted as strings, plan_id pinned
    assert legacy["discounts"][0]["original_price"] == "38.95"
    assert legacy["discounts"][0]["applies_to_plan_id"] == "4-week"
    assert legacy["trial_info"]["has_trial"] is True
    assert legacy["trial_info"]["trial_days"] == 7
    assert legacy["trial_info"]["renews_at"] == 39.99


def test_vision_to_legacy_skips_redundant_renewal():
    """When renewal == intro the renewal row is not emitted."""
    vision = {
        "currency": "USD",
        "plans": [{
            "plan_id": "lifetime", "display_name": "Lifetime",
            "billing_cycle_weeks": None,
            "intro": {"total_price": 99.0},
            "renewal": {"total_price": 99.0},
        }],
        "trial": {"exists": False},
        "discounts": [],
    }
    legacy = vision_to_legacy(vision)
    assert len(legacy["plans"]) == 1
    assert legacy["plans"][0]["price_kind"] == "intro"


def test_vision_to_legacy_handles_empty():
    legacy = vision_to_legacy({"plans": [], "discounts": [], "trial": {"exists": False}})
    assert legacy["plans"] == []
    assert legacy["discounts"] == []
    assert legacy["trial_info"]["has_trial"] is False


def test_wrap_normalises_ecommerce_supply():
    """Bioma-style supply tiles get rewritten to a stable schema:
    - bottle slugs become month slugs
    - billing_cycle_weeks is forced to 4 (monthly billing)
    - plan_id gets the -subscribe suffix when missing
    """
    raw = {
        "page_kind": "ecommerce_supply",
        "currency": "USD",
        "plans": [
            {"plan_id": "6-bottle", "display_name": "6-month supply",
             "billing_cycle_weeks": 26,
             "intro": {"total_price": 25.71, "per_day_price": 0.86}},
            {"plan_id": "3-month", "display_name": "3-month supply",
             "billing_cycle_weeks": 13,
             "intro": {"total_price": 35.31}},
            {"plan_id": "1-MONTH-Subscribe", "display_name": "1-month supply",
             "billing_cycle_weeks": None,
             "intro": {"total_price": 47.99}},
        ],
        "trial": {"exists": False},
        "discounts": [],
    }
    wrapped = _wrap(raw, "claude-sonnet-4-6")
    plans = wrapped["plans"]
    # bottle → month + subscribe suffix added
    assert plans[0]["plan_id"] == "6-month-subscribe"
    # already canonical month form, just suffix added
    assert plans[1]["plan_id"] == "3-month-subscribe"
    # capitalisation normalised, suffix already present
    assert plans[2]["plan_id"] == "1-month-subscribe"
    # cycle forced to 4 across the board (monthly billing)
    assert all(p["billing_cycle_weeks"] == 4 for p in plans)
    # monthly_equivalent now equals the per-bottle price
    assert abs(plans[0]["monthly_equivalent"] - 25.71) < 0.05
    assert abs(plans[2]["monthly_equivalent"] - 47.99) < 0.05
