import os
import subprocess
from dotenv import load_dotenv

load_dotenv()

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "funnel-screenshots")

# Composio
COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY", "")
COMPOSIO_SLACK_CHANNEL = os.getenv("COMPOSIO_SLACK_CHANNEL", "#general")

# OpenClaw
OPENCLAW_PORT = os.getenv("OPENCLAW_PORT", "18789")
OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN", "")
OPENCLAW_ALERTS_CHANNEL = os.getenv("OPENCLAW_ALERTS_CHANNEL", "telegram")
OPENCLAW_ALERTS_TARGET = os.getenv("OPENCLAW_TELEGRAM_TARGET", "")

# Telegram (direct, if needed outside OpenClaw)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_THREAD_ID = os.getenv("TELEGRAM_THREAD_ID", "")

# OpenRouter chat
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_MODEL_BASIC = os.getenv("OPENROUTER_MODEL_BASIC", "anthropic/claude-3.5-haiku")
OPENROUTER_MODEL_ADVANCED = os.getenv("OPENROUTER_MODEL_ADVANCED", "anthropic/claude-3.7-sonnet")
OPENROUTER_MODEL_EXPERT = os.getenv("OPENROUTER_MODEL_EXPERT", "anthropic/claude-opus-4")
OPENROUTER_MODEL_GENIUS = os.getenv("OPENROUTER_MODEL_GENIUS", "anthropic/claude-opus-4")

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
