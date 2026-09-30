from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from pydantic import SecretStr

from attendance_teams_bot.agent.language_model import FinalResponse, ModelRequest, ToolCall
from attendance_teams_bot.agent.mcp_catalog import DiscoveredMcpTool
from attendance_teams_bot.agent.orchestrator import AttendanceAgent
from attendance_teams_bot.agent.rendering import CLARIFICATION_REPLY, UNAVAILABLE_REPLY
from attendance_teams_bot.mcp.client import AttendanceToolFailure
from attendance_teams_bot.mcp.contracts import (
    SELF_ATTENDANCE_TOOL,
    AttendanceEvent,
    AttendanceEventPage,
    McpToolFailure,
)


@dataclass
class FakeModel:
    turns: list[ToolCall | FinalResponse]
    requests: list[ModelRequest] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> ToolCall | FinalResponse:
        self.requests.append(request)
        return self.turns.pop(0)


@dataclass
class FakeSession:
    pages: list[AttendanceEventPage] = field(default_factory=list)
    failure_on: int | None = None
    calls: list[dict[str, object]] = field(default_factory=list)

    async def list_tools(self) -> tuple[DiscoveredMcpTool, ...]:
        return (compatible_tool(),)

    async def call_tool(self, *, name: str, arguments: dict[str, object]) -> AttendanceEventPage:
        self.calls.append({"name": name, **arguments})
        if self.failure_on == len(self.calls):
            raise AttendanceToolFailure(
                McpToolFailure(
                    code="BACKEND_UNAVAILABLE",
                    message="Attendance is temporarily unavailable. Please try again shortly.",
                )
            )
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
            ),
        ),
        limit=50,
        offset=0,
        next_offset=None,
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
async def test_three_serial_calls_send_user_approved_raw_result_then_model_markdown() -> None:
    subject, model, session = agent(
        [call("one"), call("two"), call("three"), FinalResponse("**Prisotnost**", "sl")]
    )
    response = await subject.handle(message="Pokaži", mcp_access_token=SecretStr("token"))
    assert response.text == "**Prisotnost**"
    assert len(session.calls) == 3
    assert len(model.requests) == 4
    projection = model.requests[1].tool_results[0].result
    assert projection["items"][0]["note"] == "secret note"
    assert projection["items"][0]["employee_id"] == 2
    assert all(request.tools[0].name == SELF_ATTENDANCE_TOOL for request in model.requests)


@pytest.mark.anyio
async def test_fourth_call_and_bad_final_fail_closed_after_three_calls() -> None:
    subject, _, session = agent([call(), call(), call(), call()])
    response = await subject.handle(message="show", mcp_access_token=SecretStr("token"))
    assert response.text == "Podatki o prisotnosti trenutno niso na voljo. Poskusite znova pozneje."
    assert len(session.calls) == 3
    bad, _, _ = agent([FinalResponse("[unsafe](https://example.test)", "en")])
    assert (
        await bad.handle(message="show", mcp_access_token=SecretStr("token"))
    ).text == UNAVAILABLE_REPLY


@pytest.mark.anyio
async def test_failure_discards_accumulated_results_and_reuses_validated_language() -> None:
    subject, model, session = agent([call(), call()], FakeSession(failure_on=2))
    response = await subject.handle(message="show", mcp_access_token=SecretStr("token"))
    assert response.text == "Podatki o prisotnosti trenutno niso na voljo. Poskusite znova pozneje."
    assert len(session.calls) == 2 and len(model.requests) == 2


@pytest.mark.anyio
async def test_ambiguous_numeric_date_never_reaches_model_or_mcp() -> None:
    subject, model, session = agent([FinalResponse("ignored", "en")])
    response = await subject.handle(message="show 6/8", mcp_access_token=SecretStr("token"))
    assert response.text == CLARIFICATION_REPLY
    assert not model.requests and not session.calls
