"""Scripted replay of a recorded funnel traversal.

Loads the per-competitor funnel_recordings.action_log and walks it with
raw Playwright. Selector timeouts hand control to a 1-step browser-use
Agent (via backend.worker.patch), the patched action is merged back into
the action_log, and scripted playback resumes. Pricing pages always route
through a single Haiku extraction call so drift detection keeps working.

Shared-browser handoff (Decision 2 = A): the browser-use Browser wraps the
same Chromium process Playwright drives. The CDP endpoint is exposed by
Playwright's chromium.launch(args=[..., "--remote-debugging-port=0"]) and
passed into BrowserProfile(cdp_url=...) for the patch Agent.
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import time
from typing import Any

from backend.config import ANTHROPIC_API_KEY
from backend.worker.trace_parser import estimate_replay_cost

log = logging.getLogger(__name__)

SELECTOR_TIMEOUT_MS = 10_000
NAVIGATION_TIMEOUT_MS = 30_000
MAX_PATCHES_PER_RUN = 3
REPLAY_MODE_TAG = "replay"


class ReplayEscalation(Exception):
    """Raised when the replay engine needs to abandon scripted playback and
    fall back to the full LLM traversal. loop.py catches this and re-runs
    via traversal.run_traversal_sync()."""

    def __init__(self, reason: str, patches_attempted: int):
        super().__init__(reason)
        self.reason = reason
        self.patches_attempted = patches_attempted


# ---------------------------------------------------------------------------
# Pricing extraction — one Haiku call per replay (Q5 = A).
# ---------------------------------------------------------------------------

_PRICING_SYSTEM = """You extract structured pricing data from a checkout/plan page's text.

