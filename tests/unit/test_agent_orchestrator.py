from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from pydantic import SecretStr

from attendance_teams_bot.agent import orchestrator
from attendance_teams_bot.agent.language_model import ModelRequest, NoTool, ToolCall, ToolDefinition
from attendance_teams_bot.agent.orchestrator import (
    _LEGACY_READ_ONLY_TOOL_NAMES,
    CLARIFICATION_REPLY,
    INVALID_REQUEST_REPLY,
    UNAVAILABLE_REPLY,
    AttendanceAgent,
    canonical_self_attendance_tool,
    guidance_tool,
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
    failure_on_call: int | None = None
    pages: list[AttendanceEventPage] = field(default_factory=list)
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def list_tools(self) -> tuple[ToolDefinition, ...]:
        return self.tools

    async def call_tool(self, *, name: str, arguments: dict[str, object]) -> AttendanceEventPage:
        self.calls.append((name, arguments))
        if self.failure is not None and (
            self.failure_on_call is None or len(self.calls) == self.failure_on_call
        ):
            raise self.failure
        if self.pages:
            return self.pages.pop(0)
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
        annotations={"readOnlyHint": True},
    )


def legacy_read_only_catalog() -> tuple[ToolDefinition, ...]:
    return tuple(
        compatible_tool()
        if name == SELF_ATTENDANCE_TOOL
        else ToolDefinition(
            name=name,
            description="Unprompted legacy read-only tool",
            input_schema={"type": "object"},
            annotations={"readOnlyHint": True},
        )
        for name in sorted(_LEGACY_READ_ONLY_TOOL_NAMES)
    )


