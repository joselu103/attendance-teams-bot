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
    ModelRequest,
    PresentationPlan,
    ToolCall,
)
from attendance_teams_bot.agent.mcp_catalog import DiscoveredMcpTool
from attendance_teams_bot.agent.orchestrator import AttendanceAgent
from attendance_teams_bot.agent.rendering import UNAVAILABLE_REPLY
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
async def test_one_validated_tool_call_sends_minimal_projection_and_immutable_rendering() -> None:
    subject, model, session = agent(
        [
            call("one"),
            FinalResponse("ignored", "sl", presentation=PresentationPlan("Prisotnost", None)),
        ]
    )
    response = await subject.handle(message="Pokaži", mcp_access_token=SecretStr("token"))
    assert "**Prisotnost**" in response.text and "secret note" not in response.text
    assert len(session.calls) == 1
    assert len(model.requests) == 2
    projection = model.requests[1].tool_results[0].result
    assert "note" not in repr(projection) and "employee_id" not in repr(projection)
    assert model.requests[1].tools == ()


@pytest.mark.anyio
async def test_model_cannot_select_another_tool_after_the_validated_execution() -> None:
    subject, _, session = agent([call(), call()])
    response = await subject.handle(message="show", mcp_access_token=SecretStr("token"))
    assert response.text == "Podatki o prisotnosti trenutno niso na voljo. Poskusite znova pozneje."
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
    assert len(session.calls) == 1 and len(model.requests) == 1


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
    assert response.text == "Please clarify the attendance date range you want to view."
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
async def test_unavailable_greeting_failure_outcomes_are_classified_without_sensitive_values(
    monkeypatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    class Logger:
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
