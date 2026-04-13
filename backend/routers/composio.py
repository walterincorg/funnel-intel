import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.services.composio_service import composio_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/composio", tags=["composio"])


class ConnectRequest(BaseModel):
    tool: str = "slack"


@router.post("/connect")
def initiate_connection(req: ConnectRequest):
    """Start Composio OAuth flow. Returns redirect URL (hosted on composio.dev).

    Frontend should redirect the user to redirect_url. No callback needed —
    Composio handles token storage after the user completes OAuth.
    """
    try:
        url = composio_service.get_connection_url(req.tool)
        return {"redirect_url": url, "tool": req.tool}
    except Exception:
        log.exception("Failed to initiate Composio connection for tool: %s", req.tool)
        raise HTTPException(status_code=500, detail="Failed to initiate Composio connection")


@router.get("/status")
def connection_status():
    """List connected tools for the default entity.

    Returns: {"connected_tools": ["slack"], "entity_id": "default"}
    An empty list means no tools connected yet (or Composio API is unreachable).
    """
    tools = composio_service.get_connected_tools()
    return {"connected_tools": tools, "entity_id": "default"}
