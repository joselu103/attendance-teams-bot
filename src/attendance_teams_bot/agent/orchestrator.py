from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import SecretStr

from attendance_teams_bot.agent.contracts import BotResponse
from attendance_teams_bot.agent.date_resolver import has_ambiguous_numeric_date
from attendance_teams_bot.agent.language_model import (
    FinalResponse,
    LanguageModel,
    LanguageModelUnavailable,
    ModelRequest,
    ReplyLanguage,
    ToolResultView,
)
from attendance_teams_bot.agent.mcp_catalog import DiscoveredMcpTool, admit_mcp_catalog
from attendance_teams_bot.agent.rendering import (
    AttendanceResultPresenter,
    ClarificationPresentation,
    InvalidRequestPresentation,
    ToolFailurePresentation,
    UnavailablePresentation,
    is_safe_model_markdown,
)
from attendance_teams_bot.mcp.client import (
    AttendanceMcpUnavailable,
    AttendanceToolFailure,
    McpContractIncompatible,
)
from attendance_teams_bot.mcp.contracts import AttendanceEvent, AttendanceEventPage
from attendance_teams_bot.observability import current_correlation_id

MAX_MCP_CALLS = 3
MAX_PROJECTED_EVENTS = 50


class AuthenticatedMcpSession(Protocol):
    async def list_tools(self) -> tuple[DiscoveredMcpTool, ...]: ...

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> AttendanceEventPage: ...


class McpSessionFactory(Protocol):
    def open(
        self, *, access_token: SecretStr, correlation_id: UUID
    ) -> AbstractAsyncContextManager[AuthenticatedMcpSession]: ...


@dataclass(frozen=True, slots=True)
class AttendanceAgent:
    """Run at most three validated, sequential MCP calls before a safe model reply."""

    language_model: LanguageModel
    mcp_session_factory: McpSessionFactory
    presenter: AttendanceResultPresenter = field(default_factory=AttendanceResultPresenter)
    correlation_id_factory: Callable[[], UUID] = current_correlation_id
    reference_date_factory: Callable[[], date] = lambda: datetime.now(
        ZoneInfo("Europe/Ljubljana")
    ).date()

    async def handle(
        self,
        *,
        message: str,
        mcp_access_token: SecretStr,
        display_name: str | None = None,
    ) -> BotResponse:
        """Fail closed: only an approved final response can contain attendance data."""
        language: ReplyLanguage = "en"
        if has_ambiguous_numeric_date(message):
            return self.presenter.present(
                ClarificationPresentation(language=language, display_name=display_name)
            )
        try:
            async with self.mcp_session_factory.open(
                access_token=mcp_access_token, correlation_id=self.correlation_id_factory()
            ) as session:
                catalog = admit_mcp_catalog(await session.list_tools())
                if catalog is None:
                    return self._unavailable(language, display_name)
                results: list[ToolResultView] = []
                reference_date = self.reference_date_factory()
                for calls_made in range(MAX_MCP_CALLS + 1):
                    turn = await self.language_model.complete(
                        ModelRequest(
                            user_message=message,
                            reference_date=reference_date,
                            timezone="Europe/Ljubljana",
                            tools=catalog.model_tools,
                            tool_results=tuple(results),
                        )
                    )
                    if isinstance(turn, FinalResponse):
                        if turn.language not in {"en", "sl"} or not is_safe_model_markdown(
                            turn.markdown
                        ):
                            return self._unavailable(language, display_name)
                        return BotResponse(text=turn.markdown)
                    if calls_made == MAX_MCP_CALLS:
                        return self._unavailable(language, display_name)
                    policy = catalog.selected_tool(turn.name)
                    if policy is None:
                        return self._unavailable(language, display_name)
                    validated = policy.validate_arguments(turn.arguments)
                    if validated is None:
                        return self.presenter.present(
                            InvalidRequestPresentation(language=language, display_name=display_name)
                        )
                    arguments, language = validated
                    page = await session.call_tool(
                        name=policy.definition.name,
                        arguments={
                            "start_date": arguments.start_date.isoformat(),
                            "end_date": arguments.end_date.isoformat(),
                            "limit": MAX_PROJECTED_EVENTS,
                            "offset": 0,
                        },
                    )
                    results.append(_approved_result_view(turn.id, policy.definition.name, page))
        except asyncio.CancelledError:
            raise
        except AttendanceToolFailure as error:
            return self.presenter.present(
                ToolFailurePresentation(
                    code=error.failure.code, language=language, display_name=display_name
                )
            )
        except AttendanceMcpUnavailable, McpContractIncompatible, LanguageModelUnavailable:
            return self._unavailable(language, display_name)
        except Exception:
            return self._unavailable(language, display_name)
        return self._unavailable(language, display_name)

    def _unavailable(self, language: ReplyLanguage, display_name: str | None) -> BotResponse:
        return self.presenter.present(
            UnavailablePresentation(language=language, display_name=display_name)
        )


def _approved_result_view(
    call_id: str, tool_name: str, page: AttendanceEventPage
) -> ToolResultView:
    """Project provider-approved fields, never raw MCP objects, IDs, or notes."""
    if page.limit != MAX_PROJECTED_EVENTS or page.offset != 0:
        raise McpContractIncompatible
    events = tuple(page.items[:MAX_PROJECTED_EVENTS])
    return ToolResultView(
        call_id=call_id,
        tool_name=tool_name,
        result={
            "events": [_project_event(event) for event in events],
            "truncated": len(page.items) > MAX_PROJECTED_EVENTS or page.next_offset is not None,
        },
    )


def _project_event(event: AttendanceEvent) -> dict[str, object]:
    local_start = _local_timestamp(event.checked_in_at)
    local_end = _local_timestamp(event.checked_out_at)
    timestamp = local_start or local_end
    projected: dict[str, object] = {
        "display_date": timestamp.date().isoformat() if timestamp else "unavailable",
        "start_time": local_start.strftime("%H:%M") if local_start else None,
        "end_time": local_end.strftime("%H:%M") if local_end else None,
        "type": _canonical_type(event.punch_type),
    }
    location = _sanitized_location(event.location)
    if location:
        projected["location"] = location
    return projected


def _local_timestamp(value: datetime | None) -> datetime | None:
    return value.astimezone(ZoneInfo("Europe/Ljubljana")) if value else None


def _canonical_type(value: str | None) -> str:
    normalized = " ".join((value or "").split()).casefold()
    return {
        "office": "office",
        "delo na firmi": "office",
        "work": "work",
        "remote work": "remote_work",
        "work from home": "remote_work",
        "break": "break",
        "leave": "leave",
    }.get(normalized, "unspecified")


def _sanitized_location(value: str | None) -> str | None:
    if not value:
        return None
    normalized = " ".join(value.split())[:80]
    if not normalized or any(character in normalized for character in "<>`[]{}"):
        return None
    return normalized
