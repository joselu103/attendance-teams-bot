from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from pydantic import SecretStr

import attendance_teams_bot.agent.orchestrator as orchestrator
from attendance_teams_bot.agent.language_model import (
    FinalResponse,
    LanguageModelUnavailable,
    ModelMode,
    ModelRequest,
    ToolCall,
)
from attendance_teams_bot.agent.mcp_catalog import DiscoveredMcpTool
from attendance_teams_bot.agent.orchestrator import AttendanceAgent, AttendanceNeeded
from attendance_teams_bot.agent.rendering import UNAVAILABLE_REPLY
from attendance_teams_bot.mcp.client import AttendanceToolFailure
from attendance_teams_bot.mcp.contracts import (
    OTHER_ATTENDANCE_TOOL,
    RESOLVE_EMPLOYEE_TOOL,
    SELF_ATTENDANCE_TOOL,
    AttendanceEvent,
    AttendanceEventPage,
    AttendanceEventSummary,
    McpToolFailure,
)


@dataclass
class FakeModel:
    turns: list[ToolCall | FinalResponse]
    requests: list[ModelRequest] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> ToolCall | FinalResponse:
        self.requests.append(request)
        if (
            request.mode is ModelMode.PRE_AUTH
            and self.turns
            and isinstance(self.turns[0], ToolCall)
        ):
            language = self.turns[0].arguments.get("reply_language", "en")
            assert language in {"en", "sl"}
            return FinalResponse("attendance", language, "attendance_needed")
        return self.turns.pop(0)


@dataclass
class FakeSession:
    pages: list[AttendanceEventPage] = field(default_factory=list)
    failure_on: int | None = None
    exception_on: Exception | None = None
    tools: tuple[DiscoveredMcpTool, ...] | None = None
    calls: list[dict[str, object]] = field(default_factory=list)

    async def list_tools(self) -> tuple[DiscoveredMcpTool, ...]:
        return self.tools or (compatible_tool(),)

    async def call_tool(self, *, name: str, arguments: dict[str, object]) -> AttendanceEventPage:
        self.calls.append({"name": name, **arguments})
        if self.failure_on == len(self.calls):
            raise AttendanceToolFailure(
                McpToolFailure(
                    code="BACKEND_UNAVAILABLE",
                    message="Attendance is temporarily unavailable. Please try again shortly.",
                )
            )
        if self.exception_on is not None:
            raise self.exception_on
        return self.pages.pop(0) if self.pages else page()


@dataclass
class Factory:
    session: FakeSession

    @asynccontextmanager
    async def open(self, **_: object):
        yield self.session


def compatible_tool() -> DiscoveredMcpTool:
    return DiscoveredMcpTool(
        SELF_ATTENDANCE_TOOL,
        "remote",
        {
            "type": "object",
            "properties": {
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
            },
            "required": ["start_date", "end_date"],
            "additionalProperties": False,
        },
        {"readOnlyHint": True},
    )


def admin_tools() -> tuple[DiscoveredMcpTool, ...]:
    return (
        compatible_tool(),
        DiscoveredMcpTool(
            RESOLVE_EMPLOYEE_TOOL,
            "resolver",
            {"type": "object", "properties": {"employee_id": {}, "username": {}, "email": {}}},
            {"readOnlyHint": True},
        ),
        DiscoveredMcpTool(
            OTHER_ATTENDANCE_TOOL,
            "history",
            {
                "type": "object",
                "properties": {
                    "employee_id": {},
                    "start_date": {},
                    "end_date": {},
                    "limit": {},
                    "offset": {},
                },
            },
            {"readOnlyHint": True},
        ),
    )


def page() -> AttendanceEventPage:
    return AttendanceEventPage(
        items=(
            AttendanceEvent(
                attendance_event_id=1,
                employee_id=2,
                punch_type="Office",
                location="HQ",
                checked_in_at=datetime(2026, 8, 10, 8, tzinfo=UTC),
                checked_out_at=datetime(2026, 8, 10, 16, tzinfo=UTC),
                note="secret note",
                duration_minutes=480,
            ),
        ),
        limit=50,
        offset=0,
        next_offset=None,
        summary={"page_duration_minutes": 480, "period_duration_minutes": 480},
    )


def agent(
    turns: list[ToolCall | FinalResponse], session: FakeSession | None = None
) -> tuple[AttendanceAgent, FakeModel, FakeSession]:
    model, session = FakeModel(turns), session or FakeSession()
    return (
        AttendanceAgent(
            model,
            Factory(session),
            correlation_id_factory=lambda: UUID(int=1),
            reference_date_factory=lambda: date(2026, 8, 15),
        ),
        model,
        session,
    )


