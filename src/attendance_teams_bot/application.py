from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import SecretStr

from attendance_teams_bot.agent.contracts import BotResponse, Clarification, ListMyAttendanceIntent
from attendance_teams_bot.mcp.client import (
    AttendanceMcpClient,
    AttendanceMcpUnavailable,
    AttendanceToolFailure,
    McpContractIncompatible,
)
from attendance_teams_bot.mcp.contracts import AttendanceEvent, AttendanceEventPage

logging.basicConfig(level=logging.INFO, format="%(message)s")

_LOGGER = logging.getLogger(__name__)
_UNAVAILABLE_REPLY = "Attendance data is temporarily unavailable. Please try again later."
_TOOL_FAILURE_REPLIES = {
    "INVALID_ARGUMENT": "Check the attendance date range and try again.",
    "FORBIDDEN": "You do not have permission to view that attendance.",
    "IDENTITY_UNMAPPED": (
        "Your Teams account is not linked to an active attendance employee. "
        "Contact an administrator."
    ),
    "IDENTITY_AMBIGUOUS": "Your Teams account cannot be linked safely. Contact an administrator.",
    "BACKEND_UNAVAILABLE": "Attendance is temporarily unavailable. Please try again later.",
    "AUTHENTICATION_REQUIRED": "Please sign in and try again.",
    "TOKEN_INVALID": "Please sign in and try again.",
    "INTERNAL_ERROR": _UNAVAILABLE_REPLY,
    "CORRELATION_ID_INVALID": _UNAVAILABLE_REPLY,
}


class IntentSelector(Protocol):
    def select_intent(self, message: str) -> ListMyAttendanceIntent | Clarification: ...


@dataclass(frozen=True, slots=True)
class Application:
    intent_selector: IntentSelector
    mcp_client: AttendanceMcpClient
    correlation_id_factory: Callable[[], UUID]

    async def handle(self, *, message: str, mcp_access_token: SecretStr) -> BotResponse:
        intent = self.intent_selector.select_intent(message)
        if isinstance(intent, Clarification):
            return BotResponse(text=intent.message)
        correlation_id = self.correlation_id_factory()
        started_at = time.monotonic()
        try:
            page = await self.mcp_client.list_my_attendance_events(
                access_token=mcp_access_token,
                correlation_id=correlation_id,
                start_date=intent.start_date,
                end_date=intent.end_date,
            )
        except AttendanceToolFailure as error:
            self._log_attempt(correlation_id, started_at, "failure", error.failure.code)
            return BotResponse(
                text=_TOOL_FAILURE_REPLIES.get(error.failure.code, _UNAVAILABLE_REPLY)
            )
        except AttendanceMcpUnavailable:
            self._log_attempt(correlation_id, started_at, "failure", "MCP_UNAVAILABLE")
            return BotResponse(text=_UNAVAILABLE_REPLY)
        except McpContractIncompatible:
            self._log_attempt(correlation_id, started_at, "failure", "MCP_CONTRACT_INCOMPATIBLE")
            return BotResponse(text=_UNAVAILABLE_REPLY)
        self._log_attempt(correlation_id, started_at, "success", None)
        return BotResponse(text=_render_attendance_page(page))

    @staticmethod
    def _log_attempt(
        correlation_id: UUID,
        started_at: float,
        outcome: str,
        error_code: str | None,
    ) -> None:
        _LOGGER.info(
            json.dumps(
                {
                    "event": "attendance_mcp_call",
                    "correlation_id": str(correlation_id),
                    "tool_name": "list_my_attendance_events",
                    "outcome": outcome,
                    "error_code": error_code,
                    "duration_ms": max(0, round((time.monotonic() - started_at) * 1000)),
                },
                separators=(",", ":"),
            )
        )


def create_application(
    *,
    intent_selector: IntentSelector,
    mcp_client: AttendanceMcpClient,
    correlation_id_factory: Callable[[], UUID] = uuid4,
) -> Application:
    return Application(intent_selector, mcp_client, correlation_id_factory)


def _render_attendance_page(page: AttendanceEventPage) -> str:
    if not page.items:
        return "No attendance events were found for that date range."
    response = "\n".join(_render_attendance_event(event) for event in page.items)
    if page.next_offset is not None:
        response += "\nShowing the first 50 events; more events are available."
    return response


def _render_attendance_event(event: AttendanceEvent) -> str:
    checked_in = (
        event.checked_in_at.isoformat(sep=" ", timespec="minutes")
        if event.checked_in_at
        else "Open"
    )
    checked_out = (
        event.checked_out_at.isoformat(sep=" ", timespec="minutes")
        if event.checked_out_at
        else "Open"
    )
    punch_type = event.punch_type or "Unspecified attendance"
    location = event.location or "Unspecified location"
    return f"{checked_in}–{checked_out}: {punch_type} at {location}"
