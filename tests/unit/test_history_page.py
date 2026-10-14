from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pytest

from attendance_teams_bot.agent.continuation import HistoryContinuation
from attendance_teams_bot.agent.history_page import AttendanceHistoryExchange, HistoryPageRequest
from attendance_teams_bot.mcp.client import AttendanceToolFailure, McpContractIncompatible
from attendance_teams_bot.mcp.contracts import (
    OTHER_ATTENDANCE_TOOL,
    SELF_ATTENDANCE_TOOL,
    AttendanceEvent,
    AttendanceEventPage,
    McpToolFailure,
)


@dataclass
class FakeHistoryPageSession:
    page: object
    calls: list[dict[str, object]] = field(default_factory=list)

    async def call_tool(self, *, name: str, arguments: dict[str, object]) -> object:
        self.calls.append({"name": name, **arguments})
        if isinstance(self.page, Exception):
            raise self.page
        return self.page


def page(**changes: object) -> AttendanceEventPage:
    result = AttendanceEventPage(
        items=(
            AttendanceEvent(
                attendance_event_id=1,
                employee_id=2,
                punch_type="Office",
                location="HQ",
                checked_in_at=datetime(2026, 8, 10, 8, tzinfo=UTC),
                checked_out_at=datetime(2026, 8, 10, 16, tzinfo=UTC),
                note="private",
                duration_minutes=480,
            ),
        ),
        limit=50,
        offset=0,
        next_offset=None,
        summary={"page_duration_minutes": 480, "period_duration_minutes": 480},
    )
    return result.model_copy(update=changes)


@pytest.mark.anyio
async def test_requester_page_renders_reply_and_derives_continuation() -> None:
    session = FakeHistoryPageSession(page(next_offset=50))

    response = await AttendanceHistoryExchange().execute(
        session, HistoryPageRequest("self", date(2026, 8, 10), date(2026, 8, 12), 0, "en")
    )

    assert session.calls == [
        {
            "name": SELF_ATTENDANCE_TOOL,
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "limit": 50,
            "offset": 0,
        }
    ]
    assert response.continuation == HistoryContinuation(
        "self", date(2026, 8, 10), date(2026, 8, 12), 50, "en"
    )
    assert response.continuation_complete and "private" not in response.text


@pytest.mark.anyio
async def test_admin_page_uses_resolved_target_and_continuation_conversion() -> None:
    session = FakeHistoryPageSession(page(offset=50))
    request = HistoryPageRequest.from_continuation(
        HistoryContinuation("admin", date(2026, 8, 10), date(2026, 8, 12), 50, "sl", 9)
    )

    response = await AttendanceHistoryExchange().execute(session, request)

    assert session.calls[0] == {
        "name": OTHER_ATTENDANCE_TOOL,
        "employee_id": 9,
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "limit": 50,
        "offset": 50,
    }
    assert response.continuation is None and "Stran 2" in response.text


@pytest.mark.anyio
async def test_malformed_page_fails_closed_at_the_exchange_seam() -> None:
    session = FakeHistoryPageSession(page(limit=49))

    with pytest.raises(McpContractIncompatible):
        await AttendanceHistoryExchange().execute(
            session, HistoryPageRequest("self", date(2026, 8, 10), date(2026, 8, 12), 0, "en")
        )


@pytest.mark.anyio
async def test_tool_failure_propagates_from_the_exchange_seam() -> None:
    failure = AttendanceToolFailure(
        McpToolFailure(
            code="BACKEND_UNAVAILABLE",
            message="Attendance is temporarily unavailable. Please try again shortly.",
        )
    )

    with pytest.raises(AttendanceToolFailure):
        await AttendanceHistoryExchange().execute(
            FakeHistoryPageSession(failure),
            HistoryPageRequest("self", date(2026, 8, 10), date(2026, 8, 12), 0, "en"),
        )
