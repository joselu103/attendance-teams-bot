from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from attendance_teams_bot.agent.language_model import ModelRequest, ToolCall, ToolDefinition
from attendance_teams_bot.agent.orchestrator import AttendanceAgent
from attendance_teams_bot.auth.obo import DelegatedAuthenticationUnavailable
from attendance_teams_bot.mcp.contracts import (
    SELF_ATTENDANCE_TOOL,
    AttendanceEvent,
    AttendanceEventPage,
)
from attendance_teams_bot.teams.authenticated import AttendanceApplicationHandler
from attendance_teams_bot.teams.microsoft_agents import route_attendance_turn


@dataclass
class FakeConversation:
    conversation_type: str = "personal"


@dataclass
class FakeActivity:
    type: str
    text: str | None
    conversation: FakeConversation = field(default_factory=FakeConversation)


@dataclass
class FakeTurnContext:
    activity: FakeActivity
    sent_texts: list[str] = field(default_factory=list)

    async def send_activity(self, text: str) -> None:
        self.sent_texts.append(text)


@dataclass
class FakeSsoTokenProvider:
    token: SecretStr = field(default_factory=lambda: SecretStr("token-a"))
    calls: int = 0

    async def get_token(self, context: object) -> SecretStr:
        del context
        self.calls += 1
        return self.token


@dataclass
class RecordingOboTokenExchange:
    assertions: list[SecretStr] = field(default_factory=list)

    async def exchange(self, user_assertion: SecretStr) -> SecretStr:
        self.assertions.append(user_assertion)
        return SecretStr("token-b")


@dataclass
class RecordingLanguageModel:
    requests: list[ModelRequest] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> ToolCall:
        self.requests.append(request)
        return ToolCall(
            id="call-1",
            name=SELF_ATTENDANCE_TOOL,
            arguments={"start_date": "2026-08-10", "end_date": "2026-08-12"},
        )


@dataclass
class RecordingMcpSession:
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def list_tools(self) -> tuple[ToolDefinition, ...]:
        return (
            ToolDefinition(
                name=SELF_ATTENDANCE_TOOL,
                description="Remote metadata must never enter the model prompt.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "format": "date"},
                        "end_date": {"type": "string", "format": "date"},
                        "limit": {"type": "integer"},
                        "offset": {"type": "integer"},
                    },
                    "required": ["start_date", "end_date"],
                    "additionalProperties": False,
                },
                annotations={"readOnlyHint": True},
            ),
        )

    async def call_tool(self, *, name: str, arguments: dict[str, object]) -> AttendanceEventPage:
        self.calls.append((name, arguments))
        return AttendanceEventPage(
            items=(
                AttendanceEvent(
                    attendance_event_id=13,
                    employee_id=7,
                    punch_type="Office",
                    location="Company",
                    checked_in_at=datetime(2026, 8, 10, 8, tzinfo=UTC),
                    checked_out_at=datetime(2026, 8, 10, 16, tzinfo=UTC),
                    note=None,
                ),
            ),
            limit=50,
            offset=0,
            next_offset=None,
        )


@dataclass
class RecordingMcpSessionFactory:
    session: RecordingMcpSession
    tokens: list[SecretStr] = field(default_factory=list)
    correlation_ids: list[UUID] = field(default_factory=list)

    @asynccontextmanager
    async def open(self, *, access_token: SecretStr, correlation_id: UUID):
        self.tokens.append(access_token)
        self.correlation_ids.append(correlation_id)
        yield self.session


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def build_handler(
    model: RecordingLanguageModel, session_factory: RecordingMcpSessionFactory
) -> AttendanceApplicationHandler:
    return AttendanceApplicationHandler(
        AttendanceAgent(
            language_model=model,
            mcp_session_factory=session_factory,
            correlation_id_factory=uuid4,
            reference_date_factory=lambda: date(2026, 8, 15),
        )
    )


@pytest.mark.anyio
async def test_authenticated_attendance_flow_exchanges_token_a_and_sends_only_token_b_to_mcp() -> (
    None
):
    model = RecordingLanguageModel()
    session = RecordingMcpSession()
    session_factory = RecordingMcpSessionFactory(session)
    obo = RecordingOboTokenExchange()
    context = FakeTurnContext(
        activity=FakeActivity(
            type="message",
            text="Show my attendance from 2026-08-10 to 2026-08-12",
        )
    )

    await route_attendance_turn(
        context=context,
        handler=build_handler(model, session_factory),
        sso_token_provider=FakeSsoTokenProvider(),
        obo_token_exchange=obo,
    )

    assert obo.assertions == [SecretStr("token-a")]
    assert session_factory.tokens == [SecretStr("token-b")]
    assert session.calls == [
        (
            SELF_ATTENDANCE_TOOL,
            {"start_date": "2026-08-10", "end_date": "2026-08-12", "limit": 50, "offset": 0},
        )
    ]
    assert len(model.requests) == 1
    assert "token-a" not in repr(model.requests)
    assert "token-b" not in repr(model.requests)
    assert "Remote metadata" not in repr(model.requests)
    assert "token-a" not in context.sent_texts[0]
    assert "token-b" not in context.sent_texts[0]
    assert "13" not in context.sent_texts[0]
    assert "7" not in context.sent_texts[0]


@pytest.mark.anyio
@pytest.mark.parametrize("conversation_type", ["groupChat", "channel", "meeting", "unknown"])
async def test_non_personal_attendance_turn_stops_before_sso_obo_model_and_mcp(
    conversation_type: str,
) -> None:
    model = RecordingLanguageModel()
    session_factory = RecordingMcpSessionFactory(RecordingMcpSession())
    sso = FakeSsoTokenProvider()
    obo = RecordingOboTokenExchange()
    context = FakeTurnContext(
        activity=FakeActivity(
            type="message",
            text="Show my attendance",
            conversation=FakeConversation(conversation_type),
        )
    )

    await route_attendance_turn(
        context=context,
        handler=build_handler(model, session_factory),
        sso_token_provider=sso,
        obo_token_exchange=obo,
    )

    assert context.sent_texts == ["Attendance is available only in a personal chat."]
    assert sso.calls == 0
    assert obo.assertions == []
    assert model.requests == []
    assert session_factory.tokens == []


@dataclass
class FailingOboTokenExchange:
    async def exchange(self, user_assertion: SecretStr) -> SecretStr:
        del user_assertion
        raise DelegatedAuthenticationUnavailable


@pytest.mark.anyio
async def test_obo_failure_returns_a_safe_authentication_reply() -> None:
    model = RecordingLanguageModel()
    session_factory = RecordingMcpSessionFactory(RecordingMcpSession())
    context = FakeTurnContext(activity=FakeActivity(type="message", text="Show my attendance"))

    await route_attendance_turn(
        context=context,
        handler=build_handler(model, session_factory),
        sso_token_provider=FakeSsoTokenProvider(),
        obo_token_exchange=FailingOboTokenExchange(),
    )

    assert context.sent_texts == [
        "Authentication is temporarily unavailable. Please try again later."
    ]
    assert model.requests == []
    assert session_factory.tokens == []
