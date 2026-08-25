import pytest

from attendance_teams_bot.teams.authenticated import BotServiceConnectivityHandler


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
