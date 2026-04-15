import requests
import time
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


def composio_request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json: dict | None = None,
    timeout: int = 30,
    retries: int = 2,
    retry_statuses: set[int] | None = None,
):
    retry_statuses = retry_statuses or set()

    for attempt in range(retries + 1):
        try:
            response = requests.request(
                method=method,
                url=f"{COMPOSIO_BASE_URL}{path}",
                headers=composio_headers(),
                params=params,
                json=json,
                timeout=timeout,
            )
        except requests.RequestException as error:
            if attempt >= retries:
                raise HTTPException(status_code=502, detail=f"Composio request failed: {error}") from error
            time.sleep(0.4 * (2**attempt))
            continue

        if response.ok:
            return response

        should_retry = response.status_code >= 500 or response.status_code in retry_statuses
        if should_retry and attempt < retries:
            time.sleep(0.4 * (2**attempt))
            continue

        raise HTTPException(
            status_code=502,
            detail=f"Composio error ({response.status_code}): {response.text}",
        )

    raise HTTPException(status_code=502, detail="Composio request failed after retries.")


@router.get("/toolkits")
def list_toolkits(search: str = ""):
    params = {
        "limit": 50,
        "sort_by": "alphabetically",
        "include_deprecated": "false",
    }
    if search.strip():
        params["search"] = search.strip()

    response = composio_request("GET", "/toolkits", params=params)

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
    configs_response = composio_request(
        "GET",
        "/auth_configs",
        params={
            "toolkit_slug": payload.toolkit_slug,
            "show_disabled": "false",
            "is_composio_managed": "true",
            "limit": 20,
        },
    )

    configs = configs_response.json().get("items", [])
    auth_config_id = configs[0]["id"] if configs else None

    if not auth_config_id:
        # Auto-create a Composio-managed auth config when one does not exist yet.
        create_response = composio_request(
            "POST",
            "/auth_configs",
            json={
                "toolkit": {"slug": payload.toolkit_slug},
                "options": {
                    "type": "use_composio_managed_auth",
                    "name": f"{payload.toolkit_slug} (Walter)",
                },
            },
            retry_statuses={409},
        )

        created = create_response.json()
        auth_config_id = created.get("id") or created.get("auth_config", {}).get("id")

    if not auth_config_id:
        raise HTTPException(status_code=502, detail="Composio did not return an auth config id.")

    # If the user is already connected, short-circuit without re-opening OAuth.
    active_response = composio_request(
        "GET",
        "/connected_accounts",
        params={
            "toolkit_slugs": payload.toolkit_slug,
            "user_ids": payload.user_id,
            "statuses": "ACTIVE",
            "limit": 1,
        },
    )
    active_items = active_response.json().get("items", [])
    if active_items:
        return {
            "redirect_url": None,
            "connected_account_id": active_items[0].get("id"),
            "already_connected": True,
        }

    link_response = composio_request(
        "POST",
        "/connected_accounts/link",
        json={
            "auth_config_id": auth_config_id,
            "user_id": payload.user_id,
            "callback_url": payload.callback_url,
            "long_redirect_url": True,
        },
        retry_statuses={404, 409, 422},
    )

    link_payload = link_response.json()
    return {
        "redirect_url": link_payload.get("redirect_url"),
        "connected_account_id": link_payload.get("connected_account_id"),
        "already_connected": False,
    }


@router.get("/connection-status")
def connection_status(toolkit_slug: str, user_id: str):
    response = composio_request(
        "GET",
        "/connected_accounts",
        params={
            "toolkit_slugs": toolkit_slug,
            "user_ids": user_id,
            "statuses": "ACTIVE",
            "limit": 20,
        },
    )

    payload = response.json()
    items = payload.get("items", [])
    return {
        "connected": len(items) > 0,
        "count": len(items),
    }
