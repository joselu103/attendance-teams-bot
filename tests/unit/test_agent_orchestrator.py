from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from pydantic import SecretStr

from attendance_teams_bot.agent.language_model import ModelRequest, NoTool, ToolCall, ToolDefinition
from attendance_teams_bot.agent.orchestrator import (
    CLARIFICATION_REPLY,
    INVALID_REQUEST_REPLY,
    UNAVAILABLE_REPLY,
    AttendanceAgent,
    canonical_self_attendance_tool,
)
from attendance_teams_bot.mcp.client import AttendanceToolFailure
from attendance_teams_bot.mcp.contracts import (
    SELF_ATTENDANCE_TOOL,
    AttendanceEvent,
    AttendanceEventPage,
    McpToolFailure,
)


@dataclass
class FakeLanguageModel:
    turn: ToolCall | NoTool
    requests: list[ModelRequest] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> ToolCall | NoTool:
        self.requests.append(request)
        return self.turn


@dataclass
class FakeMcpSession:
    tools: tuple[ToolDefinition, ...]
    failure: AttendanceToolFailure | None = None
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def list_tools(self) -> tuple[ToolDefinition, ...]:
        return self.tools

    async def call_tool(self, *, name: str, arguments: dict[str, object]) -> AttendanceEventPage:
        self.calls.append((name, arguments))
        if self.failure is not None:
            raise self.failure
        return page()


@dataclass
class FakeMcpSessionFactory:
    session: FakeMcpSession
    opened_with: list[tuple[SecretStr, UUID]] = field(default_factory=list)

    @asynccontextmanager
    async def open(self, *, access_token: SecretStr, correlation_id: UUID):
        self.opened_with.append((access_token, correlation_id))
        yield self.session


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def page() -> AttendanceEventPage:
    return AttendanceEventPage(
        items=(
            AttendanceEvent(
                attendance_event_id=13,
                employee_id=7,
                punch_type="Office",
                location="Company",
                checked_in_at=datetime(2026, 8, 10, 8, tzinfo=UTC),
                checked_out_at=datetime(2026, 8, 10, 16, tzinfo=UTC),
                note="Internal note",
            ),
        ),
        limit=50,
        offset=0,
        next_offset=None,
    )


def compatible_tool(description: str = "Remote description") -> ToolDefinition:
    return ToolDefinition(
        name=SELF_ATTENDANCE_TOOL,
        description=description,
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
    )


def build_agent(
    turn: ToolCall | NoTool, tools: tuple[ToolDefinition, ...] | None = None
) -> tuple[AttendanceAgent, FakeLanguageModel, FakeMcpSession]:
    model = FakeLanguageModel(turn)
    session = FakeMcpSession((compatible_tool(),) if tools is None else tools)
    return (
        AttendanceAgent(
            language_model=model,
            mcp_session_factory=FakeMcpSessionFactory(session),
            correlation_id_factory=lambda: UUID("11111111-1111-1111-1111-111111111111"),
            reference_date_factory=lambda: date(2026, 8, 15),
        ),
        model,
        session,
    )


@pytest.mark.anyio
async def test_agent_uses_bot_owned_schema_and_one_fixed_page_call() -> None:
    agent, model, session = build_agent(
        ToolCall(
            "call-1", SELF_ATTENDANCE_TOOL, {"start_date": "2026-08-10", "end_date": "2026-08-12"}
        )
    )

    response = await agent.handle(
        message="How was my attendance?", mcp_access_token=SecretStr("token-b")
    )

    assert model.requests[0].tools == (canonical_self_attendance_tool(),)
    assert "Remote description" not in repr(model.requests[0])
    assert model.requests[0].reference_date == date(2026, 8, 15)
    assert model.requests[0].timezone == "Europe/Ljubljana"
    assert session.calls == [
        (
            SELF_ATTENDANCE_TOOL,
            {"start_date": "2026-08-10", "end_date": "2026-08-12", "limit": 50, "offset": 0},
        )
    ]
    assert "Internal" not in response.text
    assert "13" not in response.text
    assert "7" not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "tools",
    [
        (),
        (ToolDefinition("admin_tool", "x", {"type": "object"}),),
        (compatible_tool(), compatible_tool()),
    ],
)
async def test_agent_fails_closed_before_model_for_incompatible_catalog(
    tools: tuple[ToolDefinition, ...],
) -> None:
    agent, model, session = build_agent(ToolCall("call", SELF_ATTENDANCE_TOOL, {}), tools)

    response = await agent.handle(message="Show attendance", mcp_access_token=SecretStr("token-b"))

    assert response.text == UNAVAILABLE_REPLY
    assert model.requests == []
    assert session.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "arguments",
    [
        {"start_date": "2026-08-10", "end_date": "2026-09-10"},
        {"start_date": "2026-08-10", "end_date": "2026-08-12", "employee_id": 7},
        {"start_date": "yesterday", "end_date": "today"},
        {"start_date": "2026-08-10", "end_date": "2026-08-12", "limit": 100},
        {"start_date": "2026-08-12", "end_date": "2026-08-10"},
    ],
)
async def test_agent_rejects_arguments_outside_requester_contract(
    arguments: dict[str, object],
) -> None:
    agent, _, session = build_agent(ToolCall("call", SELF_ATTENDANCE_TOOL, arguments))

    response = await agent.handle(message="Show attendance", mcp_access_token=SecretStr("token-b"))

    assert response.text == INVALID_REQUEST_REPLY
    assert session.calls == []


@pytest.mark.anyio
async def test_agent_accepts_exactly_31_inclusive_dates() -> None:
    agent, _, session = build_agent(
        ToolCall(
            "call", SELF_ATTENDANCE_TOOL, {"start_date": "2026-08-01", "end_date": "2026-08-31"}
        )
    )

    await agent.handle(message="Show attendance", mcp_access_token=SecretStr("token-b"))

    assert len(session.calls) == 1


@pytest.mark.anyio
async def test_agent_preserves_stable_mcp_tool_failure_reply() -> None:
    agent, _, session = build_agent(
        ToolCall(
            "call",
            SELF_ATTENDANCE_TOOL,
            {"start_date": "2026-08-10", "end_date": "2026-08-12"},
        )
    )
    session.failure = AttendanceToolFailure(
        McpToolFailure(
            code="FORBIDDEN",
            message="You do not have permission to do that.",
        )
    )

    response = await agent.handle(message="Show attendance", mcp_access_token=SecretStr("token-b"))

    assert response.text == "You do not have permission to view that attendance."


@pytest.mark.anyio
async def test_agent_returns_fixed_clarification_for_no_tool_and_unknown_tool() -> None:
    agent, _, session = build_agent(NoTool())
    no_tool_response = await agent.handle(message="Hello", mcp_access_token=SecretStr("token-b"))
    unknown_agent, _, unknown_session = build_agent(ToolCall("call", "admin_tool", {}))
    unknown_response = await unknown_agent.handle(
        message="Hello", mcp_access_token=SecretStr("token-b")
    )

    assert no_tool_response.text == CLARIFICATION_REPLY
    assert unknown_response.text == UNAVAILABLE_REPLY
    assert session.calls == []
    assert unknown_session.calls == []
