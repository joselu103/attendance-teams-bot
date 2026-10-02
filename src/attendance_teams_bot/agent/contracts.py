from __future__ import annotations

from collections.abc import Mapping
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
class SelectedAttendanceAction:
    """A bot-validated pre-auth action, awaiting live MCP catalog admission."""

    name: str
    arguments: Mapping[str, object]
    language: ReplyLanguage
