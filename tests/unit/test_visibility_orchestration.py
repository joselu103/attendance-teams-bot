from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from pydantic import SecretStr

from attendance_teams_bot.agent.language_model import (
    FinalResponse,
    ModelRequest,
    PresentationPlan,
    ToolCall,
)
from attendance_teams_bot.agent.mcp_catalog import DiscoveredMcpTool
from attendance_teams_bot.agent.orchestrator import AttendanceAgent, _current_status_names
from attendance_teams_bot.mcp.client import AttendanceToolFailure
from attendance_teams_bot.mcp.contracts import (
    AttendanceEvent,
    AttendanceEventPage,
    CurrentWorkStatusPage,
    EmployeeSuggestion,
    EmployeeSuggestionPage,
    McpToolFailure,
    ResolvedEmployee,
)


def tool(name: str, properties: dict[str, object]) -> DiscoveredMcpTool:
    return DiscoveredMcpTool(
        name, "safe metadata", {"type": "object", "properties": properties}, {"readOnlyHint": True}
    )


def catalog() -> tuple[DiscoveredMcpTool, ...]:
    return (
        tool(
            "list_my_attendance_events",
            {"start_date": {}, "end_date": {}, "limit": {}, "offset": {}},
        ),
        tool("resolve_employee", {"employee_id": {}, "username": {}, "email": {}}),
        tool(
            "list_attendance_events",
            {"employee_id": {}, "start_date": {}, "end_date": {}, "limit": {}, "offset": {}},
        ),
        tool("get_current_work_status", {"statuses": {}, "limit": {}, "offset": {}}),
        tool("search_employees", {"query": {}, "limit": {}}),
    )


def events() -> AttendanceEventPage:
    return AttendanceEventPage(
        items=(
            AttendanceEvent(
                attendance_event_id=12,
                employee_id=99,
                punch_type="Office",
                location="HQ",
                checked_in_at=datetime(2026, 8, 1, tzinfo=UTC),
                checked_out_at=None,
                note="private",
            ),
        ),
        limit=50,
        offset=0,
        next_offset=None,
    )


@dataclass
class Model:
    turns: list[ToolCall | FinalResponse]
    requests: list[ModelRequest] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> ToolCall | FinalResponse:
        self.requests.append(request)
        return self.turns.pop(0)


@dataclass
class Session:
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    current_items: tuple[dict[str, object], ...] = (
        {"first_name": "Ada", "last_name": "", "status": "office"},
    )

    async def list_tools(self) -> tuple[DiscoveredMcpTool, ...]:
        return catalog()

    async def call_tool(self, *, name: str, arguments: dict[str, object]) -> object:
        self.calls.append((name, arguments))
        if name == "resolve_employee":
            return ResolvedEmployee(employee_id=99, username="alice")
        if name == "get_current_work_status":
            if arguments["offset"] == 0:
                return CurrentWorkStatusPage(
                    items=self.current_items,
                    limit=50,
                    offset=0,
                    next_offset=50,
                )
            return CurrentWorkStatusPage(
                items=(),
                limit=50,
                offset=50,
                next_offset=None,
            )
        if name == "search_employees":
            return EmployeeSuggestionPage(
                items=(EmployeeSuggestion(display_name="Tinkara Novak", username="tinkara"),)
            )
        return events()


@dataclass
class Factory:
    session: Session

    @asynccontextmanager
    async def open(self, **_: object):
        yield self.session


def subject(turns: list[ToolCall | FinalResponse]) -> tuple[AttendanceAgent, Model, Session]:
    model, session = Model(turns), Session()
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


@pytest.mark.anyio
async def test_exact_selector_is_resolved_then_internal_id_only_drives_history() -> None:
    agent, model, session = subject(
        [
            ToolCall(
                "x",
                "get_other_attendance",
                {
                    "email": "alice@example.test",
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-02",
                    "reply_language": "en",
                },
            ),
            FinalResponse("ignored", "en", presentation=PresentationPlan("Attendance", None)),
        ]
    )
    assert (
        await agent.handle(message="Alice", mcp_access_token=SecretStr("token"))
    ).text.startswith("**Part 1 of 1**\n\n**Attendance**")
    assert session.calls == [
        ("resolve_employee", {"email": "alice@example.test"}),
        (
            "list_attendance_events",
            {
                "employee_id": 99,
                "start_date": "2026-08-01",
                "end_date": "2026-08-02",
                "limit": 50,
                "offset": 0,
            },
        ),
    ]
    assert "note" not in repr(model.requests[1].tool_results[0].result)
    assert "employee_id" not in repr(model.requests[1].tool_results[0].result)