def call(identifier: str = "one") -> ToolCall:
    return ToolCall(
        identifier,
        SELF_ATTENDANCE_TOOL,
        {"start_date": "2026-08-10", "end_date": "2026-08-12", "reply_language": "sl"},
    )


@pytest.mark.anyio
async def test_validated_tool_call_renders_without_a_post_tool_model_turn() -> None:
    subject, model, session = agent([call("one")])
    response = await subject.handle(message="Pokaži", mcp_access_token=SecretStr("token"))
    assert "**Evidenca prisotnosti**" in response.text and "secret note" not in response.text
    assert "Seštevek obdobja: 8h 00m" in response.text
    assert len(session.calls) == 1
    assert len(model.requests) == 2
    assert model.requests[0].tools == ()
    assert model.requests[1].tools


@pytest.mark.anyio
async def test_validated_execution_completes_without_another_model_turn() -> None:
    subject, _, session = agent([call(), call()])
    response = await subject.handle(message="show", mcp_access_token=SecretStr("token"))
    assert "Evidenca prisotnosti" in response.text
    assert len(session.calls) == 1
    bad, _, _ = agent([FinalResponse("[unsafe](https://example.test)", "en")])
    assert (
        await bad.handle(message="show", mcp_access_token=SecretStr("token"))
    ).text == UNAVAILABLE_REPLY


@pytest.mark.anyio
async def test_failure_returns_localized_safe_reply_without_additional_model_or_tool_calls() -> (
    None
):
    subject, model, session = agent([call()], FakeSession(failure_on=1))
    response = await subject.handle(message="show", mcp_access_token=SecretStr("token"))
    assert response.text == "Podatki o prisotnosti trenutno niso na voljo. Poskusite znova pozneje."
    assert len(session.calls) == 1 and len(model.requests) == 2


@pytest.mark.anyio
async def test_malformed_history_metrics_from_the_session_fail_closed_without_partial_report() -> (
    None
):
    subject, model, session = agent(
        [call()], FakeSession(exception_on=ValueError("invalid metric"))
    )

    response = await subject.handle(message="show", mcp_access_token=SecretStr("token"))

    assert response.text == "Podatki o prisotnosti trenutno niso na voljo. Poskusite znova pozneje."
    assert len(session.calls) == 1 and len(model.requests) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("separator", [".", "/", "-"])
async def test_numeric_day_month_period_executes_model_resolved_dates(separator: str) -> None:
    subject, _, session = agent(
        [
            ToolCall(
                "numeric-period",
                SELF_ATTENDANCE_TOOL,
                {"start_date": "2026-01-10", "end_date": "2026-01-15"},
            )
        ]
    )
    response = await subject.handle(
        message=f"What is my attendance from 10{separator}01 until 15{separator}01",
        mcp_access_token=SecretStr("token"),
    )
    assert len(session.calls) == 1
    assert session.calls[0]["start_date"] == "2026-01-10"
    assert session.calls[0]["end_date"] == "2026-01-15"
    assert "2026-01-10" in response.text and "2026-01-15" in response.text


@pytest.mark.anyio
async def test_ambiguous_numeric_date_is_a_pre_auth_model_clarification() -> None:
    subject, model, session = agent(
        [
            FinalResponse(
                "Please clarify the attendance date range.", "en", "attendance_clarification"
            )
        ]
    )
    response = await subject.handle(message="show 6/8", mcp_access_token=SecretStr("token"))
    assert (
        response.text
        == "Please restate the complete attendance period with both start and end dates."
    )
    assert len(model.requests) == 1 and not session.calls


@pytest.mark.anyio
async def test_pre_auth_model_guidance_is_a_code_rendered_fixed_reply() -> None:
    subject, _, session = agent(
        [FinalResponse("The model's invented guidance", "sl", "attendance_scope_guidance")]
    )

    response = await subject.handle(message="weather?", mcp_access_token=SecretStr("token"))

    assert response.text == "Lahko vam prikažem dogodke vaše prisotnosti za določeno obdobje."
    assert not session.calls


@pytest.mark.anyio
async def test_pre_auth_scope_guidance_accepts_empty_markdown() -> None:
    subject, _, session = agent([FinalResponse("", "en", "attendance_scope_guidance")])

    response = await subject.handle(message="weather?", mcp_access_token=SecretStr("token"))

    assert response.text == "I can show your attendance events for a specific date range."
    assert not session.calls


@pytest.mark.anyio
async def test_pre_auth_attendance_needed_accepts_empty_sentinel_markdown() -> None:
    subject, model, session = agent([FinalResponse("", "en", "attendance_needed")])

    decision = await subject.pre_auth_decision(message="Show my attendance")

    assert decision == AttendanceNeeded("en")
    assert len(model.requests) == 1
    assert not session.calls


