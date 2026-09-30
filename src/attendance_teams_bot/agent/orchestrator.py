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
from attendance_teams_bot.agent.mcp_catalog import DiscoveredMcpTool, ToolPolicy, admit_mcp_catalog
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
from attendance_teams_bot.mcp.contracts import (
    CURRENT_ATTENDANCE_TOOL,
    OTHER_ATTENDANCE_TOOL,
    RESOLVE_EMPLOYEE_TOOL,
    SELF_ATTENDANCE_TOOL,
    AttendanceEventPage,
    CurrentAttendancePage,
    ResolvedEmployee,
)
from attendance_teams_bot.observability import current_correlation_id

MAX_MODEL_CALLS = 3
PAGE_SIZE = 50
MAX_CURRENT_PAGES = 200


class AuthenticatedMcpSession(Protocol):
    async def list_tools(self) -> tuple[DiscoveredMcpTool, ...]: ...
    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> AttendanceEventPage | ResolvedEmployee | CurrentAttendancePage: ...


class McpSessionFactory(Protocol):
    def open(
        self, *, access_token: SecretStr, correlation_id: UUID
    ) -> AbstractAsyncContextManager[AuthenticatedMcpSession]: ...


@dataclass(frozen=True, slots=True)
class AttendanceAgent:
    """Bounded orchestration; CRMT remains the identity and authorization authority."""

    language_model: LanguageModel
    mcp_session_factory: McpSessionFactory
    presenter: AttendanceResultPresenter = field(default_factory=AttendanceResultPresenter)
    correlation_id_factory: Callable[[], UUID] = current_correlation_id
    reference_date_factory: Callable[[], date] = lambda: datetime.now(
        ZoneInfo("Europe/Ljubljana")
    ).date()

    async def handle(
        self, *, message: str, mcp_access_token: SecretStr, display_name: str | None = None
    ) -> BotResponse:
        language: ReplyLanguage = "en"
        if has_ambiguous_numeric_date(message):
            return self.presenter.present(ClarificationPresentation(language, display_name))
        try:
            async with self.mcp_session_factory.open(
                access_token=mcp_access_token, correlation_id=self.correlation_id_factory()
            ) as session:
                catalog = admit_mcp_catalog(await session.list_tools())
                if catalog is None:
                    return self._unavailable(language, display_name)
                results: list[ToolResultView] = []
                sensitive: set[str] = set()
                for call_count in range(MAX_MODEL_CALLS + 1):
                    turn = await self.language_model.complete(
                        ModelRequest(
                            message,
                            self.reference_date_factory(),
                            "Europe/Ljubljana",
                            catalog.model_tools,
                            tuple(results),
                        )
                    )
                    if isinstance(turn, FinalResponse):
                        if (
                            turn.language not in {"en", "sl"}
                            or not is_safe_model_markdown(turn.markdown)
                            or _mentions_sensitive(turn.markdown, sensitive)
                        ):
                            return self._unavailable(language, display_name)
                        return BotResponse(text=turn.markdown)
                    if call_count == MAX_MODEL_CALLS:
                        return self._unavailable(language, display_name)
                    policy = catalog.selected_tool(turn.name)
                    if policy is None:
                        return self._unavailable(language, display_name)
                    validated = policy.validate_arguments(turn.arguments)
                    if validated is None:
                        return self.presenter.present(
                            InvalidRequestPresentation(language, display_name)
                        )
                    arguments, language = validated
                    result = await self._execute(session, policy, arguments)
                    sensitive.update(_internal_values(result))
                    results.append(ToolResultView(turn.id, policy.definition.name, result))
        except asyncio.CancelledError:
            raise
        except AttendanceToolFailure as error:
            return self.presenter.present(
                ToolFailurePresentation(error.failure.code, language, display_name)
            )
        except AttendanceMcpUnavailable, McpContractIncompatible, LanguageModelUnavailable:
            return self._unavailable(language, display_name)
        except Exception:
            return self._unavailable(language, display_name)
        return self._unavailable(language, display_name)

    async def _execute(
        self, session: AuthenticatedMcpSession, policy: ToolPolicy, arguments: Mapping[str, object]
    ) -> dict[str, object]:
        if policy.kind == "self":
            page = await session.call_tool(
                name=SELF_ATTENDANCE_TOOL, arguments={**arguments, "limit": PAGE_SIZE, "offset": 0}
            )
            return _raw_page(page)
        if policy.kind == "other":
            selector = {
                key: value
                for key, value in arguments.items()
                if key in {"employee_id", "username", "email"}
            }
            resolved = await session.call_tool(name=RESOLVE_EMPLOYEE_TOOL, arguments=selector)
            if not isinstance(resolved, ResolvedEmployee):
                raise McpContractIncompatible
            page = await session.call_tool(
                name=OTHER_ATTENDANCE_TOOL,
                arguments={
                    "employee_id": resolved.employee_id,
                    "start_date": arguments["start_date"],
                    "end_date": arguments["end_date"],
                    "limit": PAGE_SIZE,
                    "offset": 0,
                },
            )
            return _raw_page(page)
        return await _all_current_pages(session, str(arguments["status"]))

    def _unavailable(self, language: ReplyLanguage, display_name: str | None) -> BotResponse:
        return self.presenter.present(UnavailablePresentation(language, display_name))


def _raw_page(page: object) -> dict[str, object]:
    if not isinstance(page, AttendanceEventPage) or page.limit != PAGE_SIZE or page.offset != 0:
        raise McpContractIncompatible
    return page.model_dump(mode="json")


async def _all_current_pages(session: AuthenticatedMcpSession, status: str) -> dict[str, object]:
    items: list[dict[str, object]] = []
    offset = 0
    for _ in range(MAX_CURRENT_PAGES):
        page = await session.call_tool(
            name=CURRENT_ATTENDANCE_TOOL,
            arguments={"status": status, "limit": PAGE_SIZE, "offset": offset},
        )
        if (
            not isinstance(page, CurrentAttendancePage)
            or page.limit != PAGE_SIZE
            or page.offset != offset
        ):
            raise McpContractIncompatible
        # Defensive: unknown is never surfaced to the model or a Teams reply.
        items.extend(
            item for item in page.items if str(item.get("status", "")).casefold() != "unknown"
        )
        if page.next_offset is None:
            return {"items": items, "status": status, "complete": True}
        if page.next_offset <= offset:
            raise McpContractIncompatible
        offset = page.next_offset
    raise McpContractIncompatible


def _internal_values(value: object, key: str = "") -> set[str]:
    values: set[str] = set()
    if isinstance(value, Mapping):
        for name, child in value.items():
            values.update(_internal_values(child, str(name)))
    elif isinstance(value, list | tuple):
        for child in value:
            values.update(_internal_values(child, key))
    elif key.endswith("_id") or key in {"employee_id", "attendance_event_id"}:
        values.add(str(value))
    return values


def _mentions_sensitive(markdown: str, values: set[str]) -> bool:
    return any(value and value in markdown for value in values)