@pytest.mark.anyio
async def test_current_status_fetches_all_pages_and_hides_unknown_before_model() -> None:
    agent, model, session = subject(
        [
            ToolCall(
                "x", "get_current_work_status", {"statuses": ["office"], "reply_language": "en"}
            ),
            FinalResponse(
                "ignored", "en", presentation=PresentationPlan("Current attendance", None)
            ),
        ]
    )
    assert (
        await agent.handle(message="Office", mcp_access_token=SecretStr("token"))
    ).text.startswith("**Part 1 of 1**\n\n**Current attendance**\n\n**Office**\n- Ada")
    assert [call[1] for call in session.calls] == [
        {"statuses": ["office"], "limit": 50, "offset": 0},
        {"statuses": ["office"], "limit": 50, "offset": 50},
    ]
    assert model.requests[1].tool_results[0].result["statuses"] == [
        {"status": "office", "count": 1}
    ]
    assert "as_of" not in repr(session.calls)


@pytest.mark.anyio
async def test_current_status_uses_the_rest_contract_first_and_last_name_fields() -> None:
    agent, _, session = subject(
        [
            ToolCall(
                "x", "get_current_work_status", {"statuses": ["office"], "reply_language": "en"}
            ),
            FinalResponse(
                "ignored", "en", presentation=PresentationPlan("Current attendance", None)
            ),
        ]
    )
    session.current_items = ({"first_name": "Ada", "last_name": "Lovelace", "status": "office"},)

    response = await agent.handle(
        message="Who is in the office?", mcp_access_token=SecretStr("token")
    )

    assert response.text.startswith(
        "**Part 1 of 1**\n\n**Current attendance**\n\n**Office**\n- Ada Lovelace"
    )


@pytest.mark.anyio
async def test_current_status_without_a_filter_omits_status_and_fetches_every_page() -> None:
    agent, model, session = subject(
        [
            ToolCall("x", "get_current_work_status", {"reply_language": "en"}),
            FinalResponse(
                "ignored", "en", presentation=PresentationPlan("Current attendance", None)
            ),
        ]
    )

    assert (
        await agent.handle(message="Who is working?", mcp_access_token=SecretStr("token"))
    ).text.startswith("**Part 1 of 1**\n\n**Current attendance**\n\n**Office**\n- Ada")
    assert [call[1] for call in session.calls] == [
        {"limit": 50, "offset": 0},
        {"limit": 50, "offset": 50},
    ]
    assert "status" not in model.requests[1].tool_results[0].result


def test_current_status_groups_follow_the_business_status_order() -> None:
    groups = _current_status_names(
        {
            "items": [
                {"first_name": "No", "last_name": "status", "status": "no_status"},
                {"first_name": "Break", "last_name": "", "status": "break"},
                {"first_name": "Remote", "last_name": "", "status": "remote"},
                {"first_name": "Absent", "last_name": "", "status": "absence"},
                {"first_name": "Office", "last_name": "", "status": "office"},
                {"first_name": "Customer", "last_name": "", "status": "customer_site"},
            ]
        }
    )

    assert [status for status, _ in groups] == [
        "office",
        "remote",
        "customer_site",
        "break",
        "absence",
        "no_status",
    ]


@dataclass
class MultiStatusSession(Session):
    async def call_tool(self, *, name: str, arguments: dict[str, object]) -> object:
        self.calls.append((name, arguments))
        if name == "get_current_work_status":
            statuses = arguments["statuses"]
            if not isinstance(statuses, list):
                raise AssertionError("expected an MCP statuses array")
            names = {"office": "Office employee", "remote": "Remote employee"}
            return CurrentWorkStatusPage(
                items=tuple(
                    {"first_name": names[status], "last_name": "", "status": status}
                    for status in statuses
                ),
                limit=50,
                offset=0,
                next_offset=None,
            )
        return await super().call_tool(name=name, arguments=arguments)