@pytest.mark.anyio
async def test_pre_auth_tool_call_is_rejected_without_authentication() -> None:
    @dataclass
    class PreAuthToolModel:
        async def complete(self, request: ModelRequest) -> ToolCall:
            assert request.mode is ModelMode.PRE_AUTH
            return call()

    session = FakeSession()
    subject = AttendanceAgent(
        PreAuthToolModel(),
        Factory(session),
        correlation_id_factory=lambda: UUID(int=1),
        reference_date_factory=lambda: date(2026, 8, 15),
    )

    response = await subject.handle(message="show attendance", mcp_access_token=SecretStr("token"))

    assert response.text == UNAVAILABLE_REPLY
    assert not session.calls


@pytest.mark.anyio
async def test_handle_uses_one_reference_date_for_every_model_turn() -> None:
    dates = iter((date(2026, 8, 15), date(2026, 8, 16)))
    subject, model, _ = agent([call()])
    subject = AttendanceAgent(
        model,
        subject.mcp_session_factory,
        correlation_id_factory=lambda: UUID(int=1),
        reference_date_factory=lambda: next(dates),
    )

    await subject.handle(message="Pokaži", mcp_access_token=SecretStr("token"))

    assert [request.reference_date for request in model.requests] == [date(2026, 8, 15)] * 2


