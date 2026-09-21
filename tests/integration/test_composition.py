from secrets import token_urlsafe
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from attendance_teams_bot.composition import (
    create_bot_service_only_app,
    create_http_app,
    create_local_http_app,
)
from attendance_teams_bot.settings import Settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_default_runtime_composes_the_safe_local_http_app() -> None:
    app = create_http_app(Settings())

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/messages",
            json={
                "type": "message",
                "id": "activity-456",
                "from": {"id": "local-user-456"},
                "text": "Hello",
            },
        )

    assert response.status_code == 200
    assert response.json()["text"] == (
        "Attendance Teams Bot is running locally, but authentication, MCP, "
        "and LLM adapters are not configured yet."
    )


@pytest.mark.anyio
async def test_teams_runtime_composes_the_bot_service_only_http_app(monkeypatch) -> None:
    monkeypatch.setenv("BOT_RUNTIME_MODE", "teams")
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET", token_urlsafe())
    app = create_http_app(Settings())

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        response = await client.post("/api/messages", json={"type": "message"})

    assert response.status_code == 401


def test_bot_service_only_composition_uses_its_handler(monkeypatch) -> None:
    from attendance_teams_bot import composition

    monkeypatch.setenv("BOT_RUNTIME_MODE", "teams")
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET", token_urlsafe())
    recorded: dict[str, object] = {}
    expected_app = object()

    def fake_connectivity_factory(**kwargs: object):
        recorded.update(kwargs)
        return expected_app

    monkeypatch.setattr(
        composition,
        "create_bot_service_only_callback_http_app",
        fake_connectivity_factory,
    )

    assert create_bot_service_only_app(Settings()) is expected_app
    assert isinstance(recorded["handler"], composition.BotServiceOnlyHandler)


def test_teams_mode_composes_enabled_attendance_integration(monkeypatch) -> None:
    from attendance_teams_bot import composition
    from attendance_teams_bot.agent.orchestrator import AttendanceAgent
    from attendance_teams_bot.mcp.session import StreamableHttpAttendanceSessionFactory

    monkeypatch.setenv("BOT_RUNTIME_MODE", "teams")
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET", token_urlsafe())
    monkeypatch.setenv("ATTENDANCE_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("MCP_ENDPOINT", "https://attendance.example.test/mcp")
    monkeypatch.setenv("MCP_SCOPE", "api://attendance-api/attendance.access")
    monkeypatch.setenv("TEAMS_SSO_OAUTH_CONNECTION_NAME", "attendance-teams-sso")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-openai-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5-mini")
    recorded: dict[str, object] = {}
    expected_app = object()
    expected_model = object()

    def fake_attendance_factory(**kwargs: object):
        recorded.update(kwargs)
        return expected_app

    monkeypatch.setattr(
        composition,
        "create_attendance_teams_http_app",
        fake_attendance_factory,
    )
    monkeypatch.setattr(
        composition,
        "create_openai_language_model",
        lambda *, api_key, model: (
            recorded.update(openai_api_key=api_key, openai_model=model) or expected_model
        ),
    )

    app = create_http_app(Settings())

    assert app is expected_app
    assert recorded["oauth_connection_name"] == "attendance-teams-sso"
    assert recorded["delegated_scope"] == "api://attendance-api/attendance.access"
    application = recorded["attendance_application"]
    assert isinstance(application, AttendanceAgent)
    assert application.language_model is expected_model
    assert isinstance(application.mcp_session_factory, StreamableHttpAttendanceSessionFactory)
    assert recorded["openai_model"] == "gpt-5-mini"


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
