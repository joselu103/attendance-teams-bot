from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from attendance_teams_bot.agent.language_model import FinalResponse, ModelRequest
from attendance_teams_bot.agent.mcp_catalog import DiscoveredMcpTool
from attendance_teams_bot.agent.orchestrator import AttendanceAgent
from attendance_teams_bot.agent.rendering import UNAVAILABLE_REPLY
from attendance_teams_bot.mcp.contracts import (
    SELF_ATTENDANCE_TOOL,
    AttendanceEvent,
    AttendanceEventPage,
)
from attendance_teams_bot.teams.authenticated import SsoOboAttendanceTurnHandler


@dataclass
class Conversation:
    conversation_type: str = "personal"


@dataclass
class Activity:
    type: str = "message"
    text: str | None = "Show my attendance from 2026-08-10 to 2026-08-12"
    conversation: Conversation = field(default_factory=Conversation)
    from_property: object | None = None


@dataclass
class Context:
    activity: Activity = field(default_factory=Activity)
    sent: list[str] = field(default_factory=list)

    async def send_activity(self, text: str) -> None:
        self.sent.append(text)


@dataclass
class Sso:
    async def get_token(self, context: Context) -> SecretStr:
        del context
        return SecretStr("token-a")


@dataclass
class Obo:
    assertions: list[SecretStr] = field(default_factory=list)

    async def exchange(self, user_assertion: SecretStr) -> SecretStr:
        self.assertions.append(user_assertion)
        return SecretStr("token-b")


@dataclass
class GreetingModel:
    requests: list[ModelRequest] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> FinalResponse:
        self.requests.append(request)
        return FinalResponse("Hello!", "en")


@dataclass
class Session:
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def list_tools(self) -> tuple[DiscoveredMcpTool, ...]:
        return (
            DiscoveredMcpTool(
                name=SELF_ATTENDANCE_TOOL,
                description="metadata",
                input_schema={
                    "type": "object",
                    "properties": {
                        "start_date": {},
                        "end_date": {},
                        "limit": {},
                        "offset": {},
                    },
                    "additionalProperties": False,
                },
                annotations={"readOnlyHint": True},
            ),
        )

    async def call_tool(self, *, name: str, arguments: dict[str, object]) -> AttendanceEventPage:
        self.calls.append((name, arguments))
        assert name == SELF_ATTENDANCE_TOOL
        assert arguments == {
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "limit": 50,
            "offset": 0,
        }
        return AttendanceEventPage(
            items=(
                AttendanceEvent(
                    13,
                    7,
                    "Office",
                    "Company",
                    datetime(2026, 8, 10, 8, tzinfo=UTC),
                    datetime(2026, 8, 10, 16, tzinfo=UTC),
                    None,
                ),
            ),
            limit=50,
            offset=0,
            next_offset=None,
        )


@dataclass
class Factory:
    tokens: list[SecretStr] = field(default_factory=list)
    correlation_ids: list[UUID] = field(default_factory=list)
    session: Session = field(default_factory=Session)

    @asynccontextmanager
    async def open(self, *, access_token: SecretStr, correlation_id: UUID):
        self.tokens.append(access_token)
        self.correlation_ids.append(correlation_id)
        yield self.session


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_authenticated_turn_uses_only_obo_token_for_attendance_mcp() -> None:
    factory, obo = Factory(), Obo()
    attendance = AttendanceAgent(
        language_model=None,
        mcp_session_factory=factory,
        correlation_id_factory=uuid4,
        reference_date_factory=lambda: date(2026, 8, 15),
    )
    context = Context()

    await SsoOboAttendanceTurnHandler(attendance, Sso(), obo).handle(context)

    assert obo.assertions == [SecretStr("token-a")]
    assert factory.tokens == [SecretStr("token-b")]
    assert "token-a" not in repr(context.sent)
    assert "token-b" not in repr(context.sent)


@pytest.mark.anyio
async def test_personal_greeting_replays_through_one_obo_exchange_without_tool_calls() -> None:
    factory, obo, model = Factory(), Obo(), GreetingModel()
    attendance = AttendanceAgent(
        language_model=model,
        mcp_session_factory=factory,
        correlation_id_factory=lambda: UUID(int=1),
        reference_date_factory=lambda: date(2026, 8, 15),
    )
    context = Context(Activity(text="Hello"))

    await SsoOboAttendanceTurnHandler(attendance, Sso(), obo).handle(context)

    assert obo.assertions == [SecretStr("token-a")]
    assert factory.tokens == [SecretStr("token-b")]
    assert factory.correlation_ids == [UUID(int=1)]
    assert model.requests and model.requests[0].tool_results == ()
    assert factory.session.calls == []
    assert context.sent == [UNAVAILABLE_REPLY]
