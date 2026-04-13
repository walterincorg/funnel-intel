"""
Smoke tests for backend/routers/composio.py endpoints.

composio-core may not be installed — the conftest.py stubs the SDK so
tests run without the real package. The composio_service singleton is
patched per-test via monkeypatch.
"""
from unittest.mock import MagicMock, patch

# Import once at module level after conftest stubs composio
from fastapi.testclient import TestClient
from backend.main import app
import backend.routers.composio as composio_router

client = TestClient(app)


def test_status_returns_empty_list_when_no_tools(monkeypatch):
    """GET /api/composio/status returns 200 with empty connected_tools list."""
    mock_svc = MagicMock()
    mock_svc.get_connected_tools.return_value = []
    monkeypatch.setattr(composio_router, "composio_service", mock_svc)

    resp = client.get("/api/composio/status")

    assert resp.status_code == 200
    assert resp.json() == {"connected_tools": [], "entity_id": "default"}


def test_status_returns_connected_tools(monkeypatch):
    """GET /api/composio/status returns tools when Slack is connected."""
    mock_svc = MagicMock()
    mock_svc.get_connected_tools.return_value = ["slack"]
    monkeypatch.setattr(composio_router, "composio_service", mock_svc)

    resp = client.get("/api/composio/status")

    assert resp.status_code == 200
    assert "slack" in resp.json()["connected_tools"]


def test_connect_returns_redirect_url(monkeypatch):
    """POST /api/composio/connect returns redirect_url on success."""
    mock_svc = MagicMock()
    mock_svc.get_connection_url.return_value = "https://composio.dev/oauth/slack/abc123"
    monkeypatch.setattr(composio_router, "composio_service", mock_svc)

    resp = client.post("/api/composio/connect", json={"tool": "slack"})

    assert resp.status_code == 200
    data = resp.json()
    assert "redirect_url" in data
    assert data["tool"] == "slack"


def test_connect_returns_500_on_sdk_error(monkeypatch):
    """POST /api/composio/connect returns 500 when Composio SDK raises."""
    mock_svc = MagicMock()
    mock_svc.get_connection_url.side_effect = Exception("SDK error")
    monkeypatch.setattr(composio_router, "composio_service", mock_svc)

    resp = client.post("/api/composio/connect", json={"tool": "slack"})

    assert resp.status_code == 500


def test_connect_defaults_to_slack(monkeypatch):
    """POST /api/composio/connect with empty body defaults to tool='slack'."""
    mock_svc = MagicMock()
    mock_svc.get_connection_url.return_value = "https://composio.dev/oauth/slack/abc"
    monkeypatch.setattr(composio_router, "composio_service", mock_svc)

    resp = client.post("/api/composio/connect", json={})

    assert resp.status_code == 200
    assert resp.json()["tool"] == "slack"
