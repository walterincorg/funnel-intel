"""Answer selection strategies for funnel traversal."""

from __future__ import annotations


def get_default_strategy() -> dict:
    """Return a sensible default answer strategy."""
    return {
        "approach": "middle",
        "stop_at": ["paywall"],
        "max_steps": 250,
    }


def build_traversal_prompt(
    competitor_name: str,
    funnel_url: str,
    config: dict | None = None,
    available_files: list[str] | None = None,
) -> str:
    """Build the browser-use agent prompt for freeform funnel traversal."""
    cfg = config or get_default_strategy()
    stop_keywords = ", ".join(cfg.get("stop_at", ["paywall"]))
    max_steps = cfg.get("max_steps", 100)
    max_funnel_pages = cfg.get("max_funnel_pages") or cfg.get("max_pages")
    cap_rule = ""
    if max_funnel_pages:
        cap_rule = (
            f"\n7. TEST MODE: Capture at least {max_funnel_pages} distinct funnel screens/pages "
            "before stopping, unless you reach a pricing/paywall page first. Do NOT call done "
            f"with max_steps before {max_funnel_pages} funnel screens have been observed.\n"
        )

    files_block = ""
    if available_files:
        lines = "\n".join(f"- {p}" for p in available_files)
        files_block = f"""

AVAILABLE FILES:
You have these local files pre-provided by the operator. If the funnel asks
you to upload a file matching one of these (e.g. a palm/hand scan image for a
biometric gate), call the `upload_file` action with the EXACT path below. Do
not try to browse for other files or fabricate a path.
{lines}
"""

    return f"""You are traversing the marketing funnel for "{competitor_name}".

START URL: {funnel_url}

INSTRUCTIONS:
1. Navigate to the URL and begin the funnel/quiz flow.
2. At each step, observe what is shown: questions, answer options, informational screens, pricing, discounts.
3. Select answers using the "{cfg.get('approach', 'middle')}" approach:
   - "first": always pick the first option
   - "middle": pick the middle/most common option
   - "random": pick randomly
4. Continue through the funnel step by step.
5. You may freely fill in forms (name, email, phone, age, weight, etc.) with fake but plausible data to continue the funnel.
   - Email fields: always enter a fake address like jane.doe@example.com and submit — do NOT stop.
   - If a screen says "check your inbox" or "click the link we sent you": try clicking any "resend" or "skip" option first. If none exist, stop with stop_reason "email_verification".
6. STOP ONLY when you hit: {stop_keywords}
   - "paywall": a hard payment wall requiring real payment to proceed.
     Before stopping, scroll down and look for a "skip", "maybe later", or "no thanks" link — click it and keep going if it exists.
7. Safety ceiling: up to {max_steps} browser actions/LLM iterations if needed. Do NOT stop early for max_steps before reaching pricing/paywall, email verification, a true reset, or a real end screen.{cap_rule}

IMPORTANT BROWSING RULES:
- ALWAYS scroll down before clicking a button. The main action button (Continue, Next, Submit) is usually below the fold. Do NOT click buttons near the top of the page — those are often navigation/logo links that will reset the funnel.
- If the page suddenly goes back to the beginning of the funnel (e.g. you see the first question again, or the URL changes to a new visitor/session ID), STOP IMMEDIATELY. Report it as stop_reason "funnel_reset" in the summary. Do NOT restart the funnel — one pass is enough.
- CONSTANT-URL APPS: Some funnels keep the same URL while changing questions. Do NOT call that a reset just because the URL is unchanged. If the visible question/content changes and offers new answers such as Yup/Nope, Continue, or quiz options, keep going.
- After clicking, wait briefly for page transitions and animations to complete before acting on the next screen.
- POPUP/SIDEBAR/MODAL HANDLING: If a popup, sidebar, modal, cookie banner, or any overlay appears that's blocking the funnel content, CLOSE IT FIRST before trying to interact with the underlying form. Common close patterns: X button, "No thanks", "Skip", "Continue without", "Decline", or pressing Escape via send_keys.
- STUCK-LOOP RECOVERY: If you click the same element twice and the page does NOT advance, do NOT keep clicking the same element. Instead, in this order: (a) scroll to expose more elements, (b) wait 3 seconds and recheck, (c) try a SIBLING element with a similar role (e.g. another option button), (d) as a last resort, refresh the page by navigating to the current URL again. Never click the same broken element more than 2 times.
- MINI-GAME / TEST RECOVERY: Some funnels include optional reaction-time, memory, quiz, or game screens. Try the obvious interaction once or twice. If a mini-game stalls or loops, do NOT solve it indefinitely; call `bypass_mini_game` to click Skip/Continue/Next or a few safe tiles, then keep moving toward pricing.
- CONSENT SCREENS: If Continue is disabled because a consent/agreement checkbox is required, call `check_required_consent`. Never click Privacy Policy, Terms, Conditions, cookie, or legal links. If you accidentally reach a legal page, go back to the funnel and continue.
- NUMERIC INPUT SCREENS: Age, height, current weight, and goal weight screens are normal funnel steps, not resets. Prefer the `fill_numeric_screen` action. Use age 35, height 5 ft 9 in / 175 cm, current weight 180 lb / 82 kg, and goal weight 160 lb / 73 kg, then click Next/Continue.
- DEFAULT ACTION: On ordinary funnel screens, prefer calling `advance_funnel_step` first. It safely handles middle answer selection, Continue/Next buttons, email gates, numeric inputs, date screens, and consent checkboxes while avoiding legal links. Use lower-level actions only if `advance_funnel_step` reports an error.
- FALLBACK ACTIONS WHEN AN ELEMENT ISN'T INDEXED: If you can SEE an answer option, button, or input field on the screenshot but it's NOT in the indexed elements list:
  - For clickable items (answer options, buttons): use the `click_by_text` action with the visible text. Example: click_by_text(text="Mid-sized").
  - For form inputs (email, text fields): use the `fill_input` action with a CSS selector. Examples: fill_input(selector="input[type=email]", value="jane.doe@example.com"), fill_input(selector="input[name=age]", value="30").
  - You can also click via screen coordinates if the element is visible but unindexed.
  These fallbacks bypass the indexed element list entirely. Use them BEFORE giving up.
- ANSWER OPTIONS ARE VALID CLICK TARGETS: Funnels often need you to click an answer option (like "Yes", "Mid-sized", "Lose weight") which then auto-advances OR enables a Continue button. Both patterns are normal. Click the answer first; only then look for Continue.

PRICING PAGE — when you reach a pricing/checkout/subscription/plan-selection page:
This is the most important data we extract. When you see one, scroll if needed to
make every plan tile visible, then capture data and stop.

When you call the `done` action at the end of the run, the `text` field MUST be a
JSON string with this exact shape:

{{"step_type": "pricing", "url": "current page URL", "stop_reason": "paywall|end_of_funnel|max_steps|funnel_reset", "plans": [{{"name": "1-week plan", "price": "9.49", "currency": "USD", "period": "one-time", "features": ["Most Popular"]}}], "discounts": [{{"type": "percent_off", "amount": "50%", "original_price": "18.98", "discounted_price": "9.49", "conditions": "limited time"}}], "trial_info": {{"has_trial": false, "trial_days": null, "trial_price": null}}}}

Notes:
- Include every visible plan tile in the `plans` array.
- If there is no discount or no trial, use empty array / null values.
- If you stopped before reaching pricing (no pricing visible), still output JSON but with `plans: []` and the appropriate `stop_reason`.
- The `done.text` field is the ONLY place this JSON should appear. Do NOT print it
  earlier in your thinking, memory, or next_goal — only inside the final `done` call.

Then: scroll for any "skip" / "maybe later" / "no thanks" link and click it before
stopping; otherwise stop with stop_reason="paywall".
{files_block}"""


