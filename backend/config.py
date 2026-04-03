import os
import subprocess
from dotenv import load_dotenv

load_dotenv()

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# OpenClaw
OPENCLAW_PORT = os.getenv("OPENCLAW_PORT", "18789")
OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN", "")
OPENCLAW_ALERTS_CHANNEL = os.getenv("OPENCLAW_ALERTS_CHANNEL", "telegram")
OPENCLAW_ALERTS_TARGET = os.getenv("OPENCLAW_ALERTS_TARGET", "")

# Version
try:
    GIT_COMMIT = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
    ).decode().strip()
except Exception:
    GIT_COMMIT = "unknown"


def get_llm():
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    model = os.getenv("LLM_MODEL", "claude-opus-4-5")

    if provider == "anthropic":
        from browser_use.llm.anthropic.chat import ChatAnthropic
        return ChatAnthropic(
            model=model,
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        )

    if provider == "openai":
        from browser_use.llm.openai.chat import ChatOpenAI
        return ChatOpenAI(
            model=model,
            api_key=os.getenv("OPENAI_API_KEY"),
        )

    if provider == "ollama":
        from browser_use.llm.ollama.chat import ChatOllama
        return ChatOllama(model=model)

    raise ValueError(
        f"Unknown LLM_PROVIDER '{provider}'. Must be one of: anthropic, openai, ollama"
    )
