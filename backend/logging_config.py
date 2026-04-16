"""Centralized logging configuration for Funnel Intel.

Call `setup_logging()` once at application startup (main.py). All modules
that use `log = logging.getLogger(__name__)` will inherit this config.

Features:
- Structured JSON output in production (LOG_FORMAT=json)
- Human-readable colored output in development (LOG_FORMAT=text, default)
- Rotating file handler when LOG_FILE is set
- Per-module log levels via LOG_LEVELS env var
- Request correlation IDs via contextvars
- Noisy third-party loggers automatically quieted
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from contextvars import ContextVar

# ---------------------------------------------------------------------------
# Correlation ID (set per-request in middleware, propagated to all log lines)
# ---------------------------------------------------------------------------

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


# ---------------------------------------------------------------------------
# JSON Formatter
# ---------------------------------------------------------------------------

class JSONFormatter(logging.Formatter):
    """Outputs one JSON object per log line — machine-parseable for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%03dZ"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "correlation_id": correlation_id.get("-"),
        }

        # Attach extra structured fields (set via log.info("msg", extra={...}))
        for key in ("competitor_id", "run_id", "job_id", "worker_id",
                     "duration_ms", "method", "path", "status_code",
                     "ad_count", "signal_count", "step_count"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val

        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


# ---------------------------------------------------------------------------
# Human-readable formatter (development)
# ---------------------------------------------------------------------------

TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)-30s [%(correlation_id)s] %(message)s"


class TextFormatter(logging.Formatter):
    """Adds correlation_id to the standard text format."""

    def format(self, record: logging.LogRecord) -> str:
        record.correlation_id = correlation_id.get("-")
        return super().format(record)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

# Third-party loggers that produce excessive output at INFO level
_NOISY_LOGGERS = [
    "httpx",
    "httpcore",
    "urllib3",
    "urllib3.connectionpool",
    "hpack",
    "asyncio",
    "watchfiles",
    "multipart",
    "browser_use",
    "langchain",
    "langchain_core",
    "langchain_anthropic",
    "openai",
    "anthropic",
    "supabase",
    "postgrest",
    "gotrue",
    "realtime",
    "storage3",
    "uvicorn.access",
]


def setup_logging() -> None:
    """Configure the root logger. Call once at startup."""

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_format = os.getenv("LOG_FORMAT", "text").lower()  # "json" or "text"
    log_file = os.getenv("LOG_FILE", "")  # e.g. "/var/log/funnel-intel/app.log"

    root = logging.getLogger()
    root.setLevel(log_level)

    # Remove any existing handlers (prevents duplicate lines on reload)
    root.handlers.clear()

    # Choose formatter
    if log_format == "json":
        formatter = JSONFormatter()
    else:
        formatter = TextFormatter(TEXT_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")

    # Console handler (always present)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # File handler (optional, with rotation)
    if log_file:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=50 * 1024 * 1024,  # 50 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Quiet noisy third-party loggers
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Per-module overrides: LOG_LEVELS="backend.worker.traversal=DEBUG,backend.routers=DEBUG"
    custom_levels = os.getenv("LOG_LEVELS", "")
    if custom_levels:
        for pair in custom_levels.split(","):
            pair = pair.strip()
            if "=" in pair:
                mod, lvl = pair.split("=", 1)
                logging.getLogger(mod.strip()).setLevel(lvl.strip().upper())

    logging.getLogger(__name__).info(
        "Logging configured: level=%s format=%s file=%s",
        log_level, log_format, log_file or "(stdout only)",
    )
