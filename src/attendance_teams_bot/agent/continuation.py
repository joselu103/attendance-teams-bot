"""Typed continuation state. Database storage owns its lifecycle and binding."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from attendance_teams_bot.agent.contracts import BotAttachment, ReplyLanguage

HISTORY_VERB = "attendance.history.next"
CONTINUATION_TTL_SECONDS = 15 * 60
CONTINUATION_LEASE_SECONDS = 30

HistoryScope = Literal["self", "admin"]


@dataclass(frozen=True, slots=True)
class HistoryContinuation:
    """A non-authoritative next-page query stored only in the bot database."""

    scope: HistoryScope
    start_date: date
    end_date: date
    offset: int
    language: ReplyLanguage
    target: int | None = None

    def __post_init__(self) -> None:
        if (
            self.scope not in {"self", "admin"}
            or self.language not in {"en", "sl"}
            or type(self.start_date) is not date
            or type(self.end_date) is not date
            or self.end_date < self.start_date
            or type(self.offset) is not int
            or self.offset < 1
        ):
            raise ValueError("invalid history context")
        if self.scope == "self" and self.target is not None:
            raise ValueError("self history cannot carry a target")
        if self.scope == "admin" and (type(self.target) is not int or self.target < 1):
            raise ValueError("admin history requires a resolved target")


@dataclass(frozen=True, slots=True)
class ClaimedContinuation:
    """A leased active continuation returned only after authenticated binding checks."""

    identifier: UUID
    lease: UUID
    query: HistoryContinuation


def continuation_card(identifier: UUID, language: ReplyLanguage) -> BotAttachment:
    """Return a minimal opaque button; PostgreSQL holds the query and offset."""
    return BotAttachment(
        "application/vnd.microsoft.card.adaptive",
        {
            "type": "AdaptiveCard",
            "version": "1.4",
            "actions": [
                {
                    "type": "Action.Submit",
                    "title": "Naslednja stran" if language == "sl" else "Next page",
                    "data": {"verb": HISTORY_VERB, "continuation_id": str(identifier)},
                }
            ],
        },
    )


def is_typed_continuation(message: str) -> bool:
    """Recognize short, unambiguous English and Slovenian next-page requests."""
    normalized = re.sub(r"[!?.,]+", "", message.casefold()).strip()
    return normalized in {
        "continue",
        "continue please",
        "next",
        "next page",
        "more",
        "nadaljuj",
        "nadaljuj prosim",
        "naslednja",
        "naslednja stran",
    }
