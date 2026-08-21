from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from attendance_teams_bot.agent.contracts import BotResponse
from attendance_teams_bot.teams.adapter import TeamsActivityAdapter
from attendance_teams_bot.teams.http import create_teams_http_app


class RecordingHandler:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def handle(self, *, user_id: str, message: str) -> BotResponse:
        self.calls.append((user_id, message))
        return BotResponse(text="Your attendance summary is ready.")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_messages_endpoint_routes_a_message_and_returns_a_teams_text_reply() -> None:
    handler = RecordingHandler()
    app = create_teams_http_app(TeamsActivityAdapter(handler=handler))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/messages",
            json={
                "type": "message",
                "id": "activity-123",
                "text": "Show my attendance for yesterday",
                "from": {"id": "teams-user-123"},
                "conversation": {"id": "conversation-123"},
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "type": "message",
        "text": "Your attendance summary is ready.",
        "replyToId": "activity-123",
    }
    assert handler.calls == [("teams-user-123", "Show my attendance for yesterday")]


@pytest.mark.anyio
async def test_messages_endpoint_returns_an_empty_success_response_for_non_message_activities() -> (
    None
):
    handler = RecordingHandler()
    app = create_teams_http_app(TeamsActivityAdapter(handler=handler))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/messages", json={"type": "conversationUpdate"})

    assert response.status_code == 204
    assert response.content == b""
    assert handler.calls == []


@pytest.mark.anyio
async def test_messages_endpoint_returns_a_safe_teams_reply_for_an_incomplete_message() -> None:
    handler = RecordingHandler()
    app = create_teams_http_app(TeamsActivityAdapter(handler=handler))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/messages", json={"type": "message", "text": "Hello"})

    assert response.status_code == 200
    assert response.json() == {
        "type": "message",
        "text": "Please send a text message so I can help.",
    }
    assert handler.calls == []
