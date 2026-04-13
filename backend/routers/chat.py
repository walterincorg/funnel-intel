import re
from typing import Literal

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
    OPENROUTER_MODEL_ADVANCED,
    OPENROUTER_MODEL_BASIC,
    OPENROUTER_MODEL_EXPERT,
    OPENROUTER_MODEL_GENIUS,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SYSTEM_PROMPT = (
    "You are Walter, an execution-focused operations copilot. "
    "Adapt response depth to task complexity and user intent. "
    "Rules:\n"
    "- For simple greetings or short social messages (e.g., 'hey', 'thanks'), respond in one short natural sentence with no headings.\n"
    "- For straightforward questions, use a concise direct answer with minimal structure.\n"
    "- For complex multi-step workflows, use plain text sections in this order:\n"
    "  Reasoning Track:\n"
    "  Plan:\n"
    "  Connections to Set Up:\n"
    "  Virtual Computer Recommendation:\n"
    "  Confirm Before I Proceed:\n"
    "- Only include Reasoning Track for complex tasks.\n"
    "- Never force a VM recommendation. Set VM Needed to Yes only when browser automation, scraping, website login flows, or long-running desktop/browser work is required.\n"
    "- If VM is not required, explicitly say Needed: No.\n"
    "- For email briefing, inbox summary, or send-email workflows, default to VM Needed: No and prioritize connecting Gmail via Composio.\n"
    "- If VM Needed is No, do not suggest creating a virtual computer.\n"
    "- Do not use markdown syntax like #, *, **, backticks, or code fences.\n"
    "- Do not output generic advice; be implementation-ready."
)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []
    model_preset: Literal["basic", "advanced", "expert", "genius"] = "advanced"


class ChatResponse(BaseModel):
    reply: str


def get_use_case_hint(user_message: str) -> str | None:
    message = user_message.lower()

    if (
        "linkedin lead" in message
        or "lead sourcing" in message
        or ("linkedin" in message and "hubspot" in message)
    ):
        return (
            "For LinkedIn lead sourcing, recommend these exact connections unless the user requests alternatives:\n"
            "- Apify LinkedIn API (source profiles/search results)\n"
            "- Hunter.io API (email enrichment)\n"
            "- HubSpot API (create/update contacts and pipeline)\n"
            "Virtual computer recommendation:\n"
            "- Needed: Yes\n"
            "- Why: browser logins, anti-bot friction, long-running scraping and enrichment workflows."
        )

    if (
        "email briefing" in message
        or "inbox briefing" in message
        or ("briefing" in message and "email" in message)
        or "summarize my emails" in message
    ):
        return (
            "For email briefing workflows, recommend these exact connections unless the user requests alternatives:\n"
            "- Gmail API (read inbox, thread context, and send summary email)\n"
            "Virtual computer recommendation:\n"
            "- Needed: No\n"
            "- Why: this can run through direct API access without browser automation."
        )

    return None


def is_simple_greeting(user_message: str) -> bool:
    text = user_message.strip().lower()
    return text in {"hey", "hi", "hello", "yo", "sup", "thanks", "thank you"}


def resolve_model_for_preset(model_preset: str) -> str:
    model_map = {
        "basic": OPENROUTER_MODEL_BASIC,
        "advanced": OPENROUTER_MODEL_ADVANCED,
        "expert": OPENROUTER_MODEL_EXPERT,
        "genius": OPENROUTER_MODEL_GENIUS,
    }
    return model_map.get(model_preset, OPENROUTER_MODEL)


def call_openrouter(messages: list[dict[str, str]], model: str) -> requests.Response:
    return requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://tasklet.ai/",
            "X-Title": "Walter",
        },
        json={
            "model": model,
            "messages": messages,
            "temperature": 0.5,
        },
        timeout=45,
    )


def sanitize_reply_text(reply: str) -> str:
    text = reply.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"`{1,3}", "", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@router.post("", response_model=ChatResponse)
def chat(payload: ChatRequest):
    if not OPENROUTER_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="OPENROUTER_API_KEY is not configured in server environment.",
        )

    if is_simple_greeting(payload.message):
        return ChatResponse(reply="Hey, how can I help?")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    use_case_hint = get_use_case_hint(payload.message)
    if use_case_hint:
        messages.append(
            {
                "role": "system",
                "content": f"Use-case specific guidance:\n{use_case_hint}",
            }
        )
    messages.extend(
        {"role": message.role, "content": message.content}
        for message in payload.history[-8:]
        if message.content.strip()
    )
    messages.append({"role": "user", "content": payload.message})

    selected_model = resolve_model_for_preset(payload.model_preset)

    try:
        response = call_openrouter(messages, selected_model)
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=f"OpenRouter request failed: {error}") from error

    if not response.ok and "No endpoints found for" in response.text:
        try:
            response = call_openrouter(messages, OPENROUTER_MODEL_ADVANCED)
        except requests.RequestException as error:
            raise HTTPException(status_code=502, detail=f"OpenRouter request failed: {error}") from error

    if not response.ok:
        raise HTTPException(status_code=502, detail=f"OpenRouter error: {response.text}")

    data = response.json()
    reply = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    if not reply:
        reply = "I am ready to help. Tell me the next task you want me to run."

    return ChatResponse(reply=sanitize_reply_text(reply))
