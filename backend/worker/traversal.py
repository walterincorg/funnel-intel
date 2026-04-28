"""Browser-use funnel traversal engine."""

from __future__ import annotations
import asyncio
import base64
import json
import logging
import os
import re
import time
import tempfile
import shutil
from pathlib import Path

from browser_use import Agent, Browser, BrowserProfile, Tools
from browser_use.agent.views import ActionResult
from pydantic import BaseModel, Field
from backend.config import get_llm
from backend.worker.strategies import build_traversal_prompt, build_guided_prompt

log = logging.getLogger(__name__)

_PALM_IMAGE_PATH = Path(__file__).resolve().parents[1] / "assets" / "nebula_palm.png"
_PRICE_RE = re.compile(r'(?P<currency>[$€£]|USD|EUR|GBP)\s*(?P<amount>\d+(?:[.,]\d{1,2})?)', re.I)
_PERIOD_RE = re.compile(r'\b(day|daily|week|weekly|month|monthly|quarter|quarterly|year|yearly|annual|annually)\b', re.I)
_PRICING_WORD_RE = re.compile(r'\b(price|pricing|plan|subscription|checkout|paywall|trial|discount|billed)\b', re.I)


class FunnelPageLimitReached(Exception):
    """Raised internally after the configured funnel-page cap is captured."""


# --- Pydantic param models for custom actions ---

class ClickByTextParams(BaseModel):
    text: str = Field(description="Visible text on the element to click (e.g. 'Mid-sized', 'Yes', 'Continue')")


class FillInputParams(BaseModel):
    selector: str = Field(description="CSS selector for the input. Common: 'input[type=email]', 'input[name=age]'")
    value: str = Field(description="Value to fill (e.g. 'jane.doe@example.com', '30')")


class FillNumericFieldParams(BaseModel):
    field: str = Field(description="One of: age, height_ft, height_in, current_weight, goal_weight")


class FillDateParams(BaseModel):
    value: str = Field(default="2026-08-01", description="ISO date value to enter, e.g. 2026-08-01")


_LEGAL_TEXT_RE = re.compile(r'\b(privacy|terms|policy|conditions|cookie|legal)\b', re.I)


def _is_legal_page_url(url: str | None, title: str | None = None) -> bool:
    return bool(_LEGAL_TEXT_RE.search(f"{url or ''}\n{title or ''}"))


def _is_false_reset(steps: list[dict]) -> bool:
    if not steps:
        return False
    latest = steps[-1]
    url = (latest.get("url") or "").lower()
    question = latest.get("question_text") or ""
    options = latest.get("answer_options") or []
    if any(token in url for token in ("generated-questionary", "/quiz", "onboarding")):
        return bool(options or "?" in question)
    return False


