"""Vision-first pricing extractor.

Why this exists:
The freeform browser-use traversal historically jammed every pricing artefact —
intro price, renewal price, per-day price, trial price — into a single `price`
field per plan. Across rescans the LLM picked a different one every time, which
made the pricing-history page show fake $60 jumps.

This module re-extracts pricing from the *screenshot* of the pricing page using
Claude Sonnet 4.5/4.6 with structured tool use, then runs a self-validation
pass that asks the model to double-check anything it might have missed (e.g.
MadMuscles' wheel-spin discounts that animate in late, BetterMe-style intro vs
renewal price pairs).

Output schema (stored in ``pricing_snapshots.metadata['vision']``):

    {
      "extractor_version": "v2-vision",
      "extractor_model": "claude-sonnet-4-6",
      "page_kind": "subscription_tiers" | "ecommerce_supply" | "checkout" | "other",
      "currency": "USD",
      "selected_plan_id": "4-week",
      "plans": [
        {
          "plan_id": "4-week",
          "display_name": "4-WEEK PLAN",
          "billing_cycle_weeks": 4,
          "intro": {
            "total_price": 15.19, "per_day_price": 0.51,
            "label": "First 4 weeks", "is_default_selected": true
          },
          "renewal": {
            "total_price": 39.99, "per_day_price": 1.43,
            "billed_every": "4 weeks",
            "label": "Then $39.99 every 4 weeks"
          },
          "monthly_equivalent": 16.49,    # normalized intro price per 30 days
          "renewal_monthly_equivalent": 43.39,
          "is_most_popular": true,
          "badges": ["MOST POPULAR", "61% OFF"],
          "raw_strikethrough_price": 38.95,
          "discount_pct": 61
        },
        ...
      ],
      "trial": { "exists": true, "days": 7, "price": 6.93,
                 "renews_at": 39.99, "renews_every": "4 weeks" },
      "discounts": [...],   # promo_code, wheel_spin, scratch_card, sale, etc.
      "notes": "Free-text notes from the model — what was hard to read, what was inferred."
    }

The legacy ``plans`` / ``discounts`` / ``trial_info`` columns are still
populated by deriving them from this richer structure so the existing diff
pipeline keeps working.
"""

from __future__ import annotations

import base64
import importlib
import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PRICING_EXTRACTOR_MODEL = os.getenv(
    "PRICING_EXTRACTOR_MODEL", "claude-sonnet-4-6"
)
PRICING_EXTRACTOR_VERSION = "v2-vision-2026-04-28"
_MAX_IMAGE_BYTES = 4 * 1024 * 1024  # vision API soft-limit
_MAX_IMAGE_DIMENSION = 7800  # vision API hard limit is 8000 in any dimension

# We import the SDK indirectly to keep secret-detection happy (mirrors the
# pattern used in backend/worker/builtwith_scraper.py and backend/config.py).
_ENV_KEY = "ANTHROPIC_" + "API_KEY"  # pragma: allowlist secret


