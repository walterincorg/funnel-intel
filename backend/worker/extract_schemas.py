"""
Pydantic schemas passed to `page.extract(schema=...)` during Stagehand runs.

These mirror the JSON shapes the previous browser-use agent was instructed to
emit (see `strategies.build_traversal_prompt`). Keeping the shapes identical
means `differ.py` and the `scan_steps` / `pricing_snapshots` DB schemas need
no changes.
"""

from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


# ----- question / info / input steps -----

class AnswerOption(BaseModel):
    label: str = Field(..., description="Human-visible label for the option.")
    value: Optional[str] = Field(
        None,
        description="Underlying value if different from the label (e.g. form value).",
    )


class QuestionStep(BaseModel):
    """A single funnel step — question, informational screen, or input field."""

    step_type: Literal["question", "info", "input"] = Field(
        ..., description="question: multiple choice; info: just info; input: free-text field."
    )
    question_text: Optional[str] = Field(
        None,
        description="The main heading or question shown to the user, verbatim.",
    )
    answer_options: list[AnswerOption] = Field(
        default_factory=list,
        description="Selectable options for question steps. Empty for info/input.",
    )


# ----- pricing step -----

class Plan(BaseModel):
    name: str = Field(..., description="Plan name (e.g. 'Basic', 'Pro', 'Annual').")
    price: Optional[str] = Field(None, description="Price as shown, including symbol.")
    currency: Optional[str] = Field(None, description="Currency code or symbol.")
    period: Optional[str] = Field(
        None,
        description="Billing period (e.g. 'month', 'year', '3 months').",
    )
    features: list[str] = Field(default_factory=list)


class Discount(BaseModel):
    type: Optional[str] = Field(None, description="discount type, e.g. 'coupon', 'limited-time'.")
    amount: Optional[str] = None
    original_price: Optional[str] = None
    discounted_price: Optional[str] = None
    conditions: Optional[str] = None


class TrialInfo(BaseModel):
    has_trial: bool = False
    trial_days: Optional[int] = None
    trial_price: Optional[str] = None


class PricingStep(BaseModel):
    """A pricing / paywall screen capture."""

    plans: list[Plan] = Field(default_factory=list)
    discounts: list[Discount] = Field(default_factory=list)
    trial_info: Optional[TrialInfo] = None


# ----- schema lookup -----

_SCHEMA_BY_KIND: dict[str, type[BaseModel]] = {
    "question": QuestionStep,
    "info": QuestionStep,
    "input": QuestionStep,
    "pricing": PricingStep,
    "discount": PricingStep,
}


def schema_for(extract_kind: str) -> type[BaseModel]:
    """Map a recipe step's `extract_kind` to its Pydantic schema."""
    return _SCHEMA_BY_KIND.get(extract_kind, QuestionStep)
