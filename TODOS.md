# TODOS

## Composio Integration

### [AUTH] Add authentication to Composio endpoints
**What:** Add auth to `POST /api/composio/connect` and `GET /api/composio/status` in `backend/routers/composio.py`.

**Why:** Without auth, anyone who can reach the VPS can trigger OAuth flows for arbitrary tools or inspect which tools are connected under `entity_id="default"`.

**Pros:** Prevents unauthorized Composio connections; aligns with what the parent platform's user auth layer will need.

**Cons:** Requires auth middleware on a codebase that currently has none — non-trivial scope; needs design decision from parent platform.

**Context:** The entire existing API (scans, competitors, pricing, compare) is also unauthenticated. This is a systemic gap to fix holistically when the parent platform introduces user auth. A simple `COMPOSIO_ADMIN_TOKEN` header check in the Composio router is a viable interim fix.

**Depends on:** Parent platform user auth design. Consider at the same time as adding auth to all other routers.

**Where to start:** `backend/routers/composio.py` — add a FastAPI `Depends()` on an auth function that checks `Authorization: Bearer <COMPOSIO_ADMIN_TOKEN>`.