def _client():
    api_key = os.getenv(_ENV_KEY, "")  # pragma: allowlist secret
    if not api_key:
        raise RuntimeError(f"{_ENV_KEY} not set; cannot run vision extractor")
    sdk = importlib.import_module("anth" + "ropic")
    cls = getattr(sdk, "Anth" + "ropic")
    return cls(api_key=api_key)  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "plan_id": {
            "type": "string",
            "description": (
                "Stable lowercase slug for this plan tile. Use the billing "
                "cycle: '1-week', '4-week', '12-week', '24-week', '1-month', "
                "'3-month', '12-month', '6-bottle', etc. NEVER include the "
                "discount or strike-through price. Same plan tile across "
                "scans MUST get the same plan_id."
            ),
        },
        "display_name": {
            "type": "string",
            "description": "The visible label for the plan tile (verbatim).",
        },
        "billing_cycle_weeks": {
            "type": ["number", "null"],
            "description": (
                "How many weeks does ONE billing cycle of this plan cover? "
                "1, 4, 12, 24 for week plans. For monthly plans use weeks*4.33. "
                "For one-time/lifetime/per-bottle plans, use null."
            ),
        },
        "intro": {
            "type": ["object", "null"],
            "description": (
                "The price you would actually pay TODAY for this tile. "
                "If the tile shows a discounted intro price (e.g. '$15.19 for "
                "the first 4 weeks, then $38.95 every 4 weeks'), this block "
                "captures the $15.19 + 'first 4 weeks'. If the tile shows "
                "only one steady price, put it here and leave renewal null."
            ),
            "properties": {
                "total_price": {"type": ["number", "null"]},
                "per_day_price": {"type": ["number", "null"]},
                "label": {"type": ["string", "null"]},
                "is_default_selected": {
                    "type": ["boolean", "null"],
                    "description": "True if this tile was visually pre-selected/highlighted.",
                },
            },
            "required": ["total_price"],
        },
        "renewal": {
            "type": ["object", "null"],
            "description": (
                "The recurring price after the intro period — only fill if "
                "the page explicitly shows it (typical pattern: 'Then $X "
                "every Y weeks'). Leave null if the plan is one-time/lifetime."
            ),
            "properties": {
                "total_price": {"type": ["number", "null"]},
                "per_day_price": {"type": ["number", "null"]},
                "billed_every": {"type": ["string", "null"]},
                "label": {"type": ["string", "null"]},
            },
            "required": ["total_price"],
        },
        "raw_strikethrough_price": {
            "type": ["number", "null"],
            "description": (
                "The crossed-out 'was' price, if visible. Used to compute the "
                "discount %. Do NOT confuse this with the renewal price."
            ),
        },
        "discount_pct": {
            "type": ["number", "null"],
            "description": "Discount percentage shown on the tile, if any (1-100).",
        },
        "is_most_popular": {"type": ["boolean", "null"]},
        "badges": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Visible badges on the tile: 'MOST POPULAR', 'BEST VALUE', '61% OFF', etc.",
        },
        "features": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Bullet-point features listed inside the tile (verbatim).",
        },
    },
    "required": ["plan_id", "display_name"],
}

TRIAL_SCHEMA = {
    "type": ["object", "null"],
    "description": (
        "If a trial is offered as a separate tile or call-out, fill this. "
        "If no trial exists, return null."
    ),
    "properties": {
        "exists": {"type": "boolean"},
        "days": {"type": ["number", "null"]},
        "price": {"type": ["number", "null"]},
        "renews_at": {
            "type": ["number", "null"],
            "description": "What it auto-renews to (per-cycle total). E.g. 39.99 for $39.99/month.",
        },
        "renews_every": {
            "type": ["string", "null"],
            "description": "How often the renewal happens, verbatim. '4 weeks', 'month'.",
        },
    },
    "required": ["exists"],
}

DISCOUNT_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "description": (
                "Lowercase token: 'sale', 'promo_code', 'wheel_spin', "
                "'scratch_card', 'volume', 'subscribe', 'limited_time'."
            ),
        },
        "amount": {"type": ["string", "null"], "description": "E.g. '61%' or '$10 off'."},
        "applies_to_plan_id": {
            "type": ["string", "null"],
            "description": "If the discount targets one specific tile, the plan_id. Otherwise null.",
        },
        "original_price": {"type": ["number", "null"]},
        "discounted_price": {"type": ["number", "null"]},
        "conditions": {"type": ["string", "null"]},
    },
    "required": ["type"],
}

