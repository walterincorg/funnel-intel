import logging
from composio import ComposioToolSet, Action
from backend.config import COMPOSIO_API_KEY, COMPOSIO_SLACK_CHANNEL

log = logging.getLogger(__name__)

DEFAULT_ENTITY = "default"


class ComposioService:
    def __init__(self):
        self.toolset = ComposioToolSet(
            api_key=COMPOSIO_API_KEY,
            entity_id=DEFAULT_ENTITY,
        )

    def get_connection_url(self, tool: str) -> str:
        """Returns Composio OAuth URL (hosted on composio.dev) for the user to authorize.

        No callback URL needed — Composio stores tokens server-side after the user
        completes OAuth on their hosted page. Connection is then available via entity_id.
        """
        entity = self.toolset.get_entity(DEFAULT_ENTITY)
        conn_req = entity.initiate_connection(app_name=tool)
        return conn_req.redirectUrl

    def get_connected_tools(self) -> list[str]:
        """Returns list of connected integration app names for the default entity."""
        try:
            entity = self.toolset.get_entity(DEFAULT_ENTITY)
            connections = entity.get_connections()
            return [c.appName for c in connections]
        except Exception:
            log.exception("Failed to fetch Composio connections")
            return []

    def send_slack_message(self, message: str) -> bool:
        """Send a Slack message via the connected Slack integration.

        Channel is configured via COMPOSIO_SLACK_CHANNEL env var (default: #general).

        Note: Action.SLACK_SENDS_A_MESSAGE_AS_A_BOT_TO_A_CHANNEL is the real
        Composio SDK enum name — their action names are verbose by design.
        Pre-flight check: run `composio actions list --app slack | grep SENDS_A_MESSAGE`
        to confirm the exact name for your installed SDK version before coding.
        """
        try:
            result = self.toolset.execute_action(
                action=Action.SLACK_SENDS_A_MESSAGE_AS_A_BOT_TO_A_CHANNEL,
                params={"channel": COMPOSIO_SLACK_CHANNEL, "text": message},
            )
            # Response field name varies by SDK version — log keys to debug if needed
            log.debug("Composio Slack response keys: %s", list(result.keys()))
            return result.get("successful", result.get("successfull", False))
        except Exception:
            log.exception("Composio Slack send failed — check COMPOSIO_API_KEY and Slack connection")
            return False


composio_service = ComposioService()
