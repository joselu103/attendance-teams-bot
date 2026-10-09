from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal

ReplyLanguage = Literal["en", "sl"]


@dataclass(frozen=True, slots=True)
class BotResponse:
    text: str
    messages: tuple[str, ...] = ()
    assistant_memory: str | None = None
    request: None = None

    def __post_init__(self) -> None:
        messages = self.messages or (self.text,)
        if not messages or any(not isinstance(message, str) or not message for message in messages):
            raise ValueError("a response requires a non-empty ordered message batch")
        if self.text != messages[0]:
            raise ValueError("response text must remain the first message for legacy callers")
        object.__setattr__(self, "messages", messages)

    @classmethod
    def batch(cls, messages: tuple[str, ...]) -> BotResponse:
        return cls(text=messages[0], messages=messages)


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
