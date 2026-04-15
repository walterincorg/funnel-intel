import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import COMPOSIO_API_KEY

router = APIRouter(prefix="/api/composio", tags=["composio"])

COMPOSIO_BASE_URL = "https://backend.composio.dev/api/v3.1"


class ConnectRequest(BaseModel):
    toolkit_slug: str
    user_id: str
    callback_url: str


def composio_headers() -> dict[str, str]:
    if not COMPOSIO_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="COMPOSIO_API_KEY is not configured in backend environment.",
        )
    return {
        "x-api-key": COMPOSIO_API_KEY,
        "Content-Type": "application/json",
    }


@router.get("/toolkits")
def list_toolkits(search: str = ""):
    params = {
        "limit": 50,
        "sort_by": "alphabetically",
        "include_deprecated": "false",
    }
    if search.strip():
        params["search"] = search.strip()

    try:
        response = requests.get(
            f"{COMPOSIO_BASE_URL}/toolkits",
            headers=composio_headers(),
            params=params,
            timeout=30,
        )
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=f"Composio request failed: {error}") from error

    if not response.ok:
        raise HTTPException(status_code=502, detail=f"Composio error: {response.text}")

    payload = response.json()
    return {
        "items": [
            {
                "slug": item.get("slug", ""),
                "name": item.get("name", ""),
                "logo": item.get("logo"),
                "description": item.get("description") or item.get("app_url") or "",
            }
            for item in payload.get("items", [])
        ]
    }


@router.post("/connect")
def connect_toolkit(payload: ConnectRequest):
    try:
        configs_response = requests.get(
            f"{COMPOSIO_BASE_URL}/auth_configs",
            headers=composio_headers(),
            params={
                "toolkit_slug": payload.toolkit_slug,
                "show_disabled": "false",
                "is_composio_managed": "true",
                "limit": 20,
            },
            timeout=30,
        )
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=f"Composio request failed: {error}") from error

    if not configs_response.ok:
        raise HTTPException(status_code=502, detail=f"Composio error: {configs_response.text}")

    configs = configs_response.json().get("items", [])
    auth_config_id = configs[0]["id"] if configs else None

    if not auth_config_id:
        # Auto-create a Composio-managed auth config when one does not exist yet.
        try:
            create_response = requests.post(
                f"{COMPOSIO_BASE_URL}/auth_configs",
                headers=composio_headers(),
                json={
                    "toolkit": {"slug": payload.toolkit_slug},
                    "options": {
                        "type": "use_composio_managed_auth",
                        "name": f"{payload.toolkit_slug} (Walter)",
                    },
                },
                timeout=30,
            )
        except requests.RequestException as error:
            raise HTTPException(status_code=502, detail=f"Composio request failed: {error}") from error

        if not create_response.ok:
            raise HTTPException(status_code=502, detail=f"Composio error: {create_response.text}")

        created = create_response.json()
        auth_config_id = created.get("id") or created.get("auth_config", {}).get("id")

    if not auth_config_id:
        raise HTTPException(status_code=502, detail="Composio did not return an auth config id.")

    try:
        link_response = requests.post(
            f"{COMPOSIO_BASE_URL}/connected_accounts/link",
            headers=composio_headers(),
            json={
                "auth_config_id": auth_config_id,
                "user_id": payload.user_id,
                "callback_url": payload.callback_url,
            },
            timeout=30,
        )
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=f"Composio request failed: {error}") from error

    if not link_response.ok:
        raise HTTPException(status_code=502, detail=f"Composio error: {link_response.text}")

    link_payload = link_response.json()
    return {
        "redirect_url": link_payload.get("redirect_url"),
        "connected_account_id": link_payload.get("connected_account_id"),
    }


@router.get("/connection-status")
def connection_status(toolkit_slug: str, user_id: str):
    try:
        response = requests.get(
            f"{COMPOSIO_BASE_URL}/connected_accounts",
            headers=composio_headers(),
            params={
                "toolkit_slugs": toolkit_slug,
                "user_ids": user_id,
                "statuses": "ACTIVE",
                "limit": 20,
            },
            timeout=30,
        )
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=f"Composio request failed: {error}") from error

    if not response.ok:
        raise HTTPException(status_code=502, detail=f"Composio error: {response.text}")

    payload = response.json()
    items = payload.get("items", [])
    return {
        "connected": len(items) > 0,
        "count": len(items),
    }
