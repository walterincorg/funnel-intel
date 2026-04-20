"""Tests for the Stagehand driver — observe-result normalisation, replay
happy-path (no LLM calls), self-heal, and recipe-broken escalation.

The Stagehand SDK itself is mocked end-to-end; we never touch a real browser.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.worker.recipes import Recipe, RecipeStep
from backend.worker.stagehand_driver import (
    MAX_SELF_HEAL_PER_RUN,
    RecipeBrokenError,
    _coerce_observe_result,
    _iter_agent_actions,
    run_replay,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _recipe(steps):
    return Recipe(
        id=uuid4(),
        competitor_id=uuid4(),
        version=1,
        start_url="https://example.com/start",
        steps=steps,
        stop_reason="paywall",
    )


def _step(n, selector="#btn", intent="click", kind="question"):
    return RecipeStep(
        step_number=n,
        intent=intent,
        observe_result={"selector": selector, "method": "click", "arguments": []},
        extract_kind=kind,
    )


class _FakePage:
    """Async-shaped stand-in for Stagehand's page object."""

    def __init__(self, act_errors_on: list[int] | None = None):
        self.url = "https://example.com/start"
        self.act_calls: list[object] = []
        self.observe_calls: list[str] = []
        self.extract_calls: list[type] = []
        self._act_errors_on = set(act_errors_on or [])
        self._act_count = 0

    async def goto(self, _url):
        return None

    async def act(self, payload):
        self._act_count += 1
        self.act_calls.append(payload)
        if self._act_count in self._act_errors_on:
            raise RuntimeError(f"simulated act failure #{self._act_count}")

    async def observe(self, instruction: str):
        self.observe_calls.append(instruction)
        return [{"selector": "#healed", "method": "click", "arguments": [], "description": instruction}]

    async def extract(self, schema):
        self.extract_calls.append(schema)
        # Return a dict shaped like QuestionStep or PricingStep.
        if schema.__name__ == "PricingStep":
            return {"plans": [], "discounts": [], "trial_info": None}
        return {
            "step_type": "question",
            "question_text": "What is your goal?",
            "answer_options": [{"label": "Lose weight", "value": "lose"}],
        }


class _FakeStagehand:
    def __init__(self, page):
        self.page = page

    async def init(self):
        return None

    async def close(self):
        return None


@pytest.fixture
def patch_stagehand(monkeypatch):
    """Replace `_open_stagehand` with a factory that yields a fresh fake each call."""
    holder = {}

    async def _open():
        return holder["stagehand"]

    async def _close(_):
        return None

    def _install(page):
        holder["stagehand"] = _FakeStagehand(page)

    monkeypatch.setattr("backend.worker.stagehand_driver._open_stagehand", _open)
    monkeypatch.setattr("backend.worker.stagehand_driver._close_stagehand", _close)
    return _install


# ---------------------------------------------------------------------------
# Unit: observe-result normalisation
# ---------------------------------------------------------------------------

def test_coerce_from_dict():
    raw = {"selector": "#x", "method": "click", "arguments": ["a"], "description": "d"}
    assert _coerce_observe_result(raw) == {
        "selector": "#x", "method": "click", "arguments": ["a"], "description": "d",
    }


def test_coerce_from_object_with_model_dump():
    obj = SimpleNamespace()
    obj.model_dump = lambda: {"selector": "#y", "method": "fill", "arguments": ["hi"]}
    coerced = _coerce_observe_result(obj)
    assert coerced["selector"] == "#y"
    assert coerced["method"] == "fill"


def test_coerce_returns_none_without_selector():
    assert _coerce_observe_result({"method": "click"}) is None
    assert _coerce_observe_result(None) is None


def test_iter_agent_actions_prefers_actions_over_history():
    result = SimpleNamespace(
        actions=[{"selector": "#a", "method": "click"}],
        history=[{"selector": "#b", "method": "click"}],
    )
    actions = _iter_agent_actions(result)
    assert len(actions) == 1
    assert actions[0]["selector"] == "#a"


def test_iter_agent_actions_drops_selectorless_entries():
    result = SimpleNamespace(actions=[{"method": "wait"}, {"selector": "#ok", "method": "click"}])
    actions = _iter_agent_actions(result)
    assert len(actions) == 1
    assert actions[0]["selector"] == "#ok"


# ---------------------------------------------------------------------------
# Integration: run_replay happy path — no observe() calls
# ---------------------------------------------------------------------------

def test_replay_happy_path_uses_cached_observe_results(patch_stagehand):
    page = _FakePage()
    patch_stagehand(page)

    recipe = _recipe([_step(1), _step(2), _step(3)])
    result, updated, dirty = asyncio.run(run_replay(recipe))

    # Every step acted — none self-healed.
    assert len(page.act_calls) == 3
    assert page.observe_calls == []
    # The cached observe results were passed straight through.
    assert page.act_calls[0]["selector"] == "#btn"
    # Replay isn't dirty; recipe not modified.
    assert dirty is False
    assert updated == recipe
    # Each step got an extract() and generated a step row.
    assert len(page.extract_calls) >= 3
    assert len(result.steps) == 3
    # Live-extracted question text flowed through.
    assert result.steps[0]["question_text"] == "What is your goal?"


# ---------------------------------------------------------------------------
# Integration: self-heal on first act() failure
# ---------------------------------------------------------------------------

def test_replay_self_heals_and_marks_recipe_dirty(patch_stagehand):
    page = _FakePage(act_errors_on=[2])  # step 2's first act() blows up
    patch_stagehand(page)

    recipe = _recipe([_step(1), _step(2), _step(3)])
    result, updated, dirty = asyncio.run(run_replay(recipe))

    assert dirty is True
    # The healed step's observe_result now points at "#healed".
    healed = next(s for s in updated.steps if s.step_number == 2)
    assert healed.observe_result["selector"] == "#healed"
    # observe() was called exactly once (the heal for step 2).
    assert len(page.observe_calls) == 1
    # Replay still completed all three steps.
    assert len(result.steps) == 3


# ---------------------------------------------------------------------------
# Integration: recipe broken when self-heal exhausted
# ---------------------------------------------------------------------------

def test_replay_raises_recipe_broken_when_self_heal_exhausted(patch_stagehand):
    # Make every single act() fail. observe() also returns a selector whose
    # act() we won't actually be called on, because the initial act fails
    # each time it's tried — self-heal burns all its budget immediately.
    class _AlwaysFailsPage(_FakePage):
        async def act(self, payload):
            self._act_count += 1
            self.act_calls.append(payload)
            raise RuntimeError("always")

    page = _AlwaysFailsPage()
    patch_stagehand(page)

    n = MAX_SELF_HEAL_PER_RUN + 2
    recipe = _recipe([_step(i) for i in range(1, n + 1)])

    with pytest.raises(RecipeBrokenError):
        asyncio.run(run_replay(recipe))
