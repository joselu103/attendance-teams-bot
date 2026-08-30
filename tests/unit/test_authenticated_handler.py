import pytest
from pydantic import SecretStr

from attendance_teams_bot.agent.contracts import BotResponse
from attendance_teams_bot.teams.authenticated import (
    AttendanceApplicationHandler,
    BotServiceConnectivityHandler,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_bot_service_connectivity_handler_returns_a_safe_reply() -> None:
    handler = BotServiceConnectivityHandler()

    response = await handler.handle(message="Show my attendance")

    assert response.text == (
        "Microsoft Bot Service connectivity is verified, but Teams SSO, MCP, "
        "and LLM adapters are not configured yet."
    )


class FakeApplication:
    async def handle(self, *, message: str, mcp_access_token: SecretStr) -> BotResponse:
        assert message == "Show my attendance"
        assert mcp_access_token == SecretStr("mcp-token")
        return BotResponse(text="attendance reply")


@pytest.mark.anyio
async def test_attendance_handler_delegates_to_application() -> None:
    handler = AttendanceApplicationHandler(application=FakeApplication())

    response = await handler.handle(
        message="Show my attendance",
        mcp_access_token=SecretStr("mcp-token"),
    )

    assert response == BotResponse(text="attendance reply")