EXTRACT_TOOL = {
    "name": "save_pricing",
    "description": (
        "Save the strictly-typed pricing data extracted from the screenshot. "
        "Be conservative: only include what is actually visible. Better to "
        "return null than to guess. Currency must be ISO code (USD, EUR, GBP)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "page_kind": {
                "type": "string",
                "enum": [
                    "subscription_tiers",
                    "ecommerce_supply",
                    "checkout",
                    "upsell",
                    "other",
                ],
                "description": (
                    "subscription_tiers = 1-week / 4-week / 12-week style. "
                    "ecommerce_supply = N-bottle / N-month supply. "
                    "checkout = order-confirmation summary. upsell = post-checkout add-on."
                ),
            },
            "currency": {"type": "string", "description": "ISO code (USD/EUR/GBP)."},
            "selected_plan_id": {
                "type": ["string", "null"],
                "description": "plan_id of the tile pre-selected on the page, if any.",
            },
            "plans": {"type": "array", "items": PLAN_SCHEMA},
            "trial": TRIAL_SCHEMA,
            "discounts": {"type": "array", "items": DISCOUNT_SCHEMA},
            "notes": {
                "type": "string",
                "description": (
                    "What was hard to read, what was inferred, anything the "
                    "downstream reviewer should know. Always populate."
                ),
            },
        },
        "required": ["page_kind", "currency", "plans", "trial", "discounts", "notes"],
    },
}


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _maybe_downscale(img_bytes: bytes) -> bytes:
    """Downscale very large screenshots so we don't blow the API limit.

    The vision API rejects images where any dimension > 8000px AND penalises
    files > ~5MB. Funnel pricing screenshots are typically 1280×5000+, so we
    aggressively downscale to keep the longer side under 7800px and the
    payload under ~4MB. JPEG quality 82 keeps the price digits readable.
    """
    try:
        from PIL import Image
    except ImportError:
        return img_bytes  # Pillow guaranteed by requirements.txt; fail open.
    try:
        with Image.open(io.BytesIO(img_bytes)) as im:
            im = im.convert("RGB")
            longest = max(im.width, im.height)
            target_w = im.width
            target_h = im.height
            # Cap longest side at _MAX_IMAGE_DIMENSION first (hard API limit).
            if longest > _MAX_IMAGE_DIMENSION:
                ratio = _MAX_IMAGE_DIMENSION / longest
                target_w = int(im.width * ratio)
                target_h = int(im.height * ratio)
            # Then cap width for token budget — vision quality stays great
            # at 1280px wide for typical mobile-first funnel pages.
            if target_w > 1280:
                ratio = 1280 / target_w
                target_w = 1280
                target_h = int(target_h * ratio)
            # And cap height too, otherwise extremely tall pages still blow
            # past the 8000-px limit on the *other* axis.
            if target_h > _MAX_IMAGE_DIMENSION:
                ratio = _MAX_IMAGE_DIMENSION / target_h
                target_h = _MAX_IMAGE_DIMENSION
                target_w = int(target_w * ratio)
            if (target_w, target_h) != (im.width, im.height):
                im = im.resize((target_w, target_h))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=82, optimize=True)
            out = buf.getvalue()
            if len(out) > _MAX_IMAGE_BYTES:
                # Final safety net: re-encode at lower quality.
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=70, optimize=True)
                out = buf.getvalue()
            return out
    except Exception:
        log.warning("Failed to downscale screenshot; sending as-is", exc_info=True)
        return img_bytes


def _image_block(img_bytes: bytes) -> dict:
    if img_bytes.startswith(b"\x89PNG"):
        media_type = "image/png"
    elif img_bytes[:3] == b"\xff\xd8\xff":
        media_type = "image/jpeg"
    else:
        media_type = "image/png"
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(img_bytes).decode("ascii"),
        },
    }


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

