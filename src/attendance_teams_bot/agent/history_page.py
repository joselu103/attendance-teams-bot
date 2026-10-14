"""Deep exchange for one authorized attendance-history page."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, TypeGuard

from attendance_teams_bot.agent.continuation import HistoryContinuation, HistoryScope
from attendance_teams_bot.agent.contracts import BotResponse, ReplyLanguage
from attendance_teams_bot.agent.model_contract import delivery_memory
from attendance_teams_bot.agent.rendering import AttendanceResultPresenter, SafeHistoryPresentation
from attendance_teams_bot.mcp.client import McpContractIncompatible
from attendance_teams_bot.mcp.contracts import (
    OTHER_ATTENDANCE_TOOL,
    SELF_ATTENDANCE_TOOL,
    AttendanceEventPage,
)

PAGE_SIZE = 50


class HistoryPageSession(Protocol):
    """The one MCP operation required at the history-page seam."""

    async def call_tool(self, *, name: str, arguments: Mapping[str, object]) -> object: ...


@dataclass(frozen=True, slots=True)
class HistoryPageRequest:
    """Validated bot-owned shape for one attendance-history page."""

    scope: HistoryScope
    start_date: date
    end_date: date
    offset: int
    language: ReplyLanguage
    target: int | None = None
    page_number: int = 1

    def __post_init__(self) -> None:
        if (
            self.scope not in {"self", "admin"}
            or self.language not in {"en", "sl"}
            or type(self.start_date) is not date
            or type(self.end_date) is not date
            or self.end_date < self.start_date
            or type(self.offset) is not int
            or self.offset < 0
            or type(self.page_number) is not int
            or self.page_number < 1
        ):
            raise ValueError("invalid history page request")
        if self.scope == "self" and self.target is not None:
            raise ValueError("self history cannot carry a target")
        if self.scope == "admin" and (type(self.target) is not int or self.target < 1):
            raise ValueError("admin history requires a resolved target")

    @classmethod
    def from_continuation(cls, continuation: HistoryContinuation) -> HistoryPageRequest:
        return cls(
            continuation.scope,
            continuation.start_date,
            continuation.end_date,
            continuation.offset,
            continuation.language,
            continuation.target,
            continuation.page_number,
        )


@dataclass(frozen=True, slots=True)
class AttendanceHistoryExchange:
    """Execute, validate, present, and continue one authorized history page."""

    presenter: AttendanceResultPresenter = field(default_factory=AttendanceResultPresenter)

    async def execute(
        self, session: HistoryPageSession, request: HistoryPageRequest
    ) -> BotResponse:
        page = await session.call_tool(
            name=SELF_ATTENDANCE_TOOL if request.scope == "self" else OTHER_ATTENDANCE_TOOL,
            arguments={
                "start_date": request.start_date.isoformat(),
                "end_date": request.end_date.isoformat(),
                **({"employee_id": request.target} if request.scope == "admin" else {}),
                "limit": PAGE_SIZE,
                "offset": request.offset,
            },
        )
        if not _valid_page(page, request.offset):
            raise McpContractIncompatible
        continuation = _next_continuation(page, request)
        response = self.presenter.present(
            SafeHistoryPresentation(
                page.items,
                request.language,
                None,
                page.summary,
                request.start_date,
                request.end_date,
                continuation is not None,
                request.page_number,
            )
        )
        return BotResponse(
            response.text,
            response.messages,
            delivery_memory(continuation is not None),
            attachments=response.attachments,
            continuation=continuation,
            continuation_complete=True,
        )


def _valid_page(page: object, offset: int) -> TypeGuard[AttendanceEventPage]:
    return (
        isinstance(page, AttendanceEventPage)
        and page.limit == PAGE_SIZE
        and page.offset == offset
        and len(page.items) <= PAGE_SIZE
        and (
            page.next_offset is None
            or (type(page.next_offset) is int and page.next_offset > offset and bool(page.items))
        )
    )


def _next_continuation(
    page: AttendanceEventPage, request: HistoryPageRequest
) -> HistoryContinuation | None:
    if page.next_offset is None:
        return None
    return HistoryContinuation(
        request.scope,
        request.start_date,
        request.end_date,
        page.next_offset,
        request.language,
        request.target,
        request.page_number + 1,
    )