Return ONLY valid JSON with this exact shape (null fields allowed):
{
  "plans": [{"name": "...", "price": "...", "currency": "...", "period": "...", "features": ["..."]}],
  "discounts": [{"type": "...", "amount": "...", "original_price": "...", "discounted_price": "...", "conditions": "..."}],
  "trial_info": {"has_trial": true|false, "trial_days": N|null, "trial_price": "..."|null}
}"""


async def extract_pricing_via_haiku(page_text: str) -> dict | None:
    """Call Claude Haiku once on the pricing page text. Returns None on failure."""
    if not page_text or not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        log.warning("anthropic SDK not installed — skipping pricing extraction")
        return None

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    try:
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            system=_PRICING_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Pricing page text:\n\n{page_text[:8000]}",
            }],
        )
    except Exception as exc:
        log.warning("Haiku pricing extraction failed: %s", exc)
        return None

    text = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    )
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        log.warning("Haiku returned non-JSON pricing payload")
        return None


# ---------------------------------------------------------------------------
# Playwright helpers — build locators from action-log hints.
# ---------------------------------------------------------------------------

def _build_click_locator(page, action: dict):
    """Return an ordered list of locator candidates to try for a click."""
    locators = []
    target = (action.get("target_text") or "").strip()
    selector = action.get("selector")
    question = (action.get("question_text") or "").strip()

    if selector:
        locators.append(page.locator(selector).first)
    if target:
        locators.append(page.get_by_role("button", name=target, exact=False).first)
        locators.append(page.get_by_role("radio", name=target, exact=False).first)
        locators.append(page.get_by_role("checkbox", name=target, exact=False).first)
        locators.append(page.get_by_role("link", name=target, exact=False).first)
        locators.append(page.get_by_text(target, exact=False).first)
    if not locators and question:
        # Last resort: look for generic forward buttons on the page.
        for label in ("Continue", "Next", "Submit"):
            locators.append(page.get_by_role("button", name=label, exact=False).first)
    return locators


def _build_fill_locator(page, action: dict):
    locators = []
    selector = action.get("selector")
    question = (action.get("question_text") or "").strip()
    if selector:
        locators.append(page.locator(selector).first)
    if question:
        locators.append(page.get_by_label(question, exact=False).first)
    # Heuristic: use the first visible text input on the page.
    locators.append(page.locator("input:visible:not([type='hidden'])").first)
    locators.append(page.locator("textarea:visible").first)
    return locators


async def _try_locators(locators, action_fn, timeout_ms: int) -> bool:
    """Try each locator in order. Returns True on first success."""
    per_locator = max(1000, timeout_ms // max(1, len(locators)))
    for loc in locators:
        try:
            await action_fn(loc, per_locator)
            return True
        except Exception:
            continue
    return False


async def _execute_click(page, action: dict) -> bool:
    locators = _build_click_locator(page, action)

    async def _do(loc, ms):
        await loc.wait_for(state="visible", timeout=ms)
        await loc.click(timeout=ms)

    return await _try_locators(locators, _do, SELECTOR_TIMEOUT_MS)


async def _execute_fill(page, action: dict) -> bool:
    value = action.get("input_value")
    if value is None:
        value = "jane.doe@example.com" if "email" in (action.get("question_text") or "").lower() else "test"
    locators = _build_fill_locator(page, action)

    async def _do(loc, ms):
        await loc.wait_for(state="visible", timeout=ms)
        await loc.fill(str(value), timeout=ms)
        # Many quiz-style funnels advance on Enter.
        try:
            await loc.press("Enter", timeout=1500)
        except Exception:
            pass

    return await _try_locators(locators, _do, SELECTOR_TIMEOUT_MS)


# ---------------------------------------------------------------------------
# Main entry points.
# ---------------------------------------------------------------------------

async def run_replay(
    competitor_id: str,
    competitor_name: str,
    funnel_url: str,
    action_log: list[dict],
    on_progress: Any | None = None,
) -> dict:
    """Drive Chromium through the recorded action_log.

    Returns a result dict shaped like traversal.run_traversal()'s output,
    with extra fields:
      - mode: "scripted" | "patched"
      - patch_count: number of LLM patches applied this run
      - cost: breakdown from estimate_replay_cost()
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is required for scripted replay. Install via 'pip install playwright' "
            "and run 'playwright install chromium'."
        ) from exc

    headless = os.getenv("BROWSER_HEADLESS", "true").lower() != "false"
    user_data_dir = tempfile.mkdtemp(prefix="funnel-replay-")
    replay_start = time.perf_counter()

    steps: list[dict] = []
    pricing: dict | None = None
    patch_count = 0
    mutable_log = [dict(a) for a in action_log]
    stop_reason = "end_of_funnel"

    # Import browser-use lazily — only needed when we actually hit a patch.
    # Keeps the import graph shallow for tests that stub Playwright.
    browser_use_browser = None

    async with async_playwright() as pw:
        # Use launch() (not launch_persistent_context) so browser.ws_endpoint
        # is populated and we can hand the same Chromium process to browser-use
        # for CDP sharing. launch_persistent_context returns a BrowserContext
        # whose .browser is always None, breaking the CDP bridge.
        browser = await pw.chromium.launch(
            headless=headless,
            args=["--disable-dev-shm-usage", "--disable-gpu", "--no-sandbox"],
        )
        context = await browser.new_context(accept_downloads=False)
        page = await context.new_page()
        page.set_default_timeout(SELECTOR_TIMEOUT_MS)
        page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)

        try:
            await page.goto(funnel_url, wait_until="domcontentloaded")
        except Exception as exc:
            log.warning("Initial navigation to %s failed: %s", funnel_url, exc)

        try:
            for i, action in enumerate(mutable_log):
                step_no = action.get("step_number", i + 1)
                if on_progress:
                    try:
                        on_progress({
                            "step": step_no,
                            "type": action.get("step_type", "unknown"),
                            "message": f"[{REPLAY_MODE_TAG}] {action.get('question_text') or action.get('action_description') or ''}",
                        })
                    except Exception:
                        pass

                if action.get("step_type") == "pricing":
                    # Haiku extraction regardless of scripted/patched path.
                    try:
                        if action.get("url_before"):
                            await page.goto(action["url_before"], wait_until="domcontentloaded")
                    except Exception:
                        pass
                    page_text = await _collect_visible_text(page)
                    extracted = await extract_pricing_via_haiku(page_text) or {}
                    pricing = {
                        "step_number": step_no,
                        "step_type": "pricing",
                        "url": page.url,
                        "log": "Pricing page — extracted via Haiku",
                        **extracted,
                    }
                    steps.append(pricing)
                    continue

                success = False
                action_type = action.get("action_type", "click")
                try:
                    if action_type == "fill":
                        success = await _execute_fill(page, action)
                    elif action_type == "navigate" and action.get("url_before"):
                        await page.goto(action["url_before"], wait_until="domcontentloaded")
                        success = True
                    else:
                        success = await _execute_click(page, action)
                except Exception as exc:
                    log.debug("Scripted step %s raised: %s", step_no, exc)
                    success = False

                if success:
                    await _settle(page)
                    steps.append(_build_step_record(action, page.url, mode="scripted"))
                    continue

                # Scripted path couldn't resolve — attempt an LLM patch.
                if patch_count >= MAX_PATCHES_PER_RUN:
                    raise ReplayEscalation(
                        f"Exceeded {MAX_PATCHES_PER_RUN} patches at step {step_no}",
                        patch_count,
                    )

                if browser_use_browser is None:
                    browser_use_browser = _build_browser_use_wrapper(browser)

                from backend.worker.patch import patch_step
                patched = await patch_step(browser_use_browser, action, current_url=page.url)
                if not patched:
                    raise ReplayEscalation(
                        f"Patch failed at step {step_no}",
                        patch_count,
                    )

                patch_count += 1
                mutable_log[i] = patched
                await _settle(page)
                steps.append(_build_step_record(patched, page.url, mode="patched"))

                if on_progress:
                    try:
                        on_progress({
                            "step": step_no,
                            "type": "patch",
                            "message": f"LLM patch applied at step {step_no}",
                        })
                    except Exception:
                        pass
        finally:
            try:
                await asyncio.wait_for(browser.close(), timeout=15)
            except Exception:
                pass
            try:
                shutil.rmtree(user_data_dir, ignore_errors=True)
            except Exception:
                pass

    duration_ms = (time.perf_counter() - replay_start) * 1000
    cost = estimate_replay_cost(patch_count, has_pricing=pricing is not None)
    mode = "patched" if patch_count > 0 else "scripted"
    summary = {
        "total_steps": len(steps),
        "stop_reason": stop_reason,
        "mode": mode,
        "patch_count": patch_count,
        "cost": cost,
    }

    log.info(
        "Replay complete for %s: %d steps, %d patches, mode=%s, cost=$%.2f (%.1fs)",
        competitor_name, len(steps), patch_count, mode, cost["total_usd"], duration_ms / 1000,
        extra={"step_count": len(steps), "patch_count": patch_count, "duration_ms": round(duration_ms)},
    )

    return {
        "steps": steps,
        "pricing": pricing,
        "summary": summary,
        "raw_output": "",
        "mode": mode,
        "patch_count": patch_count,
        "action_log": mutable_log,
        "cost": cost,
    }


