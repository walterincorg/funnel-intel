"""Answer selection strategies for funnel traversal."""

from __future__ import annotations


def get_default_strategy() -> dict:
    """Return a sensible default answer strategy."""
    return {
        "approach": "middle",
        "stop_at": ["email_verification", "paywall"],
        "max_steps": 100,
    }


def build_traversal_prompt(competitor_name: str, funnel_url: str, config: dict | None = None) -> str:
    """Build the browser-use agent prompt for freeform funnel traversal."""
    cfg = config or get_default_strategy()
    stop_keywords = ", ".join(cfg.get("stop_at", ["email_verification", "paywall"]))
    max_steps = cfg.get("max_steps", 100)

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
   - "email_verification": the funnel requires you to verify an email address (check inbox, click link, enter code)
   - "paywall": a payment form that requires real payment to proceed
   Do NOT stop for simple input fields — fill them in and keep going.
7. Maximum {max_steps} steps.

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
{{"summary": true, "total_steps": N, "stop_reason": "email_verification|paywall|end_of_funnel|max_steps"}}
"""


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

Output the same JSON format as a freeform traversal for each step, plus add:
{{"drift": "none|minor|major", "expected": "...", "actual": "..."}}

STOP at the same point as the baseline, or earlier if you hit a gate.
"""
