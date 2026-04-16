"""FastAPI middleware for request logging and correlation IDs."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from backend.logging_config import correlation_id

log = logging.getLogger("backend.http")

# Paths that generate noise and don't need per-request logging
_SKIP_PATHS = {"/api/health"}


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every HTTP request with method, path, status, and duration.

    Assigns a correlation ID to each request so that all log lines produced
    while handling the request can be traced together.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        token = correlation_id.set(req_id)

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            log.exception(
                "%s %s -> 500 (%.0fms)",
                request.method, request.url.path, duration_ms,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": 500,
                    "duration_ms": round(duration_ms, 1),
                },
            )
            raise
        finally:
            correlation_id.reset(token)

        duration_ms = (time.perf_counter() - start) * 1000

        if request.url.path not in _SKIP_PATHS:
            level = logging.WARNING if response.status_code >= 400 else logging.INFO
            log.log(
                level,
                "%s %s -> %d (%.0fms)",
                request.method, request.url.path, response.status_code, duration_ms,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round(duration_ms, 1),
                },
            )

        response.headers["X-Request-ID"] = req_id
        return response
