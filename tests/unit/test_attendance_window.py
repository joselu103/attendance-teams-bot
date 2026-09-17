from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pytest

from attendance_teams_bot.agent.attendance_window import (
    AttendanceWindow,
    AttendanceWindowExecutor,
    McpAttendancePageReader,
    OverallAttendanceRange,
)
from attendance_teams_bot.agent.language_model import ToolDefinition
from attendance_teams_bot.mcp.client import AttendanceToolFailure, McpContractIncompatible
from attendance_teams_bot.mcp.contracts import AttendanceEvent, AttendanceEventPage, McpToolFailure


@dataclass
class FakePageReader:
    pages: list[AttendanceEventPage] = field(default_factory=list)
    error: Exception | None = None
    error_on_call: int | None = None
    calls: list[tuple[AttendanceWindow, int]] = field(default_factory=list)

    async def read_page(self, *, window: AttendanceWindow, offset: int) -> AttendanceEventPage:
        self.calls.append((window, offset))
        if self.error is not None and self.error_on_call == len(self.calls):
            raise self.error
        return self.pages.pop(0) if self.pages else attendance_page()


@dataclass
class FakeMcpSession:
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def call_tool(self, *, name: str, arguments: dict[str, object]) -> AttendanceEventPage:
        self.calls.append((name, arguments))
        return attendance_page()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def attendance_event() -> AttendanceEvent:
    return AttendanceEvent(
        attendance_event_id=13,
        employee_id=7,
        punch_type="Office",
        location="Company",
        checked_in_at=datetime(2026, 8, 10, 8, tzinfo=UTC),
        checked_out_at=datetime(2026, 8, 10, 16, tzinfo=UTC),
        note=None,
    )


def attendance_page(
    *,
    items: tuple[AttendanceEvent, ...] | None = None,
    limit: int = 50,
    offset: int = 0,
    next_offset: int | None = None,
) -> AttendanceEventPage:
    return AttendanceEventPage(
        items=(attendance_event(),) if items is None else items,
        limit=limit,
        offset=offset,
        next_offset=next_offset,
    )


def test_overall_range_enforces_twelve_month_bound_and_gap_free_windows() -> None:
    overall = OverallAttendanceRange(date(2025, 9, 15), date(2026, 9, 15))

    windows = overall.windows()

    assert windows[0].start_date == overall.start_date
    assert windows[-1].end_date == overall.end_date
    assert all((window.end_date - window.start_date).days <= 30 for window in windows)
    assert all(
        left.end_date.toordinal() + 1 == right.start_date.toordinal()
        for left, right in zip(windows, windows[1:], strict=False)
    )
    with pytest.raises(ValueError, match="12 rolling calendar months"):
        OverallAttendanceRange(date(2025, 9, 15), date(2026, 9, 16))


@pytest.mark.anyio
async def test_mcp_page_reader_serializes_fixed_bot_owned_request() -> None:
    session = FakeMcpSession()
    reader = McpAttendancePageReader(
        session,
        ToolDefinition("list_my_attendance_events", "", {"type": "object"}),
    )

    await reader.read_page(window=AttendanceWindow(date(2026, 8, 10), date(2026, 8, 12)), offset=50)

    assert session.calls == [
        (
            "list_my_attendance_events",
            {"start_date": "2026-08-10", "end_date": "2026-08-12", "limit": 50, "offset": 50},
        )
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "malformed_page",
    [
        attendance_page(limit=49),
        attendance_page(offset=50),
        attendance_page(items=(attendance_event(),) * 51),
    ],
)
async def test_executor_rejects_malformed_page_metadata(
    malformed_page: AttendanceEventPage,
) -> None:
    executor = AttendanceWindowExecutor(FakePageReader(pages=[malformed_page]))

    with pytest.raises(McpContractIncompatible):
        await executor.execute(OverallAttendanceRange(date(2026, 8, 10), date(2026, 8, 12)))


@pytest.mark.anyio
async def test_executor_caps_at_two_hundred_events_and_discloses_omission() -> None:
    event = attendance_event()
    reader = FakePageReader(
        pages=[
            attendance_page(items=(event,) * 50, offset=offset, next_offset=offset + 50)
            for offset in range(0, 200, 50)
        ]
    )

    result = await AttendanceWindowExecutor(reader).execute(
        OverallAttendanceRange(date(2026, 8, 10), date(2026, 8, 12))
    )

    assert [offset for _, offset in reader.calls] == [0, 50, 100, 150]
    assert len(result.events) == 200
    assert result.records_omitted is True


@pytest.mark.anyio
async def test_executor_propagates_later_page_failure_without_partial_result() -> None:
    failure = AttendanceToolFailure(
        McpToolFailure(code="FORBIDDEN", message="You do not have permission to do that.")
    )
    reader = FakePageReader(pages=[attendance_page(next_offset=50)], error=failure, error_on_call=2)

    with pytest.raises(AttendanceToolFailure) as raised:
        await AttendanceWindowExecutor(reader).execute(
            OverallAttendanceRange(date(2026, 8, 10), date(2026, 8, 12))
        )

    assert raised.value is failure
    assert len(reader.calls) == 2
