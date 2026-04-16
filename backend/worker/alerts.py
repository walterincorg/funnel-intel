"""Send alerts to a Telegram group via the OpenClaw CLI.

We shell out to `openclaw message send` because the gateway's HTTP
`/tools/invoke` tool names are not stable in 2026.3.23, while the CLI
is. Failures are logged and swallowed so alerts never break the worker.
"""

import logging
import os
import subprocess
import time

from backend.config import OPENCLAW_ALERTS_CHANNEL, OPENCLAW_ALERTS_TARGET

log = logging.getLogger(__name__)

OPENCLAW_BIN = os.getenv("OPENCLAW_BIN", "openclaw")
OPENCLAW_CONFIG_PATH = os.getenv("OPENCLAW_CONFIG_PATH", "")


def send_alert(message: str) -> bool:
    """Send an alert to the configured OpenClaw channel. Returns True on success."""
    if not OPENCLAW_ALERTS_TARGET:
        log.warning("OpenClaw alerts target not configured, skipping: %s", message)
        return False

    env = os.environ.copy()
    if OPENCLAW_CONFIG_PATH:
        env["OPENCLAW_CONFIG_PATH"] = OPENCLAW_CONFIG_PATH

    try:
        result = subprocess.run(
            [
                OPENCLAW_BIN,
                "message",
                "send",
                "--channel",
                OPENCLAW_ALERTS_CHANNEL,
                "--target",
                OPENCLAW_ALERTS_TARGET,
                "-m",
                message,
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        log.exception("openclaw CLI invocation failed")
        return False

    if result.returncode != 0:
        log.error(
            "openclaw CLI returned %d: %s",
            result.returncode,
            (result.stderr or result.stdout).strip(),
        )
        return False

    log.debug("Alert sent successfully via %s", OPENCLAW_ALERTS_CHANNEL)
    return True
