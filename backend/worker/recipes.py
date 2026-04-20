"""
Traversal recipes — the deterministic replay scripts for Stagehand.

A recipe is the ordered list of Stagehand `observe()` results captured during a
successful AI-driven recording run, plus metadata about what to extract at each
step. Replay walks the recipe, calling `page.act(cached_observe_result)` with
zero autonomous-agent LLM calls. If a step breaks, the driver self-heals,
mutates the in-memory step, and then a new recipe row is saved (version bumped,
old row flipped to is_active=false).

Schema lives in `supabase/migrations/20260419120000_stagehand_recipes.sql`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from backend.db import get_db

log = logging.getLogger(__name__)


ExtractKind = Literal["question", "info", "input", "pricing", "discount"]


class RecipeStep(BaseModel):
    """One step of a recorded traversal.

    `observe_result` is the raw Stagehand ObserveResult shape
    ({selector, method, arguments, ...}) — passed directly to `page.act(...)`
    on replay so no LLM is involved.
    """

    step_number: int
    intent: str = Field(
        ...,
        description="Natural-language description of what this step does. Used for self-heal re-observe.",
    )
    observe_result: dict = Field(
        ...,
        description="Stagehand ObserveResult. Replay passes this straight to page.act().",
    )
    extract_kind: Optional[ExtractKind] = Field(
        None,
        description="Which Pydantic schema page.extract() should use on this step. None = skip extract.",
    )
    expected_url_pattern: Optional[str] = Field(
        None,
        description="Regex. Replay flags drift if the live URL doesn't match.",
    )


class Recipe(BaseModel):
    id: Optional[UUID] = None
    competitor_id: UUID
    version: int = 1
    start_url: str
    steps: list[RecipeStep] = Field(default_factory=list)
    stop_reason: Optional[str] = None
    recorded_at: Optional[datetime] = None
    recorded_run_id: Optional[UUID] = None
    is_active: bool = True

    def bump_version(self) -> "Recipe":
        """Return a copy with version incremented. Used after self-heal writes."""
        return self.model_copy(update={
            "id": None,
            "version": self.version + 1,
            "recorded_at": datetime.now(timezone.utc),
        })


# ---------- DB IO ----------

def _row_to_recipe(row: dict) -> Recipe:
    return Recipe(
        id=UUID(row["id"]),
        competitor_id=UUID(row["competitor_id"]),
        version=row["version"],
        start_url=row["start_url"],
        steps=[RecipeStep.model_validate(s) for s in (row.get("steps") or [])],
        stop_reason=row.get("stop_reason"),
        recorded_at=row.get("recorded_at"),
        recorded_run_id=UUID(row["recorded_run_id"]) if row.get("recorded_run_id") else None,
        is_active=row.get("is_active", True),
    )


def get_recipe(competitor_id: str | UUID) -> Optional[Recipe]:
    """Fetch the active recipe for a competitor, or None if never recorded."""
    db = get_db()
    res = (
        db.table("traversal_recipes")
        .select("*")
        .eq("competitor_id", str(competitor_id))
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    return _row_to_recipe(res.data[0])


def save_recipe(
    competitor_id: str | UUID,
    recipe: Recipe,
    recorded_run_id: str | UUID | None = None,
) -> Recipe:
    """
    Insert a new active recipe for this competitor. Any existing active recipe
    is flipped to is_active=false first (the partial unique index enforces one
    active row per competitor).
    """
    db = get_db()
    competitor_id_str = str(competitor_id)

    # Retire any existing active recipe for this competitor.
    existing = (
        db.table("traversal_recipes")
        .select("id,version")
        .eq("competitor_id", competitor_id_str)
        .eq("is_active", True)
        .execute()
    )
    prior_version = 0
    if existing.data:
        for row in existing.data:
            prior_version = max(prior_version, row.get("version") or 0)
        db.table("traversal_recipes").update({
            "is_active": False,
            "invalidated_at": datetime.now(timezone.utc).isoformat(),
            "invalidated_reason": "superseded",
        }).eq("competitor_id", competitor_id_str).eq("is_active", True).execute()

    version = recipe.version if recipe.version > prior_version else prior_version + 1

    payload = {
        "competitor_id": competitor_id_str,
        "version": version,
        "start_url": recipe.start_url,
        "steps": [s.model_dump() for s in recipe.steps],
        "stop_reason": recipe.stop_reason,
        "recorded_run_id": str(recorded_run_id) if recorded_run_id else None,
        "is_active": True,
    }
    res = db.table("traversal_recipes").insert(payload).execute()
    saved = _row_to_recipe(res.data[0])
    log.info(
        "Saved traversal recipe for competitor=%s version=%d steps=%d",
        competitor_id_str, saved.version, len(saved.steps),
    )
    return saved


def invalidate_recipe(recipe_id: str | UUID, reason: str) -> None:
    """Flip a recipe row to is_active=false with a reason."""
    db = get_db()
    db.table("traversal_recipes").update({
        "is_active": False,
        "invalidated_at": datetime.now(timezone.utc).isoformat(),
        "invalidated_reason": reason,
    }).eq("id", str(recipe_id)).execute()
    log.warning("Invalidated recipe %s: %s", recipe_id, reason)
