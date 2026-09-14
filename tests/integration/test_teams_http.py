from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import attendance_teams_bot.observability as observability
from attendance_teams_bot.agent.contracts import BotResponse
from attendance_teams_bot.teams.adapter import TeamsActivityAdapter
from attendance_teams_bot.teams.http import create_teams_http_app


class RecordingHandler:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def handle(self, *, user_id: str, message: str) -> BotResponse:
        self.calls.append((user_id, message))
        return BotResponse(text="Your attendance summary is ready.")


class FailingHandler:
    def handle(self, *, user_id: str, message: str) -> BotResponse:
        del user_id, message
        raise RuntimeError("authorization: Bearer must-not-be-logged")


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


@pytest.mark.anyio
async def test_local_endpoint_emits_correlated_lifecycle_events_without_request_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[int, str, dict[str, object]]] = []

    def record_event(_logger, level: int, event: str, **fields: object) -> None:
        events.append((level, event, fields))

    trace_id = "11111111-1111-1111-1111-111111111111"
    monkeypatch.setattr(observability, "log_event", record_event)
    app = create_teams_http_app(TeamsActivityAdapter(handler=RecordingHandler()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/messages",
            headers={
                "X-Correlation-ID": trace_id,
                "Authorization": "Bearer should-not-appear",
            },
            json={"type": "message", "id": "raw-activity-id", "text": "private text"},
        )

    assert response.status_code == 200
    assert [(level, event) for level, event, _ in events] == [
        (20, "request_received"),
        (20, "request_completed"),
    ]
    for _, _, fields in events:
        assert fields["trace_id"] == trace_id
        assert fields["route"] == "/api/messages"
        assert fields["method"] == "POST"
    assert events[0][2]["state"] == "received"
    assert events[1][2]["state"] == "completed"
    assert events[1][2]["status_code"] == 200
    assert isinstance(events[1][2]["duration_ms"], int)
    assert "should-not-appear" not in repr(events)
    assert "raw-activity-id" not in repr(events)
    assert "private text" not in repr(events)


@pytest.mark.anyio
async def test_malformed_local_payload_uses_a_fresh_context_and_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    def record_event(_logger, _level: int, event: str, **fields: object) -> None:
        events.append((event, fields))

    monkeypatch.setattr(observability, "log_event", record_event)
    app = create_teams_http_app(TeamsActivityAdapter(handler=RecordingHandler()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        malformed = await client.post(
            "/api/messages",
            headers={"X-Correlation-ID": "not-a-uuid"},
            content=b"not-json",
        )
        normal = await client.post("/api/messages", json={"type": "conversationUpdate"})

    assert malformed.status_code == 200
    assert normal.status_code == 204
    received_trace_ids = [
        fields["trace_id"] for event, fields in events if event == "request_received"
    ]
    assert len(received_trace_ids) == 2
    assert received_trace_ids[0] != received_trace_ids[1]


@pytest.mark.anyio
async def test_unhandled_local_failure_is_logged_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[int, str, dict[str, object]]] = []

    def record_event(_logger, level: int, event: str, **fields: object) -> None:
        events.append((level, event, fields))

    monkeypatch.setattr(observability, "log_event", record_event)
    app = create_teams_http_app(TeamsActivityAdapter(handler=FailingHandler()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="must-not-be-logged"):
            await client.post(
                "/api/messages",
                json={"type": "message", "from": {"id": "user"}, "text": "private"},
            )

    assert [(level, event) for level, event, _ in events] == [
        (20, "request_received"),
        (40, "request_failed"),
    ]
    failure = events[1][2]
    assert failure["status_code"] == 500
    assert failure["state"] == "server_error"
    assert failure["error_type"] == "RuntimeError"
    assert "must-not-be-logged" not in repr(events)