def build_agent(
    turn: ToolCall | NoTool, tools: tuple[ToolDefinition, ...] | None = None
) -> tuple[AttendanceAgent, FakeLanguageModel, FakeMcpSession]:
    if (
        isinstance(turn, ToolCall)
        and turn.name == SELF_ATTENDANCE_TOOL
        and "reply_language" not in turn.arguments
    ):
        turn = ToolCall(turn.id, turn.name, {**turn.arguments, "reply_language": "en"})
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

    assert model.requests[0].tools == (canonical_self_attendance_tool(), guidance_tool())
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
async def test_agent_admits_full_legacy_catalog_but_prompts_and_calls_only_self_tool() -> None:
    agent, model, session = build_agent(
        ToolCall(
            "call-1", SELF_ATTENDANCE_TOOL, {"start_date": "2026-08-10", "end_date": "2026-08-12"}
        ),
        legacy_read_only_catalog(),
    )

    await agent.handle(message="Show attendance", mcp_access_token=SecretStr("token-b"))

    assert model.requests[0].tools == (canonical_self_attendance_tool(), guidance_tool())
    assert session.calls == [
        (
            SELF_ATTENDANCE_TOOL,
            {"start_date": "2026-08-10", "end_date": "2026-08-12", "limit": 50, "offset": 0},
        )
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "tools",
    [
        (),
        (ToolDefinition("admin_tool", "x", {"type": "object"}),),
        (compatible_tool(), compatible_tool()),
        (compatible_tool(), ToolDefinition("unexpected_tool", "x", {"type": "object"})),
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
        {"start_date": "2025-08-10", "end_date": "2026-09-10"},
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
async def test_agent_rejects_an_oversized_model_argument_before_the_mcp_call() -> None:
    agent, _, session = build_agent(
        ToolCall(
            "call",
            SELF_ATTENDANCE_TOOL,
            {"start_date": "2026-08-10", "end_date": "x" * 33},
        )
    )

    response = await agent.handle(message="Show attendance", mcp_access_token=SecretStr("token-b"))

    assert response.text == INVALID_REQUEST_REPLY
    assert session.calls == []


@pytest.mark.anyio
async def test_agent_requires_the_remote_catalog_read_only_marker() -> None:
    remote = compatible_tool()
    remote = ToolDefinition(remote.name, remote.description, remote.input_schema)
    agent, model, session = build_agent(ToolCall("call", SELF_ATTENDANCE_TOOL, {}), (remote,))

    response = await agent.handle(message="Show attendance", mcp_access_token=SecretStr("token-b"))

    assert response.text == UNAVAILABLE_REPLY
    assert model.requests == []
    assert session.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "tools",
    [
        tuple(tool for tool in legacy_read_only_catalog() if tool.name != SELF_ATTENDANCE_TOOL),
        tuple(
            ToolDefinition(
                tool.name,
                tool.description,
                {"type": "object"} if tool.name == SELF_ATTENDANCE_TOOL else tool.input_schema,
                tool.annotations,
            )
            for tool in legacy_read_only_catalog()
        ),
        tuple(
            ToolDefinition(tool.name, tool.description, tool.input_schema, {})
            if tool.name == SELF_ATTENDANCE_TOOL
            else tool
            for tool in legacy_read_only_catalog()
        ),
        tuple(
            ToolDefinition(
                tool.name,
                tool.description,
                {"type": object()} if tool.name == "list_locations" else tool.input_schema,
                tool.annotations,
            )
            for tool in legacy_read_only_catalog()
        ),
    ],
)
async def test_agent_rejects_missing_or_incompatible_selected_tool_in_legacy_catalog(
    tools: tuple[ToolDefinition, ...],
) -> None:
    agent, model, session = build_agent(ToolCall("call", SELF_ATTENDANCE_TOOL, {}), tools)

    response = await agent.handle(message="Show attendance", mcp_access_token=SecretStr("token-b"))

    assert response.text == UNAVAILABLE_REPLY
    assert model.requests == []
    assert session.calls == []


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


@pytest.mark.anyio
async def test_agent_localizes_attendance_and_strips_reply_language_before_mcp() -> None:
    agent, _, session = build_agent(
        ToolCall(
            "call",
            SELF_ATTENDANCE_TOOL,
            {"start_date": "2026-08-10", "end_date": "2026-08-12", "reply_language": "sl"},
        )
    )

    response = await agent.handle(
        message="Pokaži prisotnost", mcp_access_token=SecretStr("token-b")
    )

    assert "Prisotnost" in response.text
    assert session.calls[0][1] == {
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "limit": 50,
        "offset": 0,
    }


@pytest.mark.anyio
async def test_guidance_is_localized_and_never_reaches_mcp() -> None:
    agent, _, session = build_agent(
        ToolCall("call", "respond_with_guidance", {"intent": "date_ambiguous", "language": "sl"})
    )

    response = await agent.handle(message="moja prisotnost", mcp_access_token=SecretStr("token-b"))

    assert response.text == "Prosimo, pojasnite obdobje prisotnosti, ki ga želite prikazati."
    assert session.calls == []


@pytest.mark.anyio
async def test_agent_rejects_invalid_synthetic_guidance_and_language_arguments() -> None:
    guidance_agent, _, guidance_session = build_agent(
        ToolCall("call", "respond_with_guidance", {"intent": "other", "language": "sl"})
    )
    attendance_agent, _, attendance_session = build_agent(
        ToolCall(
            "call",
            SELF_ATTENDANCE_TOOL,
            {"start_date": "2026-08-10", "end_date": "2026-08-12", "reply_language": "de"},
        )
    )

    guidance_response = await guidance_agent.handle(
        message="x", mcp_access_token=SecretStr("token-b")
    )
    attendance_response = await attendance_agent.handle(
        message="x", mcp_access_token=SecretStr("token-b")
    )

    assert guidance_response.text == UNAVAILABLE_REPLY
    assert attendance_response.text == INVALID_REQUEST_REPLY
    assert guidance_session.calls == []
    assert attendance_session.calls == []


@pytest.mark.anyio
async def test_agent_greets_a_sanitized_presentation_only_display_name() -> None:
    agent, _, session = build_agent(
        ToolCall(
            "call",
            SELF_ATTENDANCE_TOOL,
            {"start_date": "2026-08-10", "end_date": "2026-08-12", "reply_language": "en"},
        )
    )

    response = await agent.handle(
        message="Show attendance",
        mcp_access_token=SecretStr("token-b"),
        display_name="  Ana *Example*  ",
    )

    assert response.text.startswith("Hello, Ana \\*Example\\*!")
    assert len(session.calls) == 1


@pytest.mark.anyio
async def test_agent_records_a_correlated_terminal_outcome_without_sensitive_data(
    monkeypatch,
) -> None:
    agent, _, _ = build_agent(
        ToolCall(
            "call-1",
            SELF_ATTENDANCE_TOOL,
            {"start_date": "2026-08-10", "end_date": "2026-08-12"},
        )
    )
    events: list[tuple[str, dict[str, object]]] = []

    def record_event(_logger, **fields: object) -> None:
        events.append((str(fields.pop("event")), fields))

    monkeypatch.setattr(orchestrator, "operation_event", record_event)

    response = await agent.handle(
        message="private attendance request",
        mcp_access_token=SecretStr("token-b"),
    )

    assert response.text
    assert [event for event, _ in events] == [
        "operation_started",
        "operation_step_completed",
        "operation_step_completed",
        "operation_step_completed",
        "operation_step_completed",
        "operation_succeeded",
    ]
    event, fields = events[-1]
    assert event == "operation_succeeded"
    assert fields["handler"] == "AttendanceAgent.handle"
    assert fields["operation"] == "attendance_orchestration"
    assert fields["step"] == "rendering"
    assert fields["input_metadata"]["outcome"] == "success"
    assert fields["input_metadata"]["error_code"] is None
    assert fields["error_type"] is None
    assert isinstance(fields["duration_ms"], int)
    assert "private attendance request" not in repr(events)
    assert "token-b" not in repr(events)


@pytest.mark.anyio
async def test_agent_records_permission_denial_without_mcp_error_message(monkeypatch) -> None:
    agent, _, session = build_agent(
        ToolCall(
            "call-1",
            SELF_ATTENDANCE_TOOL,
            {"start_date": "2026-08-10", "end_date": "2026-08-12"},
        )
    )
    session.failure = AttendanceToolFailure(
        McpToolFailure(code="FORBIDDEN", message="You do not have permission to do that.")
    )
    auth_events: list[dict[str, object]] = []

    def record_auth_event(_logger, **fields: object) -> None:
        auth_events.append(fields)

    monkeypatch.setattr(orchestrator, "authentication_event", record_auth_event)

    await agent.handle(message="private attendance request", mcp_access_token=SecretStr("token-b"))

    assert auth_events == [
        {
            "event": "permission_denied",
            "scheme": "attendance_mcp",
            "failure_reason": "FORBIDDEN",
        }
    ]
    assert "private attendance request" not in repr(auth_events)
    assert "token-b" not in repr(auth_events)