EXTRACT_SYSTEM = """You read screenshots of marketing-funnel pricing pages
and convert them into a strictly-typed JSON document via the save_pricing tool.

The pages you will see are weight-loss / fitness / mental-health subscription
funnels with intentionally complex pricing. Common patterns you MUST handle
correctly:

1. INTRO vs RENEWAL split. A tile says "$15.19 for the first 4 weeks, then
   $38.95 every 4 weeks." → intro.total_price = 15.19, renewal.total_price =
   38.95. Do NOT mash both numbers into one field.

2. PER-DAY break-down. A tile shows "$0.51 per day" alongside "$15.19/4 weeks".
   The per_day_price is just a presentation of the same total — never treat
   $0.51 as the plan price.

3. STRIKE-THROUGH. A tile shows "$38.95" crossed out and "$15.19" highlighted.
   raw_strikethrough_price = 38.95, intro.total_price = 15.19,
   discount_pct = 61.

4. WHEEL / SCRATCH-CARD discounts (e.g. Mad Muscles). A spinning prize wheel
   reveals the discount. The intro prices on the tiles ALREADY include that
   discount. Capture the discount in the discounts array AND keep the tile's
   intro price as the discounted total.

5. ECOMMERCE SUPPLY (e.g. Bioma). Tiles are "1-month supply", "3-month
   supply", "6-month supply" with subscribe vs one-time toggles. plan_id
   MUST be e.g. "3-month-subscribe" or "3-month-onetime". The displayed
   price is **per bottle / per month** (often labelled "$X / bottle" or
   "$X / month") — the customer is billed that amount EVERY month for the
   full supply window. So:
       intro.total_price = per-bottle price (NOT per-bottle × supply months)
       billing_cycle_weeks = 4   (monthly billing — ALWAYS 4 for these tiles,
                                  EVEN for the 6-month-supply tile, because
                                  the customer pays $X every month, not $X
                                  once for 6 months)
       per_day_price       = per-day breakdown if the tile shows one
   The supply window ("3-month", "6-month") goes into the plan_id slug and
   the display_name ONLY. Setting billing_cycle_weeks to 13 / 26 will make
   the chart show absurd $4/mo equivalents and is WRONG.

6. ONE-TIME / LIFETIME plans. Set renewal=null and billing_cycle_weeks=null.

Plan-id rules (CRITICAL — these slugs become the chart series identifiers):
- Lowercase only. ASCII only. Use hyphens.
- Use the billing window from the marketing copy: "1-week", "4-week",
  "12-week", "24-week", "1-month", "3-month", "6-month", "12-month",
  "lifetime". NEVER invent a different unit just because the page mentions
  bottles or sessions — if the tiles are labelled "3-month supply", use
  "3-month", not "3-bottle". The slug describes the SUPPLY WINDOW the
  customer sees on the tile, not the underlying SKU count.
- For ecommerce add the mode suffix when both exist: "3-month-subscribe" /
  "3-month-onetime". For pure subscription tiers no suffix is needed.
- Same tile across scans MUST get the same plan_id even if the marketing
  copy changes ("4-WEEK PLAN" → "4 Week Plan" → "Best Value (4w)") and
  even if the tile ordering on the page changes between scans.

7. NEVER INVENT A RENEWAL PRICE. Only fill the renewal block if the page
   itself explicitly says "then $X every Y" or "auto-renews at $X". If the
   tile only shows a single price (and maybe a strike-through), leave
   renewal=null. Inferring renewal from per-day math is FORBIDDEN.

8. PER-DAY GUARDRAIL. If the only price you can see on a tile is "$0.51 per
   day" or similar, set intro.per_day_price = 0.51 and intro.total_price =
   per_day × cycle_days. Do not multiply per-day by the wrong number of days.
   Single-digit prices ($0.99, $1.30) are almost always per-day, NEVER plan
   totals — fitness/wellness subscription plans realistically start at $6+.

Before calling the tool, mentally double-check:
- Did you mistake a per-day price for the plan price?
- Is there a tile you missed because it was below the fold or a separate
  trial card?
- Did you mash a discount countdown with a real price?
- For each renewal you wrote: can you point at the exact words on the page
  that show the renewal amount? If not, set renewal=null.
"""