def run_replay_sync(
    competitor_id: str,
    competitor_name: str,
    funnel_url: str,
    action_log: list[dict],
    on_progress: Any | None = None,
    timeout: int = 30 * 60,
) -> dict:
    coro = run_replay(competitor_id, competitor_name, funnel_url, action_log, on_progress)
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _collect_visible_text(page) -> str:
    try:
        return await page.evaluate("() => document.body?.innerText || ''")
    except Exception:
        return ""


async def _settle(page, timeout_ms: int = 3000) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass


def _build_step_record(action: dict, current_url: str, mode: str) -> dict:
    description = action.get("action_description") or ""
    if mode == "patched" and not description.lower().startswith("[patch"):
        description = f"[patched] {description}".strip()
    return {
        "step_number": action.get("step_number"),
        "step_type": action.get("step_type") or "question",
        "question_text": action.get("question_text"),
        "answer_options": None,
        "action_taken": description,
        "url": current_url,
        "log": description,
        "replay_mode": mode,
    }


def _build_browser_use_wrapper(playwright_browser):
    """Attach browser-use to the same Chromium instance via its CDP URL.

    browser-use's BrowserProfile accepts `cdp_url=` for exactly this case; if
    the installed version doesn't expose that field, we fall back to a fresh
    Browser which will still work (at the cost of losing in-flight cookies)."""
    from browser_use import Browser, BrowserProfile

    cdp_url = None
    try:
        # Playwright exposes the CDP endpoint on the Browser (Chromium only).
        ws = getattr(playwright_browser, "ws_endpoint", None)
        if callable(ws):
            cdp_url = ws()
        elif isinstance(ws, str):
            cdp_url = ws
    except Exception:
        cdp_url = None

    try:
        if cdp_url:
            return Browser(browser_profile=BrowserProfile(cdp_url=cdp_url, is_local=False))
    except TypeError:
        pass

    # Fallback: start a second Browser. State bleeds, but the patch is localised
    # enough that the agent still completes the step.
    return Browser(
        browser_profile=BrowserProfile(
            headless=os.getenv("BROWSER_HEADLESS", "true").lower() != "false",
            chromium_sandbox=False,
            args=["--disable-dev-shm-usage", "--disable-gpu"],
            is_local=True,
        ),
    )
