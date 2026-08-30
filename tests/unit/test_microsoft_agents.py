from dataclasses import dataclass, field

import pytest
from pydantic import SecretStr

from attendance_teams_bot.agent.contracts import BotResponse
from attendance_teams_bot.teams.microsoft_agents import (
    TeamsAuthorizationSsoTokenProvider,
    route_attendance_turn,
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


@pytest.mark.anyio
async def test_attendance_turn_passes_only_the_obo_token_to_the_handler() -> None:
    context = FakeTurnContext(activity=FakeActivity(type="message", text="Show my attendance"))
    handler = RecordingAttendanceHandler()

    await route_attendance_turn(
        context=context,
        handler=handler,
        sso_token_provider=FakeSsoTokenProvider(),
        obo_token_exchange=FakeOboTokenExchange(),
    )

    assert handler.received_token == SecretStr("mcp-token")
    assert context.sent_texts == ["safe reply"]


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
