"""Shared config used by the standalone `agent.py` CLI.

Stagehand configures its own LLM from `LLM_PROVIDER` / `LLM_MODEL` /
`ANTHROPIC_API_KEY` env vars inside `backend/worker/stagehand_driver.py`, so
this module is intentionally tiny now.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def model_name() -> str:
    """Return the Stagehand-style `provider/model` identifier."""
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    model = os.getenv("LLM_MODEL", "claude-opus-4-5")
    return model if "/" in model else f"{provider}/{model}"
