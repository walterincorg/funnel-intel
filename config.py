import os
from dotenv import load_dotenv

load_dotenv()


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
