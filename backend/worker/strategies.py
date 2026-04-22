"""Answer selection strategies for funnel traversal."""

from __future__ import annotations


def get_default_strategy() -> dict:
    """Return a sensible default answer strategy."""
    return {
        "approach": "middle",
        "stop_at": ["email_verification", "paywall"],
        "max_steps": 100,
    }


def build_traversal_prompt(
    competitor_name: str,
    funnel_url: str,
    config: dict | None = None,
    available_files: list[str] | None = None,
) -> str:
    """Build the natural-language instruction passed to Stagehand's
    autonomous agent during a recording run. Wording still references
    "output a JSON object" per step — Stagehand's agent history captures
    those messages, and we also reconstruct structured steps from its
    action trajectory regardless.
    """
    cfg = config or get_default_strategy()
    stop_keywords = ", ".join(cfg.get("stop_at", ["paywall"]))
    max_steps = cfg.get("max_steps", 100)

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
5. You may freely fill in forms (name, email, phone, etc.) with fake data to continue the funnel.
6. STOP ONLY when you hit one of these hard blockers: {stop_keywords}
   - "paywall": a payment form that requires real payment to proceed
   Do NOT stop for email fields — fill them in with fake data (e.g. test@example.com) and keep going.
7. Maximum {max_steps} steps.

IMPORTANT BROWSING RULES:
- ALWAYS scroll down before clicking a button. The main action button (Continue, Next, Submit) is usually below the fold. Do NOT click buttons near the top of the page — those are often navigation/logo links that will reset the funnel.
- If the page suddenly goes back to the beginning of the funnel (e.g. you see the first question again, or the URL changes to a new visitor/session ID), STOP IMMEDIATELY. Report it as stop_reason "funnel_reset" in the summary. Do NOT restart the funnel — one pass is enough.
- After clicking, wait briefly for page transitions and animations to complete before acting on the next screen.

For EACH step, output a JSON object on its own line:
{{"step_number": N, "step_type": "question|info|input|pricing|discount", "question_text": "...", "answer_options": [{{"label": "...", "value": "..."}}], "action_taken": "clicked X", "url": "current URL", "log": "short human-readable summary of what happened and why"}}

The "log" field is IMPORTANT — write it like a person casually commenting on what they see. Examples:
- "Landed on age selection. Four options, picked 30-39 as the middle choice."
- "Asked about fitness goals — went with Lose Weight since it's the most common."
- "Hit a pricing page! Three plans: Basic $9/mo, Pro $19/mo, Premium $39/mo."
- "Email verification required — need to check inbox. Stopping here."

If you see a PRICING page, output:
{{"step_number": N, "step_type": "pricing", "plans": [{{"name": "...", "price": "...", "currency": "...", "period": "...", "features": ["..."]}}], "discounts": [{{"type": "...", "amount": "...", "original_price": "...", "discounted_price": "...", "conditions": "..."}}], "trial_info": {{"has_trial": true/false, "trial_days": N, "trial_price": "..."}}, "url": "current URL", "log": "..."}}

After the last step, output a summary line:
{{"summary": true, "total_steps": N, "stop_reason": "paywall|funnel_reset|end_of_funnel|max_steps"}}
{files_block}"""


# NOTE: `build_guided_prompt` was removed when traversal switched from
# browser-use to Stagehand. Guided replay is now handled deterministically by
# the recipe/replay system in `stagehand_driver.run_replay`, not by a prompt.
