"""Answer selection strategies for funnel traversal."""

from __future__ import annotations


def get_default_strategy() -> dict:
    """Return a sensible default answer strategy."""
    return {
        "approach": "middle",
        "stop_at": ["paywall"],
        "max_steps": 100,
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

IMPORTANT — OUTPUT FORMAT:
For EACH step, output a JSON object on its own line with the ACTUAL question
and answer options visible on the page (not the baseline values). Include a
`drift` field so we can tell how closely the live step matches the script:

{{"step_number": N, "step_type": "question|info|input|pricing|discount", "question_text": "...", "answer_options": [{{"label": "...", "value": "..."}}], "action_taken": "clicked X", "url": "current URL", "drift": "none|minor|major", "expected": "baseline question text", "actual": "what you actually saw", "log": "short human-readable summary"}}

The "log" field is IMPORTANT — a casual one-line human comment on what you saw
and why you picked what you did.

If you hit a PRICING page, output:
{{"step_number": N, "step_type": "pricing", "plans": [{{"name": "...", "price": "...", "currency": "...", "period": "...", "features": ["..."]}}], "discounts": [{{"type": "...", "amount": "...", "original_price": "...", "discounted_price": "...", "conditions": "..."}}], "trial_info": {{"has_trial": true/false, "trial_days": N, "trial_price": "..."}}, "url": "current URL", "drift": "none|minor|major", "log": "..."}}

After the last step, output a summary line:
{{"summary": true, "total_steps": N, "stop_reason": "paywall|funnel_reset|end_of_funnel|max_steps"}}

STOP CONDITIONS (same as freeform):
- Fill in any email/name/phone fields with fake data (e.g. jane.doe@example.com) and keep going.
- Stop only if you hit a hard payment wall with no skip option (stop_reason "paywall"), or a genuine inbox-verification screen with no way around it (stop_reason "email_verification").
- If the funnel extends beyond the baseline, keep going until you reach a natural end or a stop condition.
"""


def build_single_step_patch_prompt(
    recorded_intent: str | None,
    recorded_question_text: str | None = None,
    recorded_target_text: str | None = None,
    recorded_input_value: str | None = None,
    current_url: str | None = None,
) -> str:
    """Prompt for a 1-step browser-use Agent rescuing a scripted replay.

    The agent sees the live page mid-funnel. The scripted replay engine already
    failed to match the recorded selector, so we tell the agent exactly what
    intent we were trying to execute and ask it to complete that one step —
    nothing more. The replay engine will resume scripted playback from the
    next step.
    """
    hint_lines = []
    if recorded_question_text:
        hint_lines.append(f"Expected question on this page: \"{recorded_question_text}\"")
    if recorded_target_text:
        hint_lines.append(f"Expected to click something labelled: \"{recorded_target_text}\"")
    if recorded_input_value is not None:
        hint_lines.append(f"Expected to fill a field with: \"{recorded_input_value}\"")
    if recorded_intent:
        hint_lines.append(f"Recorded intent: {recorded_intent}")
    if current_url:
        hint_lines.append(f"Current URL: {current_url}")

    hints = "\n".join(f"- {line}" for line in hint_lines) or "- (no recorded hints)"

    return f"""You are repairing ONE step of a recorded funnel replay for a competitor analysis tool.

A deterministic Playwright script was walking a saved funnel recording and
failed to locate the expected element on the current page. Your ENTIRE job is
to complete this single page correctly, then stop. Do NOT advance past this
page — another system will take over.

WHAT THE RECORDING EXPECTED:
{hints}

RULES:
1. Look at the current page. Figure out which element matches the recorded
   intent (the page likely changed slightly — relabelled button, new option,
   reordered choices).
2. Perform exactly ONE action (click OR fill) that progresses the funnel past
   this page. Prefer matching the semantic intent over the literal text.
3. After that action completes, stop. Do not explore, do not fill follow-up
   screens, do not click through to the next page's submit button.
4. When you stop, write a memory line of the form:
     Step {{N}}: '<actual question text>' - clicked '<label>'     OR
     Step {{N}}: '<actual question text>' - entered '<value>'
   Use real quoted strings so the replay engine can parse what you did.
5. If the page is fundamentally the wrong page (funnel reset, paywall,
   verification screen), report that in your memory instead of forcing an
   action.
"""