def _build_tools() -> Tools:
    """Build a Tools instance with coordinate clicking + locator-based fallbacks.

    These extra actions exist because browser-use's DOM heuristics sometimes
    miss interactable elements on custom React widgets (BetterMe answer
    buttons, custom-styled email inputs, etc.). When that happens, the
    indexed click/input actions can't find the target. Coordinate clicking
    and CSS-selector fallbacks let the LLM still complete the funnel.
    """
    tools = Tools()

    # 1. Enable built-in coordinate clicking. Once enabled, the click action
    #    accepts coordinate_x / coordinate_y from the LLM screenshot. browser-use
    #    auto-rescales coords to viewport.
    try:
        tools.set_coordinate_clicking(True)
    except Exception as e:
        log.warning("set_coordinate_clicking unavailable: %s", e)

    # 2. Custom action: click any element by visible text. Bypasses the indexed
    #    element list entirely. Works when browser-use only saw a Continue
    #    button but the page actually has answer-option buttons.
    @tools.registry.action(
        "Click any visible element by its text content. Use ONLY when the indexed "
        "click action can't reach the element (e.g. no index for an answer option). "
        "Tries safe button-like elements only. Never use it for legal/consent links.",
        param_model=ClickByTextParams,
    )
    async def click_by_text(params: ClickByTextParams, browser_session):
        page = await browser_session.get_current_page()
        if not page:
            return ActionResult(error="No active page")
        if _LEGAL_TEXT_RE.search(params.text):
            return ActionResult(error=f"Refusing to click legal/policy text '{params.text}'")
        # Try multiple selector patterns; first match wins.
        text = params.text.replace('"', '\\"')
        candidates = [
            f'button:has-text("{text}")',
            f'[role="button"]:has-text("{text}")',
            f'div[role]:has-text("{text}")',
            f'input[type="radio"] + label:has-text("{text}")',
            f'input[type="checkbox"] + label:has-text("{text}")',
        ]
        for sel in candidates:
            try:
                elements = await page.get_elements_by_css_selector(sel)
                if elements:
                    await elements[0].click()
                    msg = f"Clicked element matching '{params.text}' via selector {sel}"
                    log.info("[click_by_text] %s", msg)
                    return ActionResult(extracted_content=msg)
            except Exception as e:
                log.debug("[click_by_text] selector %s failed: %s", sel, e)
                continue
        try:
            result = await page.evaluate(
                """
                ({text}) => {
                  const legalRe = /(privacy|terms|policy|conditions|cookie|legal)/i;
                  const wanted = text.trim().toLowerCase();
                  const els = Array.from(document.querySelectorAll('button, [role="button"], label, li, div, span'));
                  const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                  };
                  for (const el of els) {
                    if (!visible(el) || el.closest('a[href]')) continue;
                    const raw = (el.innerText || el.textContent || '').trim();
                    if (!raw || legalRe.test(raw)) continue;
                    const normalized = raw.replace(/\\s+/g, ' ').trim().toLowerCase();
                    if (normalized === wanted || normalized.includes(wanted)) {
                      el.click();
                      return {ok: true, message: `Clicked fallback text: ${raw.slice(0, 80)}`};
                    }
                  }
                  return {ok: false, message: `No safe fallback element for ${text}`};
                }
                """,
                {"text": params.text},
            )
            if result and result.get("ok"):
                msg = result.get("message", f"Clicked fallback text '{params.text}'")
                log.info("[click_by_text] %s", msg)
                return ActionResult(extracted_content=msg)
        except Exception as e:
            log.debug("[click_by_text] JS fallback failed: %s", e)
        return ActionResult(error=f"No clickable element with text '{params.text}' found")

    @tools.registry.action(
        "Safely tick a required consent checkbox without clicking Privacy Policy, Terms, or other legal links. "
        "Use this when a Continue button is disabled by a required consent/agreement checkbox.",
    )
    async def check_required_consent(browser_session):
        page = await browser_session.get_current_page()
        if not page:
            return ActionResult(error="No active page")
        try:
            result = await page.evaluate(
                """
                () => {
                  const inputs = Array.from(document.querySelectorAll('input[type="checkbox"], input[type="radio"]'));
                  for (const input of inputs) {
                    const rect = input.getBoundingClientRect();
                    const label = input.closest('label') || document.querySelector(`label[for="${input.id}"]`);
                    const text = (label?.innerText || input.getAttribute('aria-label') || '').trim();
                    if (!text || !/(agree|consent|terms|privacy|policy|process)/i.test(text)) continue;
                    if (input.checked) return {ok: true, message: 'Consent already checked'};
                    input.click();
                    return {ok: true, message: `Clicked consent input: ${text.slice(0, 80)}`};
                  }
                  const labels = Array.from(document.querySelectorAll('label'));
                  for (const label of labels) {
                    const text = (label.innerText || '').trim();
                    if (!/(agree|consent|terms|privacy|policy|process)/i.test(text)) continue;
                    const input = label.querySelector('input[type="checkbox"], input[type="radio"]');
                    if (input && !input.checked) {
                      input.click();
                      return {ok: true, message: `Clicked nested consent input: ${text.slice(0, 80)}`};
                    }
                  }
                  return {ok: false, message: 'No required consent checkbox found'};
                }
                """
            )
            if result and result.get("ok"):
                msg = result.get("message", "Consent checked")
                log.info("[check_required_consent] %s", msg)
                return ActionResult(extracted_content=msg)
            return ActionResult(error=(result or {}).get("message", "No required consent checkbox found"))
        except Exception as e:
            return ActionResult(error=f"check_required_consent failed: {e}")

    # 3. Custom action: fill an input by CSS selector. Bypasses the indexed
    #    element list. Works for email/text inputs that browser-use missed.
    @tools.registry.action(
        "Fill a form input by CSS selector. Use ONLY when the indexed input action "
        "can't reach the field (e.g. no index for an email/text input). "
        "Common selectors: 'input[type=email]', 'input[name=age]', 'input[placeholder*=name]'.",
        param_model=FillInputParams,
    )
    async def fill_input(params: FillInputParams, browser_session):
        page = await browser_session.get_current_page()
        if not page:
            return ActionResult(error="No active page")
        try:
            elements = await page.get_elements_by_css_selector(params.selector)
            if not elements:
                return ActionResult(error=f"No element matched selector {params.selector}")
            await elements[0].fill(params.value)
            msg = f"Filled '{params.value[:40]}' into {params.selector}"
            log.info("[fill_input] %s", msg)
            return ActionResult(extracted_content=msg)
        except Exception as e:
            return ActionResult(error=f"fill_input failed: {e}")

    _NUMERIC_FIELD_VALUES = {
        "age": [35],
        "height": [5, 9],
        "height_ft": [5, 9],
        "height_cm": [175],
        "current_weight": [180],
        "current_weight_lb": [180],
        "current_weight_kg": [82],
        "goal_weight": [160],
        "goal_weight_lb": [160],
        "goal_weight_kg": [73],
    }

    @tools.registry.action(
        "Fill an age/height/weight screen and advance. "
        "Use this for custom shadow-DOM numeric inputs that indexed input_text cannot fill. "
        "Pass field=age|height_ft|height_cm|current_weight|current_weight_kg|goal_weight|goal_weight_kg.",
        param_model=FillNumericFieldParams,
    )
    async def fill_numeric_screen(params: FillNumericFieldParams, browser_session):
        page = await browser_session.get_current_page()
        if not page:
            return ActionResult(error="No active page")
        values = _NUMERIC_FIELD_VALUES.get(params.field.lower())
        if not values:
            return ActionResult(error=f"Unknown numeric field '{params.field}'")
        try:
            result = await page.evaluate(
                """
                ({values}) => {
                  const roots = [];
                  const walk = (node) => {
                    roots.push(node);
                    node.querySelectorAll('*').forEach((el) => {
                      if (el.shadowRoot) walk(el.shadowRoot);
                    });
                  };
                  walk(document);
                  const inputs = roots.flatMap((root) => Array.from(root.querySelectorAll('input')));
                  const visible = inputs.filter((input) => {
                    const rect = input.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0 && !input.disabled && input.type !== 'hidden';
                  });
                  if (!visible.length) return {ok: false, message: 'No visible numeric inputs found'};
                  const slice = visible.slice(0, values.length);
                  slice.forEach((input, idx) => {
                    input.focus();
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter && setter.call(input, String(values[idx]));
                    input.value = String(values[idx]);
                    input.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
                    input.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
                    input.blur();
                  });
                  const buttons = roots.flatMap((root) => Array.from(root.querySelectorAll('button, [role="button"]')));
                  const next = buttons.find((el) => /next step|continue|next/i.test(el.innerText || el.textContent || ''));
                  if (next) next.click();
                  return {ok: true, message: `Filled with ${values.join('/')}${next ? ' and advanced' : ''}`};
                }
                """,
                {"values": values},
            )
            if result and result.get("ok"):
                msg = result.get("message", "Numeric screen filled")
                log.info("[fill_numeric_screen] %s field=%s", msg, params.field)
                return ActionResult(extracted_content=msg)
            return ActionResult(error=(result or {}).get("message", "Numeric screen fill failed"))
        except Exception as e:
            return ActionResult(error=f"fill_numeric_screen failed: {e}")

    @tools.registry.action(
        "Fill a visible date input and advance. Use this when a date/special occasion screen blocks progress.",
        param_model=FillDateParams,
    )
    async def fill_date_screen(params: FillDateParams, browser_session):
        page = await browser_session.get_current_page()
        if not page:
            return ActionResult(error="No active page")
        try:
            result = await page.evaluate(
                """
                ({value}) => {
                  const input = Array.from(document.querySelectorAll('input[type="date"], input[name*=date], input'))
                    .find((el) => {
                      const rect = el.getBoundingClientRect();
                      return rect.width > 0 && rect.height > 0 && !el.disabled && el.type !== 'hidden';
                    });
                  if (!input) return {ok: false, message: 'No visible date input found'};
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                  setter && setter.call(input, value);
                  input.value = value;
                  input.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
                  input.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
                  input.blur();
                  const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));
                  const next = buttons.find((el) => /continue|next|next step/i.test(el.innerText || el.textContent || ''));
                  if (next) next.click();
                  return {ok: true, message: `Filled date ${value}${next ? ' and advanced' : ''}`};
                }
                """,
                {"value": params.value},
            )
            if result and result.get("ok"):
                msg = result.get("message", "Date screen filled")
                log.info("[fill_date_screen] %s", msg)
                return ActionResult(extracted_content=msg)
            return ActionResult(error=(result or {}).get("message", "Date screen fill failed"))
        except Exception as e:
            return ActionResult(error=f"fill_date_screen failed: {e}")

    @tools.registry.action(
        "Advance the current funnel screen deterministically. Prefer this on ordinary funnel pages: it fills visible email/numeric/date inputs, checks consent, clicks a middle answer option, or clicks Continue/Next while avoiding legal links."
    )
    async def advance_funnel_step(browser_session):
        page = await browser_session.get_current_page()
        if not page:
            return ActionResult(error="No active page")
        try:
            result = await page.evaluate(
                """
                () => {
                  const legalRe = /(privacy|terms|policy|conditions|cookie|legal)/i;
                  const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0 && !el.disabled;
                  };
                  const textOf = (el) => (el.innerText || el.textContent || el.value || '').replace(/\s+/g, ' ').trim();
                  const roots = [];
                  const walk = (node) => { roots.push(node); node.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); }); };
                  walk(document);
                  const inputs = roots.flatMap((root) => Array.from(root.querySelectorAll('input'))).filter(visible).filter((el) => el.type !== 'hidden');
                  const email = inputs.find((el) => el.type === 'email' || /email/i.test(el.name || el.placeholder || el.getAttribute('aria-label') || '')) ||
                    inputs.find((el) => /enter your email|email/i.test((el.previousElementSibling?.innerText || '') + ' ' + (el.parentElement?.innerText || '')));
                  if (email) {
                    const fake = `jane.doe.${Math.floor(Math.random()*1e6)}@gmail.com`;
                    email.focus();
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    if (setter) setter.call(email, fake);
                    email.value = fake;
                    email.dispatchEvent(new Event('input', {bubbles:true, composed:true}));
                    email.dispatchEvent(new Event('change', {bubbles:true, composed:true}));
                    email.blur();
                    const btn = roots.flatMap((r)=>Array.from(r.querySelectorAll('button,[role="button"]'))).filter(visible).find((el)=>/continue|get my plan|next|submit/i.test(textOf(el)));
                    if (btn) {
                      btn.removeAttribute('disabled');
                      btn.disabled = false;
                      btn.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                      btn.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
                      btn.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                      btn.click();
                    }
                    return {ok:true, message:`Filled email ${fake} and advanced`};
                  }
                  const body = textOf(document.body).toLowerCase();
                  const nums = inputs.filter((el) => el.type === 'number' || el.inputMode === 'numeric' || /\b(ft|in|lbs|kg|years|age|weight|height)\b/i.test((el.name || el.placeholder || el.getAttribute('aria-label') || '') + ' ' + body));
                  if (nums.length) {
                    let vals = ['35'];
                    if (/height|how tall|ft\b|\bin\b/.test(body)) vals = ['5','9'];
                    else if (/goal weight/.test(body)) vals = ['160'];
                    else if (/current weight|weight/.test(body)) vals = ['180'];
                    nums.slice(0, vals.length).forEach((input, idx) => {
                      input.focus(); input.value = vals[idx];
                      input.dispatchEvent(new Event('input', {bubbles:true, composed:true}));
                      input.dispatchEvent(new Event('change', {bubbles:true, composed:true}));
                      input.blur();
                    });
                    const btn = roots.flatMap((r)=>Array.from(r.querySelectorAll('button,[role="button"]'))).filter(visible).find((el)=>/next step|continue|next/i.test(textOf(el)));
                    if (btn) btn.click();
                    return {ok:true, message:`Filled numeric ${vals.join('/')} and advanced`};
                  }
                  const selects = roots.flatMap((root) => Array.from(root.querySelectorAll('select'))).filter(visible);
                  if (selects.length >= 2 || /date of birth|birth|dob/.test(body)) {
                    const vals = ['Jan', '1', '1990'];
                    selects.slice(0, 3).forEach((sel, idx) => {
                      const opts = Array.from(sel.options || []);
                      const wanted = opts.find(o => new RegExp(vals[idx], 'i').test(o.text || o.value)) || opts.find(o => o.value && !o.disabled) || opts[1];
                      if (wanted) sel.value = wanted.value;
                      sel.dispatchEvent(new Event('input', {bubbles:true, composed:true}));
                      sel.dispatchEvent(new Event('change', {bubbles:true, composed:true}));
                    });
                    const btn = roots.flatMap((r)=>Array.from(r.querySelectorAll('button,[role="button"],div'))).filter(visible).find((el)=>/continue|next|skip this question/i.test(textOf(el)));
                    if (btn) btn.click();
                    return {ok:true, message:`Filled DOB selects${btn ? ' and advanced' : ''}`};
                  }
                  const date = inputs.find((el) => el.type === 'date' || /date|event/i.test(el.name || el.placeholder || body));
                  if (date) {
                    date.focus(); date.value = '2026-08-01';
                    date.dispatchEvent(new Event('input', {bubbles:true, composed:true})); date.dispatchEvent(new Event('change', {bubbles:true, composed:true})); date.blur();
                    const btn = roots.flatMap((r)=>Array.from(r.querySelectorAll('button,[role="button"],div'))).filter(visible).find((el)=>/continue|next|skip this question/i.test(textOf(el)));
                    if (btn) btn.click();
                    return {ok:true, message:'Filled date and advanced'};
                  }
                  const consent = inputs.find((el) => (el.type === 'checkbox' || el.type === 'radio') && !el.checked && /(agree|consent|privacy|terms|process)/i.test(textOf(el.closest('label') || el)));
                  if (consent) { consent.click(); return {ok:true, message:'Checked consent'}; }
                  const candidates = roots.flatMap((r)=>Array.from(r.querySelectorAll('button,[role="button"],label,li,div')))
                    .filter(visible)
                    .filter((el)=>!el.closest('a[href]'))
                    .map((el)=>({el, text:textOf(el)}))
                    .filter(({text})=>text && text.length <= 90 && !legalRe.test(text))
                    .filter(({text})=>!/^(continue|next|submit|back|help|docs|faq)$/i.test(text));
                  if (/choose|select|products|all that apply/i.test(body) && candidates.length >= 2) {
                    const picks = candidates.filter(({text})=>!/^(choose all that apply|none|none of the above|other)$/i.test(text)).slice(0, 4);
                    for (const {el} of picks) {
                      el.click();
                      el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                      el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
                      el.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                    }
                    const btn = roots.flatMap((r)=>Array.from(r.querySelectorAll('button,[role="button"],a,div')))
                      .filter(visible).find((el)=>/continue|next|get my plan|see results|start test|take the quiz|skip this question/i.test(textOf(el)) && !legalRe.test(textOf(el)));
                    if (btn) {
                      btn.removeAttribute('disabled');
                      btn.disabled = false;
                      btn.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                      btn.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
                      btn.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                      btn.click();
                    }
                    return {ok:true, message:`Selected ${picks.length} options${btn ? ' and clicked CTA' : ''}`};
                  }
                  if (candidates.length >= 2) {
                    const choice = candidates[Math.floor((candidates.length - 1) / 2)].el;
                    choice.click();
                    return {ok:true, message:`Clicked middle option: ${textOf(choice).slice(0,80)}`};
                  }
                  const cont = roots.flatMap((r)=>Array.from(r.querySelectorAll('button,[role="button"],a,div')))
                    .filter(visible).find((el)=>/continue|next|get my plan|see results|start test|take the quiz|skip this question/i.test(textOf(el)) && !legalRe.test(textOf(el)));
                  if (cont) { cont.click(); return {ok:true, message:`Clicked CTA: ${textOf(cont).slice(0,80)}`}; }
                  return {ok:false, message:'No deterministic funnel action found'};
                }
                """
            )
            if result and result.get("ok"):
                msg = result.get("message", "Advanced funnel step")
                log.info("[advance_funnel_step] %s", msg)
                return ActionResult(extracted_content=msg)
            return ActionResult(error=(result or {}).get("message", "No deterministic funnel action found"))
        except Exception as e:
            return ActionResult(error=f"advance_funnel_step failed: {e}")

    @tools.registry.action(
        "Bypass or solve a lightweight mini-game/test screen. Use when reaction-time, memory, lightning-round, or game screens stall; it clicks Skip/Continue/Next if available, otherwise clicks a few visible game tiles."
    )
    async def bypass_mini_game(browser_session):
        page = await browser_session.get_current_page()
        if not page:
            return ActionResult(error="No active page")
        try:
            result = await page.evaluate(
                """
                () => {
                  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !el.disabled; };
                  const textOf = (el) => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                  const legalRe = /(privacy|terms|policy|conditions|cookie|legal)/i;
                  const all = Array.from(document.querySelectorAll('button,[role="button"],a,div,li,label'));
                  const cta = all.filter(visible).find((el) => /skip|continue|next|start|done|finish|see results/i.test(textOf(el)) && !legalRe.test(textOf(el)));
                  if (cta) { cta.click(); return {ok:true, message:`Clicked mini-game CTA: ${textOf(cta).slice(0,80)}`}; }
                  const tiles = all.filter(visible).filter((el)=>!el.closest('a[href]')).filter((el)=>textOf(el).length < 80);
                  for (const tile of tiles.slice(0, 4)) tile.click();
                  return tiles.length ? {ok:true, message:`Clicked ${Math.min(tiles.length,4)} game tiles`} : {ok:false, message:'No mini-game controls found'};
                }
                """
            )
            if result and result.get("ok"):
                msg = result.get("message", "Bypassed mini-game")
                log.info("[bypass_mini_game] %s", msg)
                return ActionResult(extracted_content=msg)
            return ActionResult(error=(result or {}).get("message", "No mini-game controls found"))
        except Exception as e:
            return ActionResult(error=f"bypass_mini_game failed: {e}")

    @tools.registry.action(
        "Upload the bundled Nebula palm photo into the current page's first visible file input. "
        "Use on Nebula's palm-scan screen when no indexed file input is reachable."
    )
    async def upload_palm_image(browser_session):
        if not _PALM_IMAGE_PATH.exists():
            return ActionResult(error="Bundled palm image not present on disk")
        try:
            page = await browser_session.get_current_page()
            handle = await page.evaluate(
                """
                () => {
                  const visible = (el) => { const r = el.getBoundingClientRect(); return (r.width > 0 && r.height > 0) || el.offsetParent !== null; };
                  const roots = [];
                  const walk = (node) => { roots.push(node); node.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); }); };
                  walk(document);
                  const inputs = roots.flatMap((r) => Array.from(r.querySelectorAll('input[type="file"]')));
                  if (!inputs.length) return false;
                  const target = inputs.find(visible) || inputs[0];
                  target.setAttribute('data-funnel-upload', 'palm');
                  return true;
                }
                """
            )
            if not handle:
                return ActionResult(error="No file input found in DOM")
            try:
                file_input = await page.query_selector('input[type="file"][data-funnel-upload="palm"]')
                if not file_input:
                    return ActionResult(error="Tagged file input could not be located")
                await file_input.set_input_files(str(_PALM_IMAGE_PATH))
                msg = f"Uploaded palm image {_PALM_IMAGE_PATH.name} via deterministic helper"
                log.info("[upload_palm_image] %s", msg)
                return ActionResult(extracted_content=msg)
            finally:
                try:
                    await page.evaluate(
                        """
                        () => {
                          const el = document.querySelector('input[type="file"][data-funnel-upload="palm"]');
                          if (el) el.removeAttribute('data-funnel-upload');
                        }
                        """
                    )
                except Exception:
                    pass
        except Exception as e:
            return ActionResult(error=f"upload_palm_image failed: {e}")

    return tools


