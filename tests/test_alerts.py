"""
Regression and unit tests for backend/worker/alerts.py.

REGRESSION: alerts.py was fully refactored to add Composio Slack delivery
alongside the existing OpenClaw path. These tests ensure OpenClaw still works.
"""
from unittest.mock import MagicMock, patch


def test_openclaw_path_fires_when_configured(monkeypatch):
    """REGRESSION: OpenClaw delivery must work after the alerts.py refactor."""
    import backend.worker.alerts as mod

    monkeypatch.setattr(mod, "_composio", None)  # disable Composio path

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"ok": True}

    with patch("backend.worker.alerts.requests.post", return_value=mock_resp), \
         patch("backend.worker.alerts.OPENCLAW_TOKEN", "test-token"), \
         patch("backend.worker.alerts.OPENCLAW_ALERTS_TARGET", "test-target"), \
         patch("backend.worker.alerts.OPENCLAW_PORT", "18789"), \
         patch("backend.worker.alerts.OPENCLAW_ALERTS_CHANNEL", "telegram"):
        result = mod.send_alert("test alert")

    assert result is True


def test_openclaw_returns_false_when_not_configured(monkeypatch):
    """send_alert returns False when OpenClaw env vars are missing."""
    import backend.worker.alerts as mod

    monkeypatch.setattr(mod, "_composio", None)

    with patch("backend.worker.alerts.OPENCLAW_TOKEN", ""), \
         patch("backend.worker.alerts.OPENCLAW_ALERTS_TARGET", ""):
        result = mod.send_alert("test alert")

    assert result is False


def test_try_composio_slack_noop_when_composio_none(monkeypatch):
    """_try_composio_slack must be a no-op when _composio is None."""
    import backend.worker.alerts as mod

    monkeypatch.setattr(mod, "_composio", None)
    mod._try_composio_slack("test message")  # must not raise


def test_try_composio_slack_noop_when_slack_not_connected(monkeypatch):
    """_try_composio_slack must be a no-op when Slack is not in connected tools."""
    import backend.worker.alerts as mod

    mock_composio = MagicMock()
    mock_composio.get_connected_tools.return_value = ["gmail", "notion"]
    monkeypatch.setattr(mod, "_composio", mock_composio)

    mod._try_composio_slack("test message")

    mock_composio.send_slack_message.assert_not_called()


def test_try_composio_slack_sends_when_connected(monkeypatch):
    """_try_composio_slack calls send_slack_message when Slack is connected."""
    import backend.worker.alerts as mod

    mock_composio = MagicMock()
    mock_composio.get_connected_tools.return_value = ["slack"]
    mock_composio.send_slack_message.return_value = True
    monkeypatch.setattr(mod, "_composio", mock_composio)

    mod._try_composio_slack("test message")

    mock_composio.send_slack_message.assert_called_once_with("test message")


def test_send_alert_returns_openclaw_result_even_when_composio_fails(monkeypatch):
    """send_alert return value is based on OpenClaw, not Composio."""
    import backend.worker.alerts as mod

    mock_composio = MagicMock()
    mock_composio.get_connected_tools.side_effect = Exception("composio down")
    monkeypatch.setattr(mod, "_composio", mock_composio)

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"ok": True}

    with patch("backend.worker.alerts.requests.post", return_value=mock_resp), \
         patch("backend.worker.alerts.OPENCLAW_TOKEN", "tok"), \
         patch("backend.worker.alerts.OPENCLAW_ALERTS_TARGET", "tgt"), \
         patch("backend.worker.alerts.OPENCLAW_PORT", "18789"), \
         patch("backend.worker.alerts.OPENCLAW_ALERTS_CHANNEL", "telegram"):
        result = mod.send_alert("alert when composio explodes")

    assert result is True  # OpenClaw succeeded; Composio failure is swallowed
