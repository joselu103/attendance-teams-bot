from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

ReplyLanguage = Literal["en", "sl"]


@dataclass(frozen=True, slots=True)
class BotResponse:
    text: str
    request: None = None


@dataclass(frozen=True, slots=True)
class ListMyAttendanceIntent:
    start_date: date
    end_date: date


@dataclass(frozen=True, slots=True)
class Clarification:
    message: str


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