def _parse_json_lines(text: str) -> list[dict]:
    """Extract JSON objects from agent output text.

    Handles three formats:
    1. One JSON object per line (legacy prompt format).
    2. A single JSON object spanning the whole text (newer prompt: done.text is JSON).
    3. JSON embedded inside markdown ```json fences.
    """
    results: list[dict] = []
    if not text:
        return results

    def _normalize(obj: dict) -> dict | None:
        # The LLM occasionally writes step_number as a string ("36").
        if "step_number" in obj:
            try:
                obj["step_number"] = int(obj["step_number"])
            except (TypeError, ValueError):
                return None
        return obj

    # Strip markdown code fences first (```json ... ```).
    cleaned = re.sub(r'^```(?:json)?\s*\n', '', text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r'\n```\s*$', '', cleaned, flags=re.MULTILINE)

    # 2. Try whole-text-as-JSON first (matches the new done.text prompt).
    stripped = cleaned.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                normalized = _normalize(obj)
                if normalized is not None:
                    results.append(normalized)
                    return results  # Whole-blob match wins
        except json.JSONDecodeError:
            pass

    # 1. Fall back to line-by-line parsing.
    for line in cleaned.split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        match = re.search(r'\{.*\}', line)
        if not match:
            continue
        try:
            obj = json.loads(match.group())
        except json.JSONDecodeError:
            continue
        normalized = _normalize(obj)
        if normalized is not None:
            results.append(normalized)
    return results


