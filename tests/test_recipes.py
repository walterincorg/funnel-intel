"""Tests for the traversal recipe Pydantic models and Supabase IO layer."""

from __future__ import annotations

import copy
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from backend.worker.recipes import (
    Recipe,
    RecipeStep,
    get_recipe,
    invalidate_recipe,
    save_recipe,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _recipe_row(competitor_id: str, version: int = 1, is_active: bool = True) -> dict:
    return {
        "id": str(uuid4()),
        "competitor_id": competitor_id,
        "version": version,
        "start_url": "https://example.com/start",
        "steps": [
            {
                "step_number": 1,
                "intent": "click continue",
                "observe_result": {
                    "selector": "#continue",
                    "method": "click",
                    "arguments": [],
                    "description": "click continue",
                },
                "extract_kind": "question",
                "expected_url_pattern": None,
            },
        ],
        "stop_reason": "paywall",
        "recorded_at": "2026-04-18T00:00:00Z",
        "recorded_run_id": None,
        "is_active": is_active,
    }


class _FakeTable:
    """Minimal stand-in for a Supabase table query builder.

    Captures the last `update` payload and returns whatever `select` rows the
    test sets on `.rows`.
    """

    def __init__(self):
        self.rows: list[dict] = []
        self.inserted: list[dict] = []
        self.last_update: dict | None = None
        self.update_filters: list[tuple[str, object]] = []

    def select(self, *_a, **_kw):  # noqa: D401
        return self

    def insert(self, payload):
        self.inserted.append(payload)
        new = copy.deepcopy(payload)
        new["id"] = str(uuid4())
        new.setdefault("recorded_at", "2026-04-18T00:00:00Z")
        self._insert_result = new
        return _InsertChain(new)

    def update(self, payload):
        self.last_update = payload
        return _UpdateChain(self)

    def eq(self, col, val):
        return _QueryChain(self, [(col, val)])

    def limit(self, _n):
        return self

    def execute(self):
        return _Result(self.rows)


class _QueryChain:
    def __init__(self, table: _FakeTable, filters):
        self.table = table
        self.filters = filters

    def eq(self, col, val):
        return _QueryChain(self.table, self.filters + [(col, val)])

    def limit(self, _n):
        return self

    def execute(self):
        matching = [
            r for r in self.table.rows
            if all(r.get(c) == v for c, v in self.filters)
        ]
        return _Result(matching)


class _UpdateChain:
    def __init__(self, table: _FakeTable):
        self.table = table

    def eq(self, col, val):
        self.table.update_filters.append((col, val))
        return self

    def execute(self):
        return _Result([])


class _InsertChain:
    def __init__(self, row):
        self._row = row

    def execute(self):
        return _Result([self._row])


class _Result:
    def __init__(self, data):
        self.data = data


@pytest.fixture
def fake_db(monkeypatch):
    """Patch `backend.worker.recipes.get_db` with a fake multi-table client."""
    tables: dict[str, _FakeTable] = {}

    def _table(name):
        tables.setdefault(name, _FakeTable())
        return tables[name]

    client = MagicMock()
    client.table.side_effect = _table

    monkeypatch.setattr("backend.worker.recipes.get_db", lambda: client)
    return tables


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

def test_bump_version_increments_and_resets_id():
    recipe = Recipe(
        id=uuid4(),
        competitor_id=uuid4(),
        version=3,
        start_url="https://example.com",
        steps=[],
    )
    bumped = recipe.bump_version()
    assert bumped.version == 4
    assert bumped.id is None
    # Original is untouched.
    assert recipe.version == 3


def test_recipe_step_roundtrip():
    step = RecipeStep(
        step_number=7,
        intent="pick the middle option",
        observe_result={"selector": "#b", "method": "click", "arguments": []},
        extract_kind="question",
    )
    dumped = step.model_dump()
    restored = RecipeStep.model_validate(dumped)
    assert restored == step


# ---------------------------------------------------------------------------
# DB IO tests
# ---------------------------------------------------------------------------

def test_get_recipe_returns_none_when_no_active_row(fake_db):
    result = get_recipe(uuid4())
    assert result is None


def test_get_recipe_parses_an_active_row(fake_db):
    competitor_id = str(uuid4())
    fake_db.setdefault("traversal_recipes", _FakeTable())
    fake_db["traversal_recipes"].rows = [_recipe_row(competitor_id)]

    recipe = get_recipe(competitor_id)
    assert recipe is not None
    assert recipe.version == 1
    assert len(recipe.steps) == 1
    assert recipe.steps[0].observe_result["selector"] == "#continue"


def test_save_recipe_retires_existing_active_row_and_bumps_version(fake_db):
    competitor_id = str(uuid4())
    table = fake_db.setdefault("traversal_recipes", _FakeTable())
    # An existing active v2 recipe.
    table.rows = [_recipe_row(competitor_id, version=2, is_active=True)]

    new = Recipe(
        competitor_id=competitor_id,
        version=1,  # intentionally stale — save_recipe should bump past the existing.
        start_url="https://example.com/start",
        steps=[
            RecipeStep(
                step_number=1,
                intent="click continue",
                observe_result={"selector": "#continue", "method": "click", "arguments": []},
                extract_kind="question",
            ),
        ],
    )
    saved = save_recipe(competitor_id, new)

    assert saved.version == 3  # one past the retired v2
    # Existing active row was asked to be flipped to is_active=false.
    assert table.last_update is not None
    assert table.last_update["is_active"] is False
    assert table.last_update["invalidated_reason"] == "superseded"
    # New row was inserted with is_active=true.
    assert len(table.inserted) == 1
    assert table.inserted[0]["is_active"] is True
    assert table.inserted[0]["version"] == 3


def test_invalidate_recipe_flips_row(fake_db):
    recipe_id = uuid4()
    table = fake_db.setdefault("traversal_recipes", _FakeTable())

    invalidate_recipe(recipe_id, reason="selector broke at step 5")

    assert table.last_update is not None
    assert table.last_update["is_active"] is False
    assert table.last_update["invalidated_reason"] == "selector broke at step 5"
    assert ("id", str(recipe_id)) in table.update_filters