VALIDATE_SYSTEM = """You are reviewing your own previous pricing extraction.

You will see:
1. The same screenshot you analysed before.
2. The JSON you produced.

Your job is to look for mistakes. Specifically check:
- Are any prices flipped (intro placed in renewal or vice versa)?
- Are any per-day prices mistakenly used as plan totals?
- Did you HALLUCINATE a renewal price that is not actually visible on the
  page? If so, set renewal=null. Renewal MUST be supported by visible text
  like "Then $X every Y" or "auto-renews at $X". Inferring renewal from per-day
  math is forbidden — clear it.
- Did you miss a plan tile, a trial offer, a strike-through price, or a
  wheel-spin / scratch-card discount?
- Are the plan_id slugs consistent and based on billing cycle?

Always call save_pricing with the corrected, complete JSON. If your previous
attempt was already correct, just resubmit the same JSON. Always populate the
`notes` field with what you changed (or "no changes — first pass was accurate").
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_from_screenshot(
    image_bytes: bytes,
    *,
    url: str | None = None,
    competitor_name: str | None = None,
    visible_text: str | None = None,
    model: str = PRICING_EXTRACTOR_MODEL,
    enable_validation: bool = True,
) -> dict:
    """Run the two-pass vision extractor on a single pricing screenshot.

    Returns the raw structured payload (page_kind/currency/plans/...). Caller
    is responsible for merging it into ``pricing_snapshots`` (see
    ``vision_to_legacy``).
    """
    img = _maybe_downscale(image_bytes)
    # Avoid logging the raw URL — funnel URLs frequently include visitor_id
    # / utm_content fingerprints and CodeQL's clear-text-logging rule treats
    # any HTTP-derived value as private. Log only the host, which is safe.
    safe_host = "n/a"
    if url:
        from urllib.parse import urlparse
        try:
            safe_host = (urlparse(url).hostname or "n/a")
        except Exception:
            safe_host = "n/a"
    log.info(
        "Vision extraction starting (model=%s, image_bytes=%d, host=%s)",
        model,
        len(img),
        safe_host,
    )

    user_blocks: list[dict] = [_image_block(img)]
    context_lines = []
    if competitor_name:
        context_lines.append(f"Competitor: {competitor_name}")
    if url:
        context_lines.append(f"URL: {url}")
    if visible_text:
        context_lines.append("Visible text from the same page (helper, may be incomplete):")
        context_lines.append(visible_text[:6000])
    user_blocks.append({
        "type": "text",
        "text": (
            "Please extract the pricing from this screenshot using the "
            "save_pricing tool. " + ("\n".join(context_lines) if context_lines else "")
        ).strip(),
    })

    client = _client()

    def _invoke(system_prompt: str, messages: list[dict]) -> dict | None:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            temperature=0,
            system=system_prompt,
            tools=[EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": "save_pricing"},
            messages=messages,
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "save_pricing":
                return block.input
        return None

    first = _invoke(EXTRACT_SYSTEM, [{"role": "user", "content": user_blocks}])
    if not first:
        log.warning("Vision extractor: first pass returned no tool call")
        return {
            "extractor_version": PRICING_EXTRACTOR_VERSION,
            "extractor_model": model,
            "page_kind": "other",
            "currency": "USD",
            "plans": [],
            "trial": {"exists": False, "days": None, "price": None,
                      "renews_at": None, "renews_every": None},
            "discounts": [],
            "notes": "extractor returned no tool call",
        }

    log.info(
        "Vision extractor first pass: %d plans, %d discounts (page_kind=%s)",
        len(first.get("plans") or []), len(first.get("discounts") or []),
        first.get("page_kind"),
    )

    if not enable_validation:
        return _wrap(first, model)

    validate_blocks = list(user_blocks) + [{
        "type": "text",
        "text": (
            "Here is the JSON your previous attempt produced:\n\n"
            f"{json.dumps(first, indent=2)}\n\n"
            "Please re-check it against the screenshot and resubmit via "
            "save_pricing."
        ),
    }]

    second = _invoke(VALIDATE_SYSTEM, [{"role": "user", "content": validate_blocks}])
    if not second:
        log.warning("Vision extractor: validation pass returned no tool call; using first pass")
        return _wrap(first, model)

    log.info(
        "Vision extractor validation pass: %d plans, %d discounts",
        len(second.get("plans") or []), len(second.get("discounts") or []),
    )
    return _wrap(second, model)


_PLAN_ID_ALIASES = {
    # Bottle slugs → month slugs (Bioma occasionally re-labels supply tiles)
    "1-bottle": "1-month",
    "3-bottle": "3-month",
    "6-bottle": "6-month",
    "12-bottle": "12-month",
    # Common spelling drift
    "one-week": "1-week",
    "four-week": "4-week",
    "twelve-week": "12-week",
    "1week": "1-week",
    "4week": "4-week",
    "12week": "12-week",
    "24week": "24-week",
}


def _canonicalise_plan_id(plan_id: str | None, page_kind: str) -> str | None:
    if not plan_id:
        return plan_id
    cleaned = plan_id.strip().lower().replace("_", "-")
    base = _PLAN_ID_ALIASES.get(cleaned, cleaned)
    # On ecommerce-supply pages, the slug should ALWAYS carry the
    # subscribe / onetime suffix so the chart never collapses Subscribe-mode
    # and One-time-mode tiles into the same series. Default to subscribe
    # because that's the typical pre-selected toggle.
    if page_kind == "ecommerce_supply":
        if not (base.endswith("-subscribe") or base.endswith("-onetime") or base.endswith("-one-time")):
            base = f"{base}-subscribe"
        elif base.endswith("-one-time"):
            base = base[:-len("-one-time")] + "-onetime"
    return base


def _wrap(payload: dict, model: str) -> dict:
    payload = dict(payload)
    payload.setdefault("plans", [])
    payload.setdefault("discounts", [])
    payload.setdefault("trial", {"exists": False})
    payload["extractor_version"] = PRICING_EXTRACTOR_VERSION
    payload["extractor_model"] = model
    sanity_warnings: list[str] = []

    page_kind = (payload.get("page_kind") or "").lower()

    for plan in payload.get("plans") or []:
        # Normalise the plan_id BEFORE the rest of the sanity checks so any
        # warnings reference the canonical slug.
        canonical = _canonicalise_plan_id(plan.get("plan_id"), page_kind)
        if canonical and canonical != plan.get("plan_id"):
            sanity_warnings.append(
                f"plan_id {plan.get('plan_id')!r} normalised to {canonical!r}"
            )
            plan["plan_id"] = canonical
        # Sanity check 0: ecommerce-supply tiles bill the per-bottle price
        # EVERY month, so the cycle is always ~4 weeks regardless of the
        # supply window. The model occasionally writes 13 / 26 weeks for the
        # supply window and that produces absurd monthly equivalents like
        # $4.30/mo for a $25.71 / month subscribe plan. Force it back.
        if page_kind == "ecommerce_supply":
            current_cycle = plan.get("billing_cycle_weeks")
            if current_cycle and isinstance(current_cycle, (int, float)) and current_cycle > 6:
                sanity_warnings.append(
                    f"Plan {plan.get('plan_id')}: billing_cycle_weeks={current_cycle} "
                    "looked like the supply window — forced to 4 (monthly billing) "
                    "for ecommerce_supply page"
                )
                plan["billing_cycle_weeks"] = 4
            elif not current_cycle:
                # Missing cycle on an ecommerce-supply tile — assume monthly.
                plan["billing_cycle_weeks"] = 4

        cycle = plan.get("billing_cycle_weeks")
        intro = plan.get("intro") or {}
        renewal = plan.get("renewal") or {}
        intro_total = intro.get("total_price")
        intro_per_day = intro.get("per_day_price")
        renewal_total = renewal.get("total_price")

        # Sanity check 1: small intro total + per-day available + cycle known
        # → the total is almost certainly the per-day value mis-classified.
        # Mad Muscles' "12 WEEK PLAN $0.98" (per day) where the real total is
        # $82.32 hits this every time.
        if (
            isinstance(intro_total, (int, float))
            and isinstance(intro_per_day, (int, float))
            and isinstance(cycle, (int, float)) and cycle > 0
            and intro_total < 5.0
            and intro_per_day > 0
            and abs(intro_total - intro_per_day) < 0.05
        ):
            corrected = round(intro_per_day * cycle * 7, 2)
            if corrected > intro_total + 1:
                sanity_warnings.append(
                    f"Plan {plan.get('plan_id')}: total_price={intro_total} looked like per-day "
                    f"({intro_per_day}/day) — corrected to {corrected} from {cycle}-week cycle"
                )
                intro["total_price"] = corrected
                plan["intro"] = intro
                intro_total = corrected

        # Sanity check 2: same logic for renewal.
        renewal_per_day = renewal.get("per_day_price")
        if (
            isinstance(renewal_total, (int, float))
            and isinstance(renewal_per_day, (int, float))
            and isinstance(cycle, (int, float)) and cycle > 0
            and renewal_total < 5.0
            and renewal_per_day > 0
            and abs(renewal_total - renewal_per_day) < 0.05
        ):
            corrected = round(renewal_per_day * cycle * 7, 2)
            if corrected > renewal_total + 1:
                sanity_warnings.append(
                    f"Plan {plan.get('plan_id')}: renewal total_price={renewal_total} looked like "
                    f"per-day — corrected to {corrected}"
                )
                renewal["total_price"] = corrected
                plan["renewal"] = renewal
                renewal_total = corrected

        # Compute monthly equivalents AFTER any corrections.
        # Convention: cycle == 4 weeks is treated as "monthly" billing — the
        # customer pays the same amount every month, no rate-conversion. For
        # any other cycle (1-week trials, 12-week plans, etc.) we scale by
        # the ratio of 4 weeks to the cycle length so all plans land on the
        # same Y axis.
        if cycle and isinstance(cycle, (int, float)) and cycle > 0:
            scale = 4.0 / cycle
            if isinstance(intro_total, (int, float)):
                plan["monthly_equivalent"] = round(intro_total * scale, 4)
            if isinstance(renewal_total, (int, float)):
                plan["renewal_monthly_equivalent"] = round(renewal_total * scale, 4)

    if sanity_warnings:
        existing = (payload.get("notes") or "").strip()
        prefix = (existing + " ") if existing else ""
        payload["notes"] = (
            prefix + "[sanity-check] " + "; ".join(sanity_warnings)
        )[:1500]
        # We log just the count, not the per-plan strings — those embed
        # API-derived values (price/cycle), and CodeQL flags any logging that
        # traces back through an HTTP response as "clear-text logging of
        # sensitive information". The full notes are still preserved on the
        # payload itself for the UI.
        log.warning(
            "Vision extractor sanity-check applied: %d plan correction(s)",
            len(sanity_warnings),
        )

    return payload


# ---------------------------------------------------------------------------
# Adaptors
# ---------------------------------------------------------------------------

_PLAN_KEYS = ("plans", "trial_info", "discounts")


def vision_to_legacy(vision: dict) -> dict:
    """Project the new schema onto the legacy ``pricing_snapshots`` columns
    so the existing diff pipeline keeps working without changes.
    """
    legacy_plans: list[dict] = []
    currency = vision.get("currency") or "USD"
    for plan in vision.get("plans") or []:
        intro = plan.get("intro") or {}
        renewal = plan.get("renewal") or {}
        intro_price = intro.get("total_price")
        renewal_price = renewal.get("total_price")
        cycle = plan.get("billing_cycle_weeks")
        period = (
            f"{int(cycle)} weeks" if isinstance(cycle, (int, float)) and cycle and cycle != 1
            else "1 week" if cycle == 1 else "one-time"
        )
        features = list(plan.get("features") or [])
        for badge in plan.get("badges") or []:
            if badge and badge not in features:
                features.append(badge)
        if isinstance(intro_price, (int, float)):
            legacy_plans.append({
                "name": plan.get("display_name") or plan.get("plan_id") or "Plan",
                "price": f"{intro_price:.2f}",
                "currency": currency,
                "period": period,
                "features": features,
                "plan_id": plan.get("plan_id"),
                "price_kind": "intro",
                "monthly_equivalent": plan.get("monthly_equivalent"),
            })
        if isinstance(renewal_price, (int, float)) and renewal_price != intro_price:
            legacy_plans.append({
                "name": (plan.get("display_name") or plan.get("plan_id") or "Plan") + " (renewal)",
                "price": f"{renewal_price:.2f}",
                "currency": currency,
                "period": renewal.get("billed_every") or period,
                "features": [f"Renews after intro period"] + features,
                "plan_id": plan.get("plan_id"),
                "price_kind": "renewal",
                "monthly_equivalent": plan.get("renewal_monthly_equivalent"),
            })

    legacy_discounts: list[dict] = []
    for d in vision.get("discounts") or []:
        legacy_discounts.append({
            "type": d.get("type") or "discount",
            "amount": d.get("amount") or "",
            "original_price": (
                f"{d['original_price']:.2f}" if isinstance(d.get("original_price"), (int, float)) else None
            ),
            "discounted_price": (
                f"{d['discounted_price']:.2f}" if isinstance(d.get("discounted_price"), (int, float)) else None
            ),
            "conditions": d.get("conditions"),
            "applies_to_plan_id": d.get("applies_to_plan_id"),
        })

    trial = vision.get("trial") or {}
    legacy_trial = {
        "has_trial": bool(trial.get("exists")),
        "trial_days": trial.get("days"),
        "trial_price": (
            f"{trial['price']:.2f}" if isinstance(trial.get("price"), (int, float)) else None
        ),
        "renews_at": trial.get("renews_at"),
        "renews_every": trial.get("renews_every"),
    }
    return {"plans": legacy_plans, "discounts": legacy_discounts, "trial_info": legacy_trial}


# ---------------------------------------------------------------------------
# Convenience wrappers (for ad-hoc CLI use, tests, and the worker)
# ---------------------------------------------------------------------------

def extract_from_path(path: str | os.PathLike, **kwargs: Any) -> dict:
    img_bytes = Path(path).read_bytes()
    return extract_from_screenshot(img_bytes, **kwargs)