def _extract_all_content(result) -> str:
    """Extract all text content from agent result for JSON parsing."""
    lines = []
    try:
        extracted = result.extracted_content()
        if extracted:
            for item in extracted:
                if isinstance(item, str):
                    lines.append(item)
    except Exception:
        pass
    return "\n".join(lines)


def _parse_funnel_step_from_memory(memory: str, step_num: int) -> dict | None:
    """
    Try to extract step details from the agent's memory text for a given step number.

    The agent writes memory like:
      "Step 36: 'Got it! And what's your goal weight?' - entered 130 lbs."
    """
    # Match: Step N: 'Question' - action
    pattern = (
        r'[Ss]tep\s+' + str(step_num) + r':\s+["\u201c\u2018]([^"\u201d\u2019]+)["\u201d\u2019]'
        r'(?:\s*[-–]\s*(.{0,120}))?'
    )
    m = re.search(pattern, memory)
    if m:
        question = m.group(1).strip()
        action_hint = (m.group(2) or "").strip().rstrip('.,')
        return {
            "step_number": step_num,
            "step_type": "question",
            "question_text": question,
            "action_taken": action_hint or "completed",
            "log": f"Step {step_num}: {question}" + (f" → {action_hint}" if action_hint else ""),
        }
    return None


def _clean_visible_line(line: str) -> str:
    line = re.sub(r'\s+', ' ', line).strip()
    line = re.sub(r'^\*?\[\d+\]\s*', '', line)
    line = re.sub(r'^<[^>]+/?>\s*', '', line).strip()
    line = re.sub(r'^<[^>]+/?>\s*', '', line).strip()
    line = re.sub(r'\s*<[^>]+/?>$', '', line).strip()
    line = re.sub(r'\b(role|aria-label|type|href|class|id)=["\'][^"\']*["\']', '', line).strip()
    return line


