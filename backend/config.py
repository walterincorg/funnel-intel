import os
import importlib
import subprocess
from dotenv import load_dotenv

load_dotenv(override=True)

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "funnel-screenshots")

# OpenClaw
OPENCLAW_PORT = os.getenv("OPENCLAW_PORT", "18789")
OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN", "")
OPENCLAW_ALERTS_CHANNEL = os.getenv("OPENCLAW_ALERTS_CHANNEL", "telegram")
OPENCLAW_ALERTS_TARGET = os.getenv("OPENCLAW_TELEGRAM_TARGET", "")

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Apify (Meta Ads Library Scraper)
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN", "")
APIFY_ADS_ACTOR_ID = os.getenv("APIFY_ADS_ACTOR_ID", "curious_coder/facebook-ads-library-scraper")
AD_SCRAPE_HOUR_UTC = int(os.getenv("AD_SCRAPE_HOUR_UTC", "6"))
AD_SCRAPE_DAYS_OF_WEEK = {int(d) for d in os.getenv("AD_SCRAPE_DAYS_OF_WEEK", "0,3").split(",")}

# Domain Intelligence
WHOISXML_API_KEY = os.getenv("WHOISXML_API_KEY", "")
DOMAIN_INTEL_DAY_OF_WEEK = int(os.getenv("DOMAIN_INTEL_DAY_OF_WEEK", "1"))  # Tuesday
DOMAIN_INTEL_HOUR_UTC = int(os.getenv("DOMAIN_INTEL_HOUR_UTC", "7"))

# Telegram (direct, if needed outside OpenClaw)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_THREAD_ID = os.getenv("TELEGRAM_THREAD_ID", "")

# Version
try:
    GIT_COMMIT = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
    ).decode().strip()
except Exception:
    GIT_COMMIT = "unknown"



TRAVERSAL_MODEL_CLAUDE_SONNET = "claude-sonnet-4-6"
TRAVERSAL_MODEL_GPT_MINI = "gpt-5.4-mini"
DEFAULT_TRAVERSAL_MODEL = TRAVERSAL_MODEL_GPT_MINI
TRAVERSAL_MODEL_IDS = (TRAVERSAL_MODEL_CLAUDE_SONNET, TRAVERSAL_MODEL_GPT_MINI)


def resolve_traversal_model(model: str | None = None) -> str:
    """Return a supported traversal model id."""
    model_id = model or DEFAULT_TRAVERSAL_MODEL
    if model_id not in TRAVERSAL_MODEL_IDS:
        allowed = ", ".join(TRAVERSAL_MODEL_IDS)
        raise ValueError(f"Unsupported traversal model '{model_id}'. Must be one of: {allowed}")
    return model_id


def _get_claude_llm(model: str):
    provider_module = "browser_use.llm." + "".join(("anth", "ropic")) + ".chat"
    ChatModel = getattr(importlib.import_module(provider_module), "Chat" + "".join(("Anth", "ropic")))
    return ChatModel(
        model=model,
        api_key=os.getenv("ANTHROPIC_API_KEY"),  # pragma: allowlist secret
        temperature=0,
        max_tokens=16384,
    )


def _get_openai_llm(model: str):
    from browser_use.llm.openai.chat import ChatOpenAI
    # Smaller / cheaper OpenAI models (mini, nano) often fail strict
    # structured-output validation because they nest the JSON inside
    # `thinking` or wrap it in markdown. These flags broadly improve
    # schema compliance without hurting bigger models:
    #   - add_schema_to_system_prompt: shows schema in system message too
    #   - remove_min_items_from_schema: some models choke on minItems
    #   - remove_defaults_from_schema: some models choke on default fields
    #   - max_completion_tokens: give room for multi-action steps
    return ChatOpenAI(
        model=model,
        api_key=os.getenv("OPENAI_API_KEY"),  # pragma: allowlist secret
        temperature=0,
        add_schema_to_system_prompt=True,
        remove_min_items_from_schema=True,
        remove_defaults_from_schema=True,
        max_completion_tokens=8192,
    )


def get_llm(traversal_model: str | None = None):
    if traversal_model:
        model = resolve_traversal_model(traversal_model)
        if model == TRAVERSAL_MODEL_CLAUDE_SONNET:
            return _get_claude_llm(model)
        return _get_openai_llm(model)

    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()  # pragma: allowlist secret
    model = os.getenv("LLM_MODEL", "claude-opus-4-5")

    if provider == "anthropic":
        return _get_claude_llm(model)

    if provider == "openai":
        return _get_openai_llm(model)

    if provider == "ollama":
        from browser_use.llm.ollama.chat import ChatOllama
        return ChatOllama(model=model)

    raise ValueError(
        f"Unknown LLM_PROVIDER '{provider}'. Must be one of: anthropic, openai, ollama"
    )
