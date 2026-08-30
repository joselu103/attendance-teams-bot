from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import SecretStr

from attendance_teams_bot.agent.contracts import BotResponse, Clarification, ListMyAttendanceIntent
from attendance_teams_bot.mcp.client import AttendanceMcpClient
from attendance_teams_bot.mcp.contracts import AttendanceEvent, AttendanceEventPage


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
        page = await self.mcp_client.list_my_attendance_events(
            access_token=mcp_access_token,
            correlation_id=self.correlation_id_factory(),
            start_date=intent.start_date,
            end_date=intent.end_date,
        )
        return BotResponse(text=_render_attendance_page(page))


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
    return "\n".join(_render_attendance_event(event) for event in page.items)


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