@pytest.mark.anyio
async def test_current_status_accepts_and_combines_multiple_requested_statuses() -> None:
    model = Model(
        [
            ToolCall(
                "selection",
                "get_current_work_status",
                {"statuses": ["office", "remote"], "reply_language": "en"},
            ),
            FinalResponse(
                "ignored", "en", presentation=PresentationPlan("Current attendance", None)
            ),
        ]
    )
    session = MultiStatusSession()
    agent = AttendanceAgent(
        model,
        Factory(session),
        correlation_id_factory=lambda: UUID(int=1),
        reference_date_factory=lambda: date(2026, 8, 15),
    )

    response = await agent.handle(message="office or home", mcp_access_token=SecretStr("token"))

    assert [call[1] for call in session.calls] == [
        {"statuses": ["office", "remote"], "limit": 50, "offset": 0}
    ]
    assert "**Office**\n- Office employee" in response.text
    assert "**Remote work**\n- Remote employee" in response.text


@pytest.mark.anyio
async def test_rejects_multiple_selectors_unknown_status_and_internal_ids_in_reply() -> None:
    agent, _, session = subject(
        [
            ToolCall(
                "x",
                "get_other_attendance",
                {
                    "email": "a@b.test",
                    "username": "a",
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-02",
                    "reply_language": "en",
                },
            )
        ]
    )
    assert (
        "temporarily unavailable"
        in (await agent.handle(message="x", mcp_access_token=SecretStr("token"))).text
    )
    assert not session.calls
    agent, _, _ = subject(
        [
            ToolCall(
                "x",
                "get_current_work_status",
                {"statuses": ["unknown"], "reply_language": "en"},
            )
        ]
    )
    assert (
        "temporarily unavailable"
        in (await agent.handle(message="x", mcp_access_token=SecretStr("token"))).text
    )
    agent, _, _ = subject(
        [
            ToolCall(
                "x",
                "get_other_attendance",
                {
                    "employee_id": "99",
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-02",
                    "reply_language": "en",
                },
            ),
            FinalResponse("Employee 99 is present", "en"),
        ]
    )
    assert (
        "temporarily unavailable"
        in (await agent.handle(message="x", mcp_access_token=SecretStr("token"))).text
    )


@pytest.mark.anyio
async def test_authority_denial_from_resolution_is_rendered_safely() -> None:
    @dataclass
    class ForbiddenSession(Session):
        async def call_tool(self, *, name: str, arguments: dict[str, object]) -> object:
            if name == "resolve_employee":
                raise AttendanceToolFailure(
                    McpToolFailure(
                        code="FORBIDDEN", message="You do not have permission to do that."
                    )
                )
            return await super().call_tool(name=name, arguments=arguments)

    model = Model(
        [
            ToolCall(
                "x",
                "get_other_attendance",
                {
                    "username": "alice",
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-02",
                    "reply_language": "en",
                },
            )
        ]
    )
    agent = AttendanceAgent(
        model,
        Factory(ForbiddenSession()),
        correlation_id_factory=lambda: UUID(int=1),
        reference_date_factory=lambda: date(2026, 8, 15),
    )
    response = await agent.handle(message="Alice", mcp_access_token=SecretStr("token"))
    assert response.text == "You do not have permission to view that attendance."


@pytest.mark.anyio
async def test_model_cannot_make_an_attendance_claim_before_a_tool_succeeds() -> None:
    agent, _, session = subject([FinalResponse("No records; Ada is remote.", "en")])

    response = await agent.handle(message="Where is Ada?", mcp_access_token=SecretStr("token"))

    assert "temporarily unavailable" in response.text
    assert not session.calls


@pytest.mark.anyio
async def test_name_search_renders_candidates_and_requires_a_later_selection() -> None:
    agent, model, session = subject(
        [ToolCall("x", "search_employees", {"query": "Tinkara", "reply_language": "en"})]
    )

    response = await agent.handle(
        message="Show Tinkara's attendance", mcp_access_token=SecretStr("token")
    )

    assert response.text == (
        "Select one employee in a new message:\n"
        "- Tinkara Novak — tinkara\n"
        "Reply with the listed username or email."
    )
    assert session.calls == [("search_employees", {"query": "Tinkara", "limit": 10})]
    assert len(model.requests) == 1
