from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from pydantic import SecretStr
from starlette.responses import Response

from attendance_teams_bot.agent.contracts import BotResponse
from attendance_teams_bot.settings import TeamsConnectionSettings
from attendance_teams_bot.teams import microsoft_agents
from attendance_teams_bot.teams.microsoft_agents import (
    TeamsAuthorizationSsoTokenProvider,
    create_attendance_teams_http_app,
    normalize_oauth_invoke_response,
    route_authenticated_turn,
)


@dataclass
class FakeActivity:
    type: str
    text: str | None


@dataclass
class FakeTurnContext:
    activity: FakeActivity
    sent_texts: list[str] = field(default_factory=list)

    async def send_activity(self, text: str) -> None:
        self.sent_texts.append(text)


@dataclass
class RecordingHandler:
    messages: list[str] = field(default_factory=list)

    async def handle(self, *, message: str) -> BotResponse:
        self.messages.append(message)
        return BotResponse(text="safe reply")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_connectivity_route_handles_blank_and_trimmed_messages() -> None:
    handler = RecordingHandler()
    blank = FakeTurnContext(FakeActivity(type="message", text="  "))
    message = FakeTurnContext(FakeActivity(type="message", text="  Hello  "))

    await route_authenticated_turn(context=blank, handler=handler)
    await route_authenticated_turn(context=message, handler=handler)

    assert blank.sent_texts == ["Please send a message so I can help."]
    assert handler.messages == ["Hello"]
    assert message.sent_texts == ["safe reply"]


class FakeTokenResponse:
    token = "teams-token"


class FakeAuthorization:
    async def get_token(self, context: FakeTurnContext, auth_handler_id: str) -> FakeTokenResponse:
        assert context.activity.text == "Show my attendance"
        assert auth_handler_id == "teams-sso"
        return FakeTokenResponse()


@pytest.mark.anyio
async def test_sso_provider_reads_a_token_from_the_sdk_authorization_boundary() -> None:
    token_provider = TeamsAuthorizationSsoTokenProvider(
        authorization=FakeAuthorization(), auth_handler_id="teams-sso"
    )
    token = await token_provider.get_token(
        FakeTurnContext(FakeActivity(type="message", text="Show my attendance"))
    )
    assert token == SecretStr("teams-token")


def _teams_connection() -> TeamsConnectionSettings:
    return TeamsConnectionSettings(
        client_id=uuid4(), tenant_id=uuid4(), client_secret=SecretStr("test-client-secret")
    )


@dataclass
class FakeApplication:
    async def handle(self, *, message: str, mcp_access_token: SecretStr) -> BotResponse:
        del message, mcp_access_token
        return BotResponse(text="safe reply")


def test_attendance_factory_configures_sso_and_forwards_callback_to_turn_handler(
    monkeypatch,
) -> None:
    recorded: dict[str, object] = {}

    class FakeAuthHandler:
        def __init__(self, *, name: str, auth_type: str, abs_oauth_connection_name: str) -> None:
            recorded.update(name=name, auth_type=auth_type, connection=abs_oauth_connection_name)

    class FakeAuthorization:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args
            recorded["auth_handlers"] = kwargs["auth_handlers"]

    class FakeAgentApplication:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def activity(self, activity_type: str, *, auth_handlers: list[str]):
            recorded["activity_type"] = activity_type
            recorded["route_auth_handlers"] = auth_handlers
            return lambda function: function

    class FakeConnectionManager:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def get_default_connection(self) -> object:
            return object()

        def get_default_connection_configuration(self) -> dict[str, object]:
            return {}

    class FakeAdapter:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

    monkeypatch.setattr(microsoft_agents, "AuthHandler", FakeAuthHandler)
    monkeypatch.setattr(microsoft_agents, "Authorization", FakeAuthorization)
    monkeypatch.setattr(microsoft_agents, "AgentApplication", FakeAgentApplication)
    monkeypatch.setattr(microsoft_agents, "MsalConnectionManager", FakeConnectionManager)
    monkeypatch.setattr(microsoft_agents, "CloudAdapter", FakeAdapter)

    create_attendance_teams_http_app(
        connection=_teams_connection(),
        attendance_application=FakeApplication(),
        oauth_connection_name="attendance-teams-sso",
        delegated_scope="api://attendance-api/attendance.access",
    )

    assert recorded["name"] == "attendance-teams-sso"
    assert recorded["auth_type"] == "UserAuthorization"
    assert recorded["connection"] == "attendance-teams-sso"
    assert recorded["route_auth_handlers"] == ["attendance-teams-sso"]


def test_failed_teams_sso_token_exchange_requests_interactive_sign_in() -> None:
    response = normalize_oauth_invoke_response(
        activity={"type": "invoke", "name": "signin/tokenExchange", "value": {"id": "id"}},
        response=Response(status_code=501),
        oauth_connection_name="attendance-teams-sso",
    )
    assert response is not None
    assert response.status_code == 412


def test_unmatched_teams_sso_token_exchange_501_is_preserved() -> None:
    response = Response(status_code=501)
    normalized = normalize_oauth_invoke_response(
        activity={"type": "invoke", "name": "signin/tokenExchange", "value": {}},
        response=response,
        oauth_connection_name="attendance-teams-sso",
    )
    assert normalized is response
