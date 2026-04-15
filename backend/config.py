import os
import subprocess
from dotenv import load_dotenv

load_dotenv()

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "funnel-screenshots")

# OpenClaw
OPENCLAW_PORT = os.getenv("OPENCLAW_PORT", "18789")
OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN", "")
OPENCLAW_ALERTS_CHANNEL = os.getenv("OPENCLAW_ALERTS_CHANNEL", "telegram")
OPENCLAW_ALERTS_TARGET = os.getenv("OPENCLAW_TELEGRAM_TARGET", "")

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


def get_llm():
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    model = os.getenv("LLM_MODEL", "claude-opus-4-5")

    if provider == "anthropic":
        from browser_use.llm.anthropic.chat import ChatAnthropic
        return ChatAnthropic(
            model=model,
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            temperature=0,
            max_tokens=16384,
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
