from secrets import token_urlsafe
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from attendance_teams_bot.teams.authenticated import BotServiceConnectivityHandler
from attendance_teams_bot.teams.microsoft_agents import create_authenticated_teams_http_app


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
    app = create_authenticated_teams_http_app(handler=BotServiceConnectivityHandler())

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        response = await client.post("/api/messages", json={"type": "message"})

    assert response.status_code == 401