def build_guided_prompt(competitor_name: str, funnel_url: str,
                        baseline_steps: list[dict]) -> str:
    """Build a guided replay prompt using a baseline run."""
    steps_script = []
    for s in baseline_steps:
        q = s.get("question_text", "")
        action = s.get("action_taken", "")
        steps_script.append(f"  Step {s['step_number']}: expect '{q}' → {action}")

    script_text = "\n".join(steps_script)

    return f"""You are re-traversing the marketing funnel for "{competitor_name}".

START URL: {funnel_url}

You have a BASELINE of what to expect. Follow this script:
{script_text}

At each step:
1. Verify the page roughly matches the expected question/content.
2. If it matches: execute the prescribed action and move on.
3. If it's slightly different (reworded but same intent): execute the action, note the difference.
4. If it's completely different: report the drift and continue exploring freely.

CONSENT / LEGAL LINKS:
If a consent checkbox blocks Continue, use `check_required_consent`. Never click
Privacy Policy, Terms, Conditions, Cookie Policy, or legal links. If you land on a
legal page, go back to the funnel immediately and continue.

For each step write a brief observation in your `memory` and `next_goal` fields:
the question text you see, the option you picked, and any drift from the baseline.
Be concrete (e.g. "Step 8: 'How active are you?' — picked Moderate. Baseline expected
'How often do you exercise?' — minor wording drift.").

PRICING PAGE — when you reach a pricing/checkout/subscription/plan-selection page:
This is the most important data we extract. Scroll if needed to make every plan
tile visible.

When you call the `done` action at the end of the run, the `text` field MUST be a
JSON string with this exact shape (single-line, no markdown fences):

{{"step_type": "pricing", "url": "current page URL", "stop_reason": "paywall|end_of_funnel|max_steps|funnel_reset", "plans": [{{"name": "1-week plan", "price": "9.49", "currency": "USD", "period": "one-time", "features": ["Most Popular"]}}], "discounts": [{{"type": "percent_off", "amount": "50%", "original_price": "18.98", "discounted_price": "9.49", "conditions": "limited time"}}], "trial_info": {{"has_trial": false, "trial_days": null, "trial_price": null}}}}

Notes:
- Include every visible plan tile in the `plans` array.
- If there is no discount or no trial, use empty array / null values.
- If you stopped before reaching pricing (no pricing visible), still output JSON but with `plans: []` and the appropriate `stop_reason`.
- The `done.text` field is the ONLY place this JSON should appear. Do NOT print it
  earlier in your thinking, memory, or next_goal — only inside the final `done` call.

STOP CONDITIONS:
- Fill in any email/name/phone fields with fake data (e.g. jane.doe@example.com) and keep going.
- Numeric age, height, current weight, and goal weight screens are normal funnel steps; call `fill_numeric_screens` when standard input actions cannot fill them. Do not treat them as funnel resets.
- If Continue is disabled because a consent/agreement checkbox is required, call `check_required_consent`. Never click Privacy Policy, Terms, Conditions, cookie, or legal links. If you accidentally reach a legal page, go back to the funnel and continue.
- Stop only if you hit a hard payment wall with no skip option, or a genuine inbox-verification screen with no way around it.
- If the funnel extends beyond the baseline, keep going until you reach a natural end or a stop condition.
"""
