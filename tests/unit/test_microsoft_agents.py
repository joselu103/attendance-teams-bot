from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from pydantic import SecretStr

from attendance_teams_bot.agent.contracts import BotResponse
from attendance_teams_bot.settings import TeamsConnectionSettings
from attendance_teams_bot.teams.microsoft_agents import (
    TeamsAuthorizationSsoTokenProvider,
    create_attendance_teams_http_app,
    route_attendance_turn,
    route_authenticated_turn,
)


@dataclass
class FakeConversation:
    conversation_type: str | None


@dataclass
class FakeActivity:
    type: str
    text: str | None
    conversation: FakeConversation | None = None


@dataclass
class FakeTurnContext:
    activity: FakeActivity
    sent_texts: list[str] = field(default_factory=list)

    async def send_activity(self, text: str) -> None:
        self.sent_texts.append(text)


@dataclass
class RecordingAsyncHandler:
    messages: list[str] = field(default_factory=list)

    async def handle(self, *, message: str) -> BotResponse:
        self.messages.append(message)
        return BotResponse(text="safe reply")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_blank_authenticated_message_does_not_invoke_handler() -> None:
    handler = RecordingAsyncHandler()
    context = FakeTurnContext(activity=FakeActivity(type="message", text="  "))

    await route_authenticated_turn(context=context, handler=handler)

    assert handler.messages == []
    assert context.sent_texts == ["Please send a message so I can help."]


@pytest.mark.anyio
async def test_authenticated_message_routes_trimmed_text_and_sends_the_reply() -> None:
    handler = RecordingAsyncHandler()
    context = FakeTurnContext(activity=FakeActivity(type="message", text="  Hello  "))

    await route_authenticated_turn(context=context, handler=handler)

    assert handler.messages == ["Hello"]
    assert context.sent_texts == ["safe reply"]


@dataclass
class RecordingAttendanceHandler:
    received_token: SecretStr | None = None

    async def handle(self, *, message: str, mcp_access_token: SecretStr) -> BotResponse:
        assert message == "Show my attendance"
        self.received_token = mcp_access_token
        return BotResponse(text="safe reply")


class FakeSsoTokenProvider:
    async def get_token(self, context: FakeTurnContext) -> SecretStr:
        assert context.activity.text == "Show my attendance"
        return SecretStr("teams-token")


class FakeOboTokenExchange:
    async def exchange(self, user_assertion: SecretStr) -> SecretStr:
        assert user_assertion.get_secret_value() == "teams-token"
        return SecretStr("mcp-token")


def _teams_connection() -> TeamsConnectionSettings:
    return TeamsConnectionSettings(
        client_id=uuid4(),
        tenant_id=uuid4(),
        client_secret=SecretStr("test-client-secret"),
    )


@dataclass
class FakeAttendanceHandler:
    async def handle(self, *, message: str, mcp_access_token: SecretStr) -> BotResponse:
        del message, mcp_access_token
        return BotResponse(text="safe reply")


def test_attendance_factory_configures_required_teams_sso_handler(monkeypatch) -> None:
    from attendance_teams_bot.teams import microsoft_agents

    recorded: dict[str, object] = {}

    class FakeAuthHandler:
        def __init__(
            self,
            *,
            name: str,
            auth_type: str,
            abs_oauth_connection_name: str,
        ) -> None:
            recorded["auth_handler_name"] = name
            recorded["auth_type"] = auth_type
            recorded["oauth_connection_name"] = abs_oauth_connection_name

    class FakeAuthorization:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args
            recorded["authorization_auth_handlers"] = kwargs["auth_handlers"]

    class FakeAgentApplication:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def activity(self, _activity_type: str, *, auth_handlers: list[str]):
            recorded["route_auth_handlers"] = auth_handlers

            def decorate(function):
                return function

            return decorate

    class FakeConnectionManager:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def get_default_connection(self) -> FakeOboTokenExchange:
            return FakeOboTokenExchange()

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
        attendance_handler=FakeAttendanceHandler(),
        oauth_connection_name="attendance-teams-sso",
        delegated_scope="api://attendance-api/attendance.access",
    )

    assert recorded["auth_handler_name"] == "attendance-teams-sso"
    assert recorded["auth_type"] == "UserAuthorization"
    assert recorded["oauth_connection_name"] == "attendance-teams-sso"
    assert recorded["route_auth_handlers"] == ["attendance-teams-sso"]


@pytest.mark.anyio
async def test_attendance_turn_passes_only_the_obo_token_to_the_handler() -> None:
    context = FakeTurnContext(
        activity=FakeActivity(
            type="message",
            text="Show my attendance",
            conversation=FakeConversation("personal"),
        )
    )
    handler = RecordingAttendanceHandler()

    await route_attendance_turn(
        context=context,
        handler=handler,
        sso_token_provider=FakeSsoTokenProvider(),
        obo_token_exchange=FakeOboTokenExchange(),
    )

    assert handler.received_token == SecretStr("mcp-token")
    assert context.sent_texts == ["safe reply"]


@pytest.mark.anyio
@pytest.mark.parametrize("conversation_type", ["groupChat", "channel", None, "unknown"])
async def test_attendance_turn_refuses_nonpersonal_conversations_before_sso(
    conversation_type: str | None,
) -> None:
    class UnexpectedSso:
        async def get_token(self, context: FakeTurnContext) -> SecretStr:
            del context
            raise AssertionError("nonpersonal conversation must not request SSO")

    context = FakeTurnContext(
        activity=FakeActivity(
            type="message",
            text="Show my attendance",
            conversation=FakeConversation(conversation_type),
        )
    )

    await route_attendance_turn(
        context=context,
        handler=RecordingAttendanceHandler(),
        sso_token_provider=UnexpectedSso(),
        obo_token_exchange=FakeOboTokenExchange(),
    )

    assert context.sent_texts == ["Attendance is available only in a personal chat."]


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
        authorization=FakeAuthorization(),
        auth_handler_id="teams-sso",
    )

    token = await token_provider.get_token(
        FakeTurnContext(activity=FakeActivity(type="message", text="Show my attendance"))
    )

    assert token == SecretStr("teams-token")
