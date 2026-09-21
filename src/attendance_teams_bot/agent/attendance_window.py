"""Bot-controlled attendance range partitioning and page aggregation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from attendance_teams_bot.mcp.client import McpContractIncompatible
from attendance_teams_bot.mcp.contracts import AttendanceEvent, AttendanceEventPage

PAGE_SIZE = 50
MAX_EVENTS = 200


@dataclass(frozen=True, slots=True)
class AttendanceWindow:
    """A bot-owned inclusive range that can be safely sent to one MCP call."""

    start_date: date
    end_date: date


@dataclass(frozen=True, slots=True)
class OverallAttendanceRange:
    """An inclusive requester range, bounded to twelve rolling calendar months."""

    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        if self.start_date > self.end_date:
            raise ValueError("end date precedes start date")
        if self.end_date > _add_calendar_months(self.start_date, 12):
            raise ValueError("date range exceeds 12 rolling calendar months")

    def windows(self) -> tuple[AttendanceWindow, ...]:
        """Partition this range into chronological, contiguous 31-day windows."""
        windows: list[AttendanceWindow] = []
        start = self.start_date
        while start <= self.end_date:
            end = min(start.fromordinal(start.toordinal() + 30), self.end_date)
            windows.append(AttendanceWindow(start, end))
            start = start.fromordinal(end.toordinal() + 1)
        return tuple(windows)


class AttendancePageReader(Protocol):
    """Reads a bot-selected page for a bot-owned attendance window."""

    async def read_page(self, *, window: AttendanceWindow, offset: int) -> AttendanceEventPage: ...


class AuthenticatedMcpToolCaller(Protocol):
    """Calls an already authenticated MCP tool with bot-controlled arguments."""

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> AttendanceEventPage: ...


@dataclass(frozen=True, slots=True)
class McpAttendancePageReader:
    """Serializes only fixed, bot-owned attendance MCP page arguments."""

    session: AuthenticatedMcpToolCaller
    tool_name: str

    async def read_page(self, *, window: AttendanceWindow, offset: int) -> AttendanceEventPage:
        return await self.session.call_tool(
            name=self.tool_name,
            arguments={
                "start_date": window.start_date.isoformat(),
                "end_date": window.end_date.isoformat(),
                "limit": PAGE_SIZE,
                "offset": offset,
            },
        )


@dataclass(frozen=True, slots=True)
class AttendanceWindowResult:
    """Aggregate events plus an indication that the bot omitted additional records."""

    events: tuple[AttendanceEvent, ...]
    records_omitted: bool


@dataclass(frozen=True, slots=True)
class AttendanceWindowExecutor:
    """Read bounded MCP pages across an overall range, capping the aggregate at 200 events."""

    page_reader: AttendancePageReader

    async def execute(self, overall_range: OverallAttendanceRange) -> AttendanceWindowResult:
        """Aggregate pages, failing the request if any page is invalid or unavailable."""
        events: list[AttendanceEvent] = []
        records_omitted = False
        for window in overall_range.windows():
            offset = 0
            while True:
                page = await self.page_reader.read_page(window=window, offset=offset)
                if page.limit != PAGE_SIZE or page.offset != offset or len(page.items) > PAGE_SIZE:
                    raise McpContractIncompatible
                remaining = MAX_EVENTS - len(events)
                events.extend(page.items[:remaining])
                if len(page.items) > remaining:
                    records_omitted = True
                if len(events) == MAX_EVENTS:
                    return AttendanceWindowResult(
                        events=tuple(events),
                        records_omitted=records_omitted or page.next_offset is not None,
                    )
                if page.next_offset is None:
                    break
                offset += PAGE_SIZE
        return AttendanceWindowResult(events=tuple(events), records_omitted=records_omitted)


def _add_calendar_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year, month_zero_based = divmod(value.year * 12 + month_index, 12)
    month = month_zero_based + 1
    for day in range(value.day, 0, -1):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError("a calendar month always has at least one day")
