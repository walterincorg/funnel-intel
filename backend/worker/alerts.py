import logging
import requests
from backend.config import (
    OPENCLAW_PORT,
    OPENCLAW_TOKEN,
    OPENCLAW_ALERTS_CHANNEL,
    OPENCLAW_ALERTS_TARGET,
)

log = logging.getLogger(__name__)


def send_alert(message: str) -> bool:
    """Send an alert to the configured OpenClaw topic. Returns True on success."""
    if not OPENCLAW_TOKEN or not OPENCLAW_ALERTS_TARGET:
        log.warning("OpenClaw alerts not configured, skipping: %s", message)
        return False

    try:
        resp = requests.post(
            f"http://127.0.0.1:{OPENCLAW_PORT}/tools/invoke",
            headers={
                "Authorization": f"Bearer {OPENCLAW_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "tool": "message",
                "action": "send",
                "args": {
                    "channel": OPENCLAW_ALERTS_CHANNEL,
                    "target": OPENCLAW_ALERTS_TARGET,
                    "message": message,
                },
            },
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json()
        if not result.get("ok"):
            log.error("OpenClaw returned error: %s", result.get("error"))
            return False
        return True
    except Exception:
        log.exception("Failed to send OpenClaw alert")
        return False