def _extract_dom_lines(browser_state) -> list[str]:
    try:
        dom_text = browser_state.dom_state.llm_representation()
    except Exception:
        return []
    lines: list[str] = []
    seen: set[str] = set()
    for raw in dom_text.splitlines():
        line = _clean_visible_line(raw)
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return lines


def _extract_answer_options(lines: list[str]) -> list[dict]:
    options: list[dict] = []
    question = _extract_question_text(lines) or ""
    has_question_context = "?" in question or any(
        token in question.lower()
        for token in ("choose", "select", "what ", "which ", "how ", "goal", "age")
    )
    for raw_line in lines:
        line = _clean_visible_line(raw_line)
        lower = line.lower()
        if not re.search(r'[A-Za-z0-9]', line):
            continue
        if len(line) > 90 or _PRICING_WORD_RE.search(line):
            continue
        if any(word in lower for word in ("continue", "next", "submit", "back", "skip", "privacy", "terms", "agree")):
            continue
        if not has_question_context and not ("role=" in raw_line.lower() or "button" in raw_line.lower()):
            if not re.search(r'\bage\b|^\d+\s*[-+]', lower):
                continue
        looks_interactive = (
            "role=" in raw_line.lower()
            or re.search(r'\b(button|option|radio|checkbox|link)\b', raw_line.lower())
            or re.match(r'^[A-Z0-9][A-Za-z0-9 ,./+\-–:]{1,70}$', line, flags=re.I)
        )
        if looks_interactive:
            label = re.sub(r'\s*(button|option|radio|checkbox|link)\s*$', '', line, flags=re.I).strip(' "\'')
            if re.fullmatch(r'(my profile|help|please review before continuing|as featured in|by|in|their \d+s?)', label, flags=re.I):
                continue
            if label and not any(o["label"].lower() == label.lower() for o in options):
                options.append({"label": label})
        if len(options) >= 12:
            break
    return options


def _extract_question_text(lines: list[str]) -> str | None:
    for line in lines[:80]:
        if len(line) < 8 or len(line) > 180:
            continue
        if "?" in line:
            return line
    for line in lines[:80]:
        lower = line.lower()
        if any(token in lower for token in ("what ", "which ", "how ", "select ", "choose ", "tell us")):
            return line
    return None


def _snapshot_from_browser_state(browser_state, step_number: int, url: str) -> dict:
    lines = _extract_dom_lines(browser_state)
    visible_text = "\n".join(lines[:80])
    title = getattr(browser_state, "title", "") or ""
    is_legal = bool(_LEGAL_TEXT_RE.search(f"{url}\n{title}"))
    is_pricing = bool(_PRICING_WORD_RE.search(visible_text) and _PRICE_RE.search(visible_text))
    return {
        "step_number": step_number,
        "step_type": "pricing" if is_pricing else "question",
        "question_text": _extract_question_text(lines),
        "answer_options": _extract_answer_options(lines),
        "url": url,
        "screenshot_path": None,
        "visible_text": visible_text[:12000],
        "is_pricing": is_pricing,
        "is_legal_page": is_legal,
    }


def _normalize_price(raw: str) -> str:
    return raw.replace(",", ".")


def _pricing_from_snapshot(snapshot: dict) -> dict:
    text = snapshot.get("visible_text") or ""
    prices = []
    for match in _PRICE_RE.finditer(text):
        currency = match.group("currency").upper()
        if currency == "$":
            currency = "USD"
        elif currency == "€":
            currency = "EUR"
        elif currency == "£":
            currency = "GBP"
        prices.append({
            "name": "Visible plan",
            "price": _normalize_price(match.group("amount")),
            "currency": currency,
            "period": "unknown",
            "features": [],
        })
    deduped: list[dict] = []
    for plan in prices:
        key = (plan["price"], plan["currency"])
        if key not in {(p["price"], p["currency"]) for p in deduped}:
            deduped.append(plan)
    period_match = _PERIOD_RE.search(text)
    if period_match:
        for plan in deduped:
            plan["period"] = period_match.group(1).lower()
    return {
        "step_type": "pricing",
        "step_number": snapshot.get("step_number"),
        "url": snapshot.get("url"),
        "screenshot_path": snapshot.get("screenshot_path"),
        "plans": deduped,
        "discounts": [],
        "trial_info": _extract_trial_info(text),
        "raw_text": text[:12000],
    }


def _extract_trial_info(text: str) -> dict | None:
    if not re.search(r'\btrial\b', text, re.I):
        return None
    days = None
    m = re.search(r'(\d+)\s*[- ]?\s*day\s+trial', text, re.I)
    if m:
        days = int(m.group(1))
    trial_price = None
    for line in text.splitlines():
        if "trial" in line.lower():
            price = _PRICE_RE.search(line)
            if price:
                trial_price = price.group(0)
                break
    return {"has_trial": True, "trial_days": days, "trial_price": trial_price}


def _has_pricing_evidence(pricing: dict | None) -> bool:
    if not pricing:
        return False
    if pricing.get("plans"):
        return True
    trial = pricing.get("trial_info")
    if isinstance(trial, dict) and (trial.get("trial_price") or trial.get("trial_days")):
        return True
    for discount in pricing.get("discounts") or []:
        if discount.get("discounted_price") or discount.get("original_price"):
            return True
    return False


def _looks_like_active_funnel_step(step: dict) -> bool:
    url = (step.get("url") or "").lower()
    question = step.get("question_text") or ""
    options = step.get("answer_options") or []
    if any(token in url for token in ("generated-questionary", "/quiz", "/onboarding", "/question")):
        return bool(question and (options or "?" in question))
    return False


async def _capture_screenshot(browser: Browser, screenshot_dir: Path, step_number: int) -> str | None:
    try:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = screenshot_dir / f"step_{step_number:03d}.png"
        await browser.take_screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception as e:
        log.debug("Failed to capture screenshot for step %s: %s", step_number, e)
        return None


