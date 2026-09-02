from secrets import token_urlsafe
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from attendance_teams_bot.settings import Settings
from attendance_teams_bot.teams import microsoft_agents
from attendance_teams_bot.teams.authenticated import BotServiceConnectivityHandler
from attendance_teams_bot.teams.microsoft_agents import (
    create_attendance_teams_http_app,
    create_authenticated_teams_http_app,
)


class _UnusedAttendanceHandler:
    async def handle(self, *, message: str, mcp_access_token):
        del message, mcp_access_token
        raise AssertionError("unsigned callback must not reach attendance handling")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_authenticated_endpoint_rejects_requests_without_bot_service_credentials(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET", token_urlsafe())
    connection = Settings().teams_connection
    assert connection is not None
    app = create_authenticated_teams_http_app(
        connection=connection,
        handler=BotServiceConnectivityHandler(),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        response = await client.post("/api/messages", json={"type": "message"})

    assert response.status_code == 401


@pytest.mark.anyio
async def test_authenticated_endpoint_records_a_safe_callback_lifecycle(monkeypatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    def record_event(_logger, _level: int, event: str, **fields: object) -> None:
        events.append((event, fields))

    monkeypatch.setattr(microsoft_agents, "log_event", record_event)
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET", token_urlsafe())
    connection = Settings().teams_connection
    assert connection is not None
    app = create_authenticated_teams_http_app(
        connection=connection,
        handler=BotServiceConnectivityHandler(),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        response = await client.post("/api/messages", json={"type": "message", "text": "secret"})

    assert response.status_code == 401
    assert len(events) == 1
    event, fields = events[0]
    assert event == "teams_callback_completed"
    assert fields["method"] == "POST"
    assert fields["path"] == "/api/messages"
    assert fields["status_code"] == 401
    assert isinstance(UUID(str(fields["correlation_id"])), UUID)
    assert isinstance(fields["duration_ms"], int)
    assert fields["duration_ms"] >= 0
    assert "secret" not in repr(events)


@pytest.mark.anyio
async def test_authenticated_endpoint_exposes_a_health_probe(monkeypatch) -> None:
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET", token_urlsafe())
    connection = Settings().teams_connection
    assert connection is not None
    app = create_authenticated_teams_http_app(
        connection=connection,
        handler=BotServiceConnectivityHandler(),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200


@pytest.mark.anyio
async def test_enabled_attendance_endpoint_exposes_health_and_rejects_unsigned_requests(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET", token_urlsafe())
    connection = Settings().teams_connection
    assert connection is not None
    app = create_attendance_teams_http_app(
        connection=connection,
        attendance_handler=_UnusedAttendanceHandler(),
        oauth_connection_name="attendance-teams-sso",
        delegated_scope="api://attendance-crmt/attendance.access",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        health = await client.get("/health")
        response = await client.post("/api/messages", json={"type": "message"})

    assert health.status_code == 200
    assert response.status_code == 401