@pytest.mark.anyio
async def test_unavailable_greeting_failure_outcomes_are_classified_without_sensitive_values(
    monkeypatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    class Logger:
        def debug(self, event: str, **fields: object) -> None:
            del event, fields

        def info(self, event: str, **fields: object) -> None:
            del event, fields

        def warning(self, event: str, **fields: object) -> None:
            events.append((event, fields))

    monkeypatch.setattr(orchestrator, "_LOGGER", Logger(), raising=False)

    @dataclass
    class RejectedCatalogSession(FakeSession):
        async def list_tools(self) -> tuple[DiscoveredMcpTool, ...]:
            return ()

    @dataclass
    class UnavailableModel:
        async def complete(self, request: ModelRequest) -> FinalResponse:
            del request
            raise LanguageModelUnavailable

    for model, session in (
        (FakeModel([FinalResponse("Hello!", "en")]), FakeSession()),
        (
            FakeModel(
                [
                    ToolCall(
                        "catalog",
                        SELF_ATTENDANCE_TOOL,
                        {
                            "start_date": "2026-08-10",
                            "end_date": "2026-08-12",
                            "reply_language": "en",
                        },
                    )
                ]
            ),
            RejectedCatalogSession(),
        ),
        (UnavailableModel(), FakeSession()),
        (FakeModel([]), FakeSession()),
    ):
        response = await AttendanceAgent(
            model,
            Factory(session),
            correlation_id_factory=lambda: UUID(int=1),
            reference_date_factory=lambda: date(2026, 8, 15),
        ).handle(message="hello token=must-not-log", mcp_access_token=SecretStr("token-b"))
        assert response.text == UNAVAILABLE_REPLY

    assert events == [
        (
            "attendance_agent_unavailable",
            {"correlation_id": str(UUID(int=1)), "outcome": "malformed_final"},
        ),
        (
            "attendance_agent_unavailable",
            {"correlation_id": str(UUID(int=1)), "outcome": "catalog_rejected"},
        ),
        (
            "attendance_agent_unavailable",
            {
                "correlation_id": str(UUID(int=1)),
                "outcome": "model_unavailable",
                "error_type": "LanguageModelUnavailable",
            },
        ),
        (
            "attendance_agent_unavailable",
            {
                "correlation_id": str(UUID(int=1)),
                "outcome": "unexpected_exception",
                "error_type": "IndexError",
            },
        ),
    ]
    assert "must-not-log" not in repr(events)
    assert "token-b" not in repr(events)


@pytest.mark.anyio
async def test_history_requests_one_complete_multiyear_page_only() -> None:
    selection = ToolCall(
        "one",
        SELF_ATTENDANCE_TOOL,
        {"start_date": "2020-01-01", "end_date": "2030-12-31", "reply_language": "en"},
    )
    first = page().model_copy(update={"next_offset": 50})
    subject, _, session = agent([selection], FakeSession(pages=[first]))
    decision = await subject.pre_auth_decision(message="2020 through 2030")
    response = await subject.handle_selected(
        message="2020 through 2030",
        selection=decision,
        mcp_access_token=SecretStr("token"),
    )
    assert session.calls == [
        {
            "name": SELF_ATTENDANCE_TOOL,
            "start_date": "2020-01-01",
            "end_date": "2030-12-31",
            "limit": 50,
            "offset": 0,
        }
    ]
    assert "2030" in response.text
    assert 'Type "next" for the next page.' in response.messages[-1]
    assert "End of results." not in response.messages[-1]
    assert response.continuation is not None and response.continuation.page_number == 2


@pytest.mark.anyio
async def test_continuation_is_direct_live_catalog_and_never_calls_model() -> None:
    from attendance_teams_bot.agent.continuation import HistoryContinuation

    query = HistoryContinuation(
        "self",
        date(2020, 1, 1),
        date(2030, 12, 31),
        50,
        "en",
    )
    fresh_page = page().model_copy(
        update={
            "offset": 50,
            "summary": AttendanceEventSummary(
                page_duration_minutes=720, period_duration_minutes=720
            ),
        }
    )
    subject, model, session = agent([], FakeSession(pages=[fresh_page]))
    response = await subject.handle_continuation(
        query=query, mcp_access_token=SecretStr("fresh-token")
    )
    assert session.calls == [
        {
            "name": SELF_ATTENDANCE_TOOL,
            "start_date": "2020-01-01",
            "end_date": "2030-12-31",
            "limit": 50,
            "offset": 50,
        }
    ]
    assert not model.requests
    assert "📅 **Attendance Record** (Page 2)" in response.messages[-1]
    assert "Period Total: 12h 00m" in response.messages[-1]
    assert "End of results." in response.messages[-1]


@pytest.mark.anyio
async def test_continuation_advances_saved_page_number_without_model() -> None:
    from attendance_teams_bot.agent.continuation import HistoryContinuation

    query = HistoryContinuation(
        "self", date(2020, 1, 1), date(2030, 12, 31), 100, "en", page_number=3
    )
    subject, model, _ = agent(
        [], FakeSession(pages=[page().model_copy(update={"offset": 100, "next_offset": 150})])
    )
    response = await subject.handle_continuation(
        query=query, mcp_access_token=SecretStr("fresh-token")
    )
    assert "(Page 3)" in response.text
    assert response.continuation is not None
    assert response.continuation.page_number == 4
    assert not model.requests


@pytest.mark.anyio
async def test_admin_continuation_uses_the_changed_live_total_without_model_or_cursor_cache() -> (
    None
):
    from attendance_teams_bot.agent.continuation import HistoryContinuation

    query = HistoryContinuation("admin", date(2026, 8, 1), date(2026, 8, 2), 50, "en", 99)
    fresh_page = page().model_copy(
        update={
            "offset": 50,
            "summary": AttendanceEventSummary(
                page_duration_minutes=600, period_duration_minutes=600
            ),
        }
    )
    subject, model, session = agent([], FakeSession(pages=[fresh_page], tools=admin_tools()))

    response = await subject.handle_continuation(
        query=query, mcp_access_token=SecretStr("fresh-token")
    )

    assert session.calls == [
        {
            "name": OTHER_ATTENDANCE_TOOL,
            "employee_id": 99,
            "start_date": "2026-08-01",
            "end_date": "2026-08-02",
            "limit": 50,
            "offset": 50,
        }
    ]
    assert "Period Total: 10h 00m" in response.text
    assert not model.requests


@pytest.mark.anyio
@pytest.mark.parametrize(
    "arguments",
    [
        {"start_date": "2026-08-01", "reply_language": "en"},
        {"end_date": "2026-08-31", "reply_language": "en"},
        {"start_date": "2026-08-31", "end_date": "2026-08-01", "reply_language": "en"},
        {"start_date": "not-a-date", "end_date": "2026-08-01", "reply_language": "en"},
    ],
)
async def test_missing_invalid_or_reversed_boundaries_restate_whole_period_before_mcp(
    arguments,
) -> None:
    subject, model, session = agent([ToolCall("selection", SELF_ATTENDANCE_TOOL, arguments)])
    response = await subject.handle(message="show attendance", mcp_access_token=SecretStr("token"))
    assert response.text == "Please provide a valid attendance request."
    assert not session.calls and len(model.requests) == 2


@pytest.mark.anyio
async def test_clear_relative_period_resolves_from_ljubljana_reference_and_displays_dates() -> None:
    subject, model, session = agent(
        [
            ToolCall(
                "selection",
                SELF_ATTENDANCE_TOOL,
                {"start_date": "2026-07-01", "end_date": "2026-07-31", "reply_language": "en"},
            )
        ]
    )
    response = await subject.handle(message="last month", mcp_access_token=SecretStr("token"))
    assert model.requests[0].reference_date == date(2026, 8, 15)
    assert "Timezone: `Europe/Ljubljana`" in model.requests[0].messages[0].content
    assert (
        session.calls[0]["start_date"] == "2026-07-01"
        and session.calls[0]["end_date"] == "2026-07-31"
    )
    assert "2026-07-01" in response.text and "2026-07-31" in response.text
