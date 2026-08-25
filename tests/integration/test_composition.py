import pytest
from httpx import ASGITransport, AsyncClient

from attendance_teams_bot.composition import create_local_http_app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_local_http_app_composes_the_safe_handler() -> None:
    app = create_local_http_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/messages",
            json={
                "type": "message",
                "id": "activity-123",
                "from": {"id": "teams-user-123"},
                "text": "Show my attendance",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "type": "message",
        "text": (
            "Attendance Teams Bot is running locally, but authentication, MCP, "
            "and LLM adapters are not configured yet."
        ),
        "replyToId": "activity-123",
    }