async def _auto_recover_page(browser: Browser, snapshot: dict) -> str | None:
    """Deterministic recovery for screens where GPT mini often loops."""
    text = (snapshot.get("visible_text") or "").lower()
    is_products = "choose the products you like" in text
    is_dob = bool(re.search(r'\b(date of birth|birth date|d\.?o\.?b\.?|day .*? month .*? year|month .*? day .*? year|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b', text)) and "year" in text and ("month" in text or "day" in text)
    is_email_gate = "enter your email" in text or "invalid email" in text or ("email" in text and "save your" in text)
    is_palm_scan = "scan my palm" in text or ("palm" in text and "upload" in text)
    if not (is_products or is_dob or is_email_gate or is_palm_scan):
        return None
    try:
        page = await browser.get_current_page()
        if is_palm_scan and _PALM_IMAGE_PATH.exists():
            try:
                tagged = await page.evaluate(
                    """
                    () => {
                      const visible = (el) => { const r = el.getBoundingClientRect(); return (r.width > 0 && r.height > 0) || el.offsetParent !== null; };
                      const roots = [];
                      const walk = (node) => { roots.push(node); node.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); }); };
                      walk(document);
                      const inputs = roots.flatMap((r) => Array.from(r.querySelectorAll('input[type="file"]')));
                      if (!inputs.length) return false;
                      const target = inputs.find(visible) || inputs[0];
                      target.setAttribute('data-funnel-upload', 'palm');
                      return true;
                    }
                    """
                )
                if tagged:
                    file_input = await page.query_selector('input[type="file"][data-funnel-upload="palm"]')
                    if file_input:
                        await file_input.set_input_files(str(_PALM_IMAGE_PATH))
                        try:
                            await page.evaluate(
                                """
                                () => {
                                  const el = document.querySelector('input[type="file"][data-funnel-upload="palm"]');
                                  if (el) el.removeAttribute('data-funnel-upload');
                                }
                                """
                            )
                        except Exception:
                            pass
                        return f"auto uploaded {Path(_PALM_IMAGE_PATH).name}"
            except Exception as e:
                log.debug("auto palm upload failed: %s", e)
        if is_email_gate:
            result = await page.evaluate(
                """
                () => {
                  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !el.disabled; };
                  const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                  const roots = [];
                  const walk = (node) => { roots.push(node); node.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) walk(el.shadowRoot); }); };
                  walk(document);
                  const inputs = roots.flatMap((r) => Array.from(r.querySelectorAll('input'))).filter(visible).filter((el) => el.type !== 'hidden');
                  const target = inputs.find((el) => el.type === 'email') || inputs.find((el) => el.type === 'text') || inputs[0];
                  if (!target) return {ok: false, message: 'no email input found'};
                  const fake = `jane.doe.${Date.now()}@gmail.com`;
                  target.focus();
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                  if (setter) setter.call(target, fake);
                  target.value = fake;
                  target.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
                  target.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
                  target.blur();
                  const btn = roots.flatMap((r) => Array.from(r.querySelectorAll('button,[role="button"]')))
                    .filter(visible).find((el) => /continue|next|submit|get my plan/i.test(textOf(el)));
                  if (btn) {
                    btn.removeAttribute('disabled');
                    btn.disabled = false;
                    btn.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                    btn.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
                    btn.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                    btn.click();
                  }
                  return {ok: true, message: `set email ${fake}` + (btn ? ' and clicked CTA' : '')};
                }
                """
            )
            if result and result.get("ok"):
                return result.get("message") or "auto recovered email gate"
            return None
        if is_dob:
            result = await page.evaluate(
                """
                () => {
                  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !el.disabled; };
                  const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                  const selects = Array.from(document.querySelectorAll('select')).filter(visible);
                  const setSelect = (sel, wantedRegex, fallbackIdx) => {
                    if (!sel) return false;
                    const opts = Array.from(sel.options || []);
                    let pick = opts.find((o) => wantedRegex.test((o.text || '') + ' ' + (o.value || '')));
                    if (!pick) pick = opts.find((o) => o.value && !o.disabled && o.value !== '');
                    if (!pick) pick = opts[fallbackIdx];
                    if (!pick) return false;
                    sel.value = pick.value;
                    sel.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
                    sel.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
                    return true;
                  };
                  if (selects.length >= 2) {
                    const month = selects[0];
                    const day = selects[1];
                    const year = selects[2];
                    setSelect(month, /\\b(jun|june|6)\\b/i, 6);
                    if (day) setSelect(day, /^15$/, 15);
                    if (year) setSelect(year, /\\b1990\\b/, Math.max(0, (year.options || []).length - 30));
                    const btn = Array.from(document.querySelectorAll('button,[role="button"]')).filter(visible)
                      .find((el) => /continue|next|skip this question/i.test(textOf(el)));
                    if (btn) {
                      btn.removeAttribute('disabled');
                      btn.disabled = false;
                      btn.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                      btn.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
                      btn.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                      btn.click();
                    }
                    return {ok: true, message: 'set DOB selects' + (btn ? ' and clicked CTA' : '')};
                  }
                  return {ok: false, message: 'no DOB selects'};
                }
                """
            )
            if result and result.get("ok"):
                return result.get("message") or "auto recovered DOB"
            return None
        result = await page.evaluate(
            """
            () => {
              const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !el.disabled; };
              const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
              const legalRe = /(privacy|terms|policy|conditions|cookie|legal)/i;
              const all = Array.from(document.querySelectorAll('button,[role="button"],label,li,div,span'));
              const chips = all
                .filter(visible)
                .filter((el) => !el.closest('a[href]'))
                .filter((el) => {
                  const text = textOf(el);
                  return text && text.length <= 40 && !legalRe.test(text) && !/^(continue|next|back|help|docs|faq|choose the products you like)$/i.test(text);
                });
              for (const chip of chips.slice(0, 12)) {
                chip.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                chip.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
                chip.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                chip.click();
              }
              const ctas = all.filter(visible).filter((el) => /continue|next/i.test(textOf(el)) && !legalRe.test(textOf(el)));
              const btn = ctas[ctas.length - 1];
              if (btn) {
                btn.removeAttribute('disabled');
                btn.disabled = false;
                btn.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                btn.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
                btn.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                btn.click();
              }
              return {ok: !!btn, message: `selected ${Math.min(chips.length, 12)} product chips${btn ? ' and clicked continue' : ''}`};
            }
            """
        )
        if result and result.get("ok"):
            return result.get("message") or "auto recovered product screen"
    except Exception as e:
        log.debug("Auto recovery failed: %s", e)
    return None


