from __future__ import annotations

from dataclasses import dataclass
from datetime import date


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
