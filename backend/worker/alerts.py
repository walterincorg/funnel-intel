import logging
import requests
from backend.config import (
    OPENCLAW_PORT,
    OPENCLAW_TOKEN,
    OPENCLAW_ALERTS_CHANNEL,
    OPENCLAW_ALERTS_TARGET,
)

log = logging.getLogger(__name__)

# Alert delivery
#  ┌──────────────────────────────────────────────────────┐
#  │ send_alert(message)                                  │
#  │   ├─ _send_openclaw()       [primary, blocking]      │
#  │   └─ _try_composio_slack()  [best-effort, additive]  │
#  └──────────────────────────────────────────────────────┘

# Eager import with graceful fallback — fails visibly at startup if misconfigured,
# but doesn't break the worker if Composio is not yet set up.
try:
    from backend.services.composio_service import composio_service as _composio
except Exception as _e:
    log.warning("Composio service unavailable: %s — Slack alerts disabled", _e)
    _composio = None


def send_alert(message: str) -> bool:
    """Send alert via OpenClaw (primary) and Composio Slack (if connected, best-effort)."""
    openclaw_ok = _send_openclaw(message)
    _try_composio_slack(message)
    return openclaw_ok


def _send_openclaw(message: str) -> bool:
    """Send alert to the configured OpenClaw topic. Returns True on success."""
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


def _try_composio_slack(message: str) -> None:
    """Best-effort Slack delivery via Composio. Fails silently — OpenClaw is primary."""
    if _composio is None:
        return
    try:
        if "slack" not in _composio.get_connected_tools():
            return
        ok = _composio.send_slack_message(message)
        if not ok:
            log.warning("Composio Slack delivery failed for alert: %s", message[:80])
            # TODO: wire to Sentry/monitoring when available
    except Exception:
        log.exception("Composio Slack delivery error")
