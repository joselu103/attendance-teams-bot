from secrets import token_urlsafe
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from attendance_teams_bot.composition import create_http_app, create_local_http_app
from attendance_teams_bot.settings import RuntimeSettings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_default_runtime_composes_the_safe_local_http_app() -> None:
    app = create_http_app(RuntimeSettings())

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
async def test_teams_runtime_composes_the_authenticated_http_app(monkeypatch) -> None:
    monkeypatch.setenv("BOT_RUNTIME_MODE", "teams")
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET", token_urlsafe())
    app = create_http_app(RuntimeSettings())

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        response = await client.post("/api/messages", json={"type": "message"})

    assert response.status_code == 401


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
