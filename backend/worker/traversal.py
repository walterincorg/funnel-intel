"""Traversal dispatcher — routes to Stagehand record or replay.

Public contract matches the pre-Stagehand `run_traversal(...)` so the worker
loop and CLI don't have to care whether this scan recorded a new recipe or
replayed an existing one:

    result = run_traversal_sync(competitor_name, funnel_url, ...)
    # → {"steps": [...], "pricing": {...}|None, "summary": {...}, "raw_output": str}

Flow:

  1. Look up the active recipe for `competitor_id`.
  2. If one exists → `run_replay(recipe, ...)`. On success, maybe persist the
     self-healed recipe. On `RecipeBrokenError`, invalidate the recipe and
     fall through to record mode.
  3. If no recipe (or we just invalidated one) → `run_record(...)` and
     `save_recipe(...)` on success.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Callable, Optional

from backend.worker.recipes import (
    Recipe,
    get_recipe,
    invalidate_recipe,
    save_recipe,
)
from backend.worker.stagehand_driver import (
    MAX_SELF_HEAL_PER_RUN,
    SCAN_TIMEOUT,
    RecipeBrokenError,
    TraversalResult,
    run_record,
    run_replay,
)

log = logging.getLogger(__name__)

_PALM_IMAGE_PATH = Path(__file__).resolve().parents[1] / "assets" / "nebula_palm.png"


ProgressCb = Optional[Callable[[dict], None]]


def _available_files(competitor_name: str, competitor_slug: Optional[str]) -> list[str]:
    is_nebula = (
        (competitor_slug or "").lower() == "nebula"
        or "nebula" in (competitor_name or "").lower()
    )
    if is_nebula and _PALM_IMAGE_PATH.exists():
        return [str(_PALM_IMAGE_PATH)]
    return []


async def _do_record(
    competitor_name: str,
    funnel_url: str,
    config: dict | None,
    available_files: list[str],
    on_progress: ProgressCb,
    competitor_id: Optional[str],
    run_id: Optional[str],
    prior_version: int,
) -> TraversalResult:
    """Run record mode and persist the resulting recipe."""
    result, recipe = await run_record(
        funnel_url=funnel_url,
        competitor_name=competitor_name,
        config=config,
        available_files=available_files,
        on_progress=on_progress,
        competitor_id=competitor_id,
    )

    # Only persist a recipe if we captured a meaningful number of steps. Same
    # rule as the existing baseline-promotion logic in loop.process_job.
    captured = len(recipe.steps)
    if competitor_id and captured >= 3:
        try:
            # Version continues from any prior recipe so history is monotonic.
            recipe.version = max(prior_version + 1, recipe.version)
            recipe.competitor_id = competitor_id  # type: ignore[assignment]
            save_recipe(competitor_id, recipe, recorded_run_id=run_id)
        except Exception:
            log.exception(
                "Failed to save recipe for competitor %s — scan still returns data.",
                competitor_id,
            )
    elif competitor_id:
        log.warning(
            "Record run for competitor %s captured only %d steps — skipping recipe save.",
            competitor_id, captured,
        )

    return result


async def _run_traversal_async(
    competitor_name: str,
    funnel_url: str,
    config: dict | None = None,
    on_progress: ProgressCb = None,
    competitor_slug: Optional[str] = None,
    competitor_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict:
    """Async implementation. Public wrapper is `run_traversal_sync`."""
    traversal_start = time.perf_counter()
    available_files = _available_files(competitor_name, competitor_slug)

    recipe = get_recipe(competitor_id) if competitor_id else None

    # Happy path: replay the existing recipe.
    if recipe is not None:
        log.info(
            "Traversal for %s — replaying recipe v%d (%d steps)",
            competitor_name, recipe.version, len(recipe.steps),
        )
        try:
            result, updated_recipe, recipe_dirty = await run_replay(
                recipe=recipe, on_progress=on_progress,
            )
            if recipe_dirty and competitor_id:
                try:
                    bumped = updated_recipe.bump_version()
                    save_recipe(competitor_id, bumped, recorded_run_id=run_id)
                    log.info("Persisted self-healed recipe v%d", bumped.version)
                except Exception:
                    log.exception("Failed to persist self-healed recipe")
            duration_ms = (time.perf_counter() - traversal_start) * 1000
            log.info(
                "Traversal replay complete for %s: %d steps (%.1fs)",
                competitor_name, len(result.steps), duration_ms / 1000,
            )
            return result.as_dict()
        except RecipeBrokenError as err:
            log.warning(
                "Replay broke for %s (%s) — invalidating recipe v%d and re-recording.",
                competitor_name, err, recipe.version,
            )
            if recipe.id is not None:
                try:
                    invalidate_recipe(recipe.id, reason=str(err))
                except Exception:
                    log.exception("Failed to invalidate broken recipe")
            # Fall through to record.
            result = await _do_record(
                competitor_name=competitor_name,
                funnel_url=funnel_url,
                config=config,
                available_files=available_files,
                on_progress=on_progress,
                competitor_id=competitor_id,
                run_id=run_id,
                prior_version=recipe.version,
            )
            result.mode = "record_fallback"
            duration_ms = (time.perf_counter() - traversal_start) * 1000
            log.info(
                "Traversal record-fallback complete for %s: %d steps (%.1fs)",
                competitor_name, len(result.steps), duration_ms / 1000,
            )
            return result.as_dict()

    # No recipe yet — first scan for this competitor.
    log.info("Traversal for %s — no recipe, recording from scratch.", competitor_name)
    result = await _do_record(
        competitor_name=competitor_name,
        funnel_url=funnel_url,
        config=config,
        available_files=available_files,
        on_progress=on_progress,
        competitor_id=competitor_id,
        run_id=run_id,
        prior_version=0,
    )
    duration_ms = (time.perf_counter() - traversal_start) * 1000
    log.info(
        "Traversal record complete for %s: %d steps (%.1fs)",
        competitor_name, len(result.steps), duration_ms / 1000,
    )
    return result.as_dict()


def run_traversal_sync(
    competitor_name: str,
    funnel_url: str,
    config: dict | None = None,
    on_progress: ProgressCb = None,
    competitor_slug: Optional[str] = None,
    competitor_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict:
    """Synchronous entry point — same shape the worker loop has always called."""
    coro = _run_traversal_async(
        competitor_name=competitor_name,
        funnel_url=funnel_url,
        config=config,
        on_progress=on_progress,
        competitor_slug=competitor_slug,
        competitor_id=competitor_id,
        run_id=run_id,
    )
    return asyncio.run(asyncio.wait_for(coro, timeout=SCAN_TIMEOUT))


# Re-export for external callers / tests.
__all__ = [
    "MAX_SELF_HEAL_PER_RUN",
    "SCAN_TIMEOUT",
    "RecipeBrokenError",
    "run_traversal_sync",
]