async def run_traversal(
    competitor_name: str,
    funnel_url: str,
    config: dict | None = None,
    baseline_steps: list[dict] | None = None,
    on_progress: callable | None = None,
    competitor_slug: str | None = None,
    traversal_model: str | None = None,
    run_id: str | None = None,
) -> dict:
    """
    Run a funnel traversal and return structured results.

    Returns:
        {
            "steps": [...],
            "pricing": {...} or None,
            "summary": {...},
            "raw_output": str,
        }
    """
    available_file_paths: list[str] = []
    is_nebula = (
        (competitor_slug or "").lower() == "nebula"
        or "nebula" in (competitor_name or "").lower()
    )
    if is_nebula and _PALM_IMAGE_PATH.exists():
        available_file_paths.append(str(_PALM_IMAGE_PATH))

    if baseline_steps and not (config or {}).get("max_funnel_pages"):
        # TODO: thread available_files into guided prompt too when Nebula baselines exist.
        prompt = build_guided_prompt(competitor_name, funnel_url, baseline_steps)
    else:
        prompt = build_traversal_prompt(
            competitor_name, funnel_url, config, available_files=available_file_paths,
        )

    log.info("Starting traversal for %s (%s) — mode=%s model=%s",
             competitor_name, funnel_url, "guided" if baseline_steps else "freeform",
             traversal_model or "env-default")
    traversal_start = time.perf_counter()

    headless = os.getenv("BROWSER_HEADLESS", "true").lower() != "false"
    # Pydantic 2.12+ broke the field_validator that auto-creates user_data_dir,
    # so we must pass it explicitly. Each scan gets its own temp dir.
    _scan_user_data_dir = tempfile.mkdtemp(prefix="funnel-scan-")
    screenshot_dir = Path(tempfile.mkdtemp(prefix="funnel-shots-"))
    browser = Browser(
        browser_profile=BrowserProfile(
            headless=headless,
            chromium_sandbox=False,
            args=["--disable-dev-shm-usage", "--disable-gpu"],
            is_local=True,
            user_data_dir=_scan_user_data_dir,
            wait_for_network_idle_page_load_time=2.0,
            wait_between_actions=0.5,
        ),
    )

    max_funnel_pages = int((config or {}).get("max_funnel_pages") or (config or {}).get("max_pages") or 0)
    pricing_snapshots: list[dict] = []

    # Per-step capture via callback — so we don't depend on the agent
    # calling extract_content for each step. The agent tracks progress in
    # its memory field; we read it after every LLM step.
    callback_steps: list[dict] = []
    _last_funnel_step: list[int] = [0]   # mutable for closure
    _last_memory: list[str] = [""]
    _last_url: list[str] = [funnel_url]
    _stop_after_cap: list[bool] = [False]
    _captured_pages: list[int] = [0]

    async def _step_callback(browser_state, agent_output, n_steps: int):
        memory = (agent_output.memory or "") if agent_output else ""
        next_goal = (agent_output.next_goal or "") if agent_output else ""
        eval_prev = (agent_output.evaluation_previous_goal or "") if agent_output else ""
        actions = list(agent_output.action) if agent_output and agent_output.action else []
        url = browser_state.url if browser_state else _last_url[0]

        page_snapshot = _snapshot_from_browser_state(browser_state, n_steps, url)
        if not page_snapshot.get("screenshot_path"):
            screenshot_path = await _capture_screenshot(browser, screenshot_dir, n_steps)
            if screenshot_path:
                page_snapshot["screenshot_path"] = screenshot_path
        if page_snapshot.get("is_pricing"):
            pricing_snapshots.append(_pricing_from_snapshot(page_snapshot))
        # 1. Try to parse structured JSON from memory first (best signal — has
        # step_type, answer_options, pricing fields)
        json_objs = _parse_json_lines(memory)
        new_json = [
            s for s in json_objs
            if "step_number" in s
            and not s.get("summary")
            and s["step_number"] > _last_funnel_step[0]
        ]
        if new_json:
            for s in sorted(new_json, key=lambda x: x["step_number"]):
                step_with_url = {**page_snapshot, **s, "url": s.get("url") or url}
                callback_steps.append(step_with_url)
                _captured_pages[0] = len(callback_steps)
                if on_progress:
                    msg = s.get("log") or s.get("question_text") or f"Step {s['step_number']}"
                    on_progress({"step": s["step_number"], "type": s.get("step_type", "unknown"), "message": msg})
                _last_funnel_step[0] = s["step_number"]
            _last_memory[0] = memory
            _last_url[0] = url
            return

        # 2. Text fallback: detect "Steps 1-N completed" transitions
        m = re.search(r'[Ss]teps?\s+1[-\u2013\u2014]\s*(\d+)\s+completed', memory)
        if m:
            completed_up_to = int(m.group(1))
            while _last_funnel_step[0] < completed_up_to:
                next_step = _last_funnel_step[0] + 1
                step_data = _parse_funnel_step_from_memory(_last_memory[0], next_step) \
                    or _parse_funnel_step_from_memory(memory, next_step) \
                    or {
                        "step_number": next_step,
                        "step_type": "question",
                        "question_text": None,
                        "action_taken": "completed",
                        "log": f"Step {next_step} completed",
                    }
                step_data = {**page_snapshot, **step_data, "url": _last_url[0]}
                callback_steps.append(step_data)
                _captured_pages[0] = len(callback_steps)
                if on_progress:
                    on_progress({"step": next_step, "type": "question", "message": step_data["log"]})
                _last_funnel_step[0] = next_step
            _last_memory[0] = memory
            _last_url[0] = url
            return

        # 3. Always-on fallback: capture every browser-use step using agent_output
        # and browser_state directly. Doesn't depend on the model emitting any
        # specific JSON / memory format — works on any model. Fires once per
        # browser-use step. Smarter models will overwrite via path 1 above.
        if n_steps and n_steps > _last_funnel_step[0]:
            # Compose a short human log from what we have.
            action_summary = ""
            if actions:
                first = actions[0]
                if hasattr(first, "model_dump"):
                    d = first.model_dump(exclude_none=True)
                    if d:
                        name = next(iter(d.keys()))
                        params = d[name] if isinstance(d[name], dict) else {}
                        if name == "click" and "index" in params:
                            action_summary = f"clicked element {params['index']}"
                        elif name == "input_text" and "text" in params:
                            txt = str(params['text'])[:40]
                            action_summary = f"entered '{txt}'"
                        elif name == "scroll":
                            action_summary = "scrolled"
                        elif name == "go_to_url" and "url" in params:
                            action_summary = f"navigated to {params['url'][:60]}"
                        elif name == "done":
                            action_summary = "marked done"
                        else:
                            action_summary = name
            log_msg = (next_goal[:90] or eval_prev[:90] or memory[:90] or "step")
            if action_summary:
                log_msg = f"{log_msg} → {action_summary}"
            step_data = {
                **page_snapshot,
                "step_number": n_steps,
                "step_type": "question",
                "question_text": page_snapshot.get("question_text") or (next_goal[:200] if next_goal else None),
                "action_taken": action_summary or "completed",
                "log": log_msg,
                "url": url,
            }
            callback_steps.append(step_data)
            _captured_pages[0] = len(callback_steps)
            if on_progress:
                on_progress({"step": n_steps, "type": "question", "message": log_msg})
            _last_funnel_step[0] = n_steps

        if max_funnel_pages and _captured_pages[0] >= max_funnel_pages:
            log.info("Captured %d funnel pages; later agent actions are ignored for capped run", max_funnel_pages)
            _stop_after_cap[0] = True

        _last_memory[0] = memory
        _last_url[0] = url

    async def _should_stop() -> bool:
        return _stop_after_cap[0]

    try:
        agent = Agent(
            task=prompt,
            llm=get_llm(traversal_model=traversal_model),
            browser=browser,
            tools=_build_tools(),
            llm_timeout=180,
            register_new_step_callback=_step_callback,
            register_should_stop_callback=_should_stop,
            available_file_paths=available_file_paths or None,
            max_actions_per_step=2,
            max_failures=20,
            max_history_items=30,
            use_thinking=False,
        )
        # Cap browser-use iterations. Real funnels are 25-50 question steps,
        # but the agent may burn extra cycles on retries / overlays. 250 gives
        # 5x headroom while still catching runaway loops within ~30-40 min.
        try:
            result = await agent.run(max_steps=max(350, (max_funnel_pages or 0) + 12))
        except FunnelPageLimitReached as e:
            log.info("Stopping traversal after %d captured funnel pages", e.limit)
            result = None
            raw = ""
        else:
            raw = _extract_all_content(result)
    finally:
        try:
            await asyncio.wait_for(browser.stop(), timeout=15)
        except (asyncio.TimeoutError, Exception):
            log.warning("browser.stop() timed out or errored — Chrome process may be orphaned")
        # Clean up per-scan user data dir regardless of how the browser exited
        try:
            shutil.rmtree(_scan_user_data_dir, ignore_errors=True)
        except Exception:
            pass

    # Parse structured output from extracted content (highest quality, most structured)
    parsed = _parse_json_lines(raw)

    steps: list[dict] = []
    pricing = None
    summary = None

    for obj in parsed:
        if obj.get("summary"):
            summary = obj
        elif obj.get("step_type") == "pricing":
            pricing = obj
            # Pricing JSON now embeds the stop_reason (from the new done.text format).
            # Promote it to a synthetic summary so downstream code (PR body, dashboard)
            # gets the right stop_reason instead of falling back to "unknown".
            if obj.get("stop_reason") and not summary:
                summary = {
                    "summary": True,
                    "total_steps": obj.get("total_steps"),
                    "stop_reason": obj.get("stop_reason"),
                }
            if "step_number" in obj:
                steps.append(obj)
        elif "step_number" in obj:
            steps.append(obj)

        if on_progress and "step_number" in obj and not obj.get("summary"):
            log_msg = obj.get("log") or obj.get("question_text") or ""
            if log_msg:
                on_progress({
                    "step": obj.get("step_number", 0),
                    "type": obj.get("step_type", "unknown"),
                    "message": log_msg,
                })

    # Merge: prefer parsed (structured) steps; fill gaps with callback steps
    if steps:
        parsed_nums = {s["step_number"] for s in steps}
        for s in callback_steps:
            if s.get("step_number") not in parsed_nums:
                steps.append(s)
        steps.sort(key=lambda s: s.get("step_number", 0))
    elif callback_steps:
        # Nothing from extracted content — use what the callback captured
        steps = callback_steps
        # Also check if pricing was in memory
        for item in result.history if result and hasattr(result, 'history') else []:
            if item.model_output and item.model_output.memory:
                pricing_candidates = [
                    s for s in _parse_json_lines(item.model_output.memory)
                    if s.get("step_type") == "pricing"
                ]
                if pricing_candidates:
                    pricing = pricing_candidates[-1]
                    break

    snapshot_pricing = next((p for p in reversed(pricing_snapshots) if _has_pricing_evidence(p)), None)
    if not _has_pricing_evidence(pricing) and snapshot_pricing:
        pricing = snapshot_pricing
    elif pricing and snapshot_pricing:
        pricing = {
            **snapshot_pricing,
            **pricing,
            "screenshot_path": pricing.get("screenshot_path") or snapshot_pricing.get("screenshot_path"),
            "metadata": {
                **snapshot_pricing.get("metadata", {}),
                **pricing.get("metadata", {}),
            },
        }
    elif pricing and not _has_pricing_evidence(pricing):
        log.warning("Discarding pricing snapshot without price evidence for %s", competitor_name)
        pricing = None

    if not summary:
        summary = {
            "total_steps": len(steps),
            "stop_reason": "max_pages" if max_funnel_pages and len(steps) >= max_funnel_pages else "unknown",
        }
    elif max_funnel_pages and len(steps) >= max_funnel_pages and summary.get("stop_reason") == "unknown":
        summary["stop_reason"] = "max_pages"
    summary["total_steps"] = len(steps)
    if max_funnel_pages and len(steps) >= max_funnel_pages:
        summary["stop_reason"] = "max_pages"
        summary["total_steps"] = len(steps)
    elif max_funnel_pages and len(steps) >= max_funnel_pages and summary.get("stop_reason") == "unknown":
        summary["stop_reason"] = "max_pages"
    if summary.get("stop_reason") == "funnel_reset" and _is_false_reset(steps):
        log.warning("Overriding likely false funnel_reset for %s; active funnel question still visible", competitor_name)
        summary["stop_reason"] = "unknown"

    duration_ms = (time.perf_counter() - traversal_start) * 1000
    log.info("Traversal complete for %s: %d steps, stop=%s, pricing=%s (%.1fs)",
             competitor_name, len(steps), summary.get("stop_reason", "unknown"),
             "yes" if pricing else "no", duration_ms / 1000,
             extra={"step_count": len(steps), "duration_ms": round(duration_ms)})

    return {
        "steps": steps,
        "pricing": pricing,
        "summary": summary,
        "raw_output": raw,
    }


SCAN_TIMEOUT = 45 * 60  # 45 minutes — raise asyncio.TimeoutError if exceeded


def run_traversal_sync(
    competitor_name: str,
    funnel_url: str,
    config: dict | None = None,
    baseline_steps: list[dict] | None = None,
    on_progress: callable | None = None,
    competitor_slug: str | None = None,
    traversal_model: str | None = None,
    run_id: str | None = None,
) -> dict:
    """Synchronous wrapper for run_traversal."""
    coro = run_traversal(
        competitor_name=competitor_name,
        funnel_url=funnel_url,
        config=config,
        baseline_steps=baseline_steps,
        on_progress=on_progress,
        competitor_slug=competitor_slug,
        traversal_model=traversal_model,
        run_id=run_id,
    )
    return asyncio.run(asyncio.wait_for(coro, timeout=SCAN_TIMEOUT))
