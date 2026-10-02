from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from pydantic import SecretStr

from attendance_teams_bot.agent.attendance_window import (
    AttendanceWindowExecutor,
    McpAttendancePageReader,
    OverallAttendanceRange,
)
from attendance_teams_bot.agent.contracts import BotResponse, SelectedAttendanceAction
from attendance_teams_bot.agent.language_model import (
    FinalResponse,
    LanguageModel,
    LanguageModelUnavailable,
    ModelRequest,
    ReplyLanguage,
    ToolResultView,
)
from attendance_teams_bot.agent.mcp_catalog import (
    DiscoveredMcpTool,
    ToolPolicy,
    admit_mcp_catalog,
    pre_auth_tool_policies,
)
from attendance_teams_bot.agent.rendering import (
    AttendanceResultPresenter,
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
    EmployeeSuggestionPage,
    ResolvedEmployee,
)
from attendance_teams_bot.observability import current_correlation_id

MAX_MODEL_CALLS = 3
PAGE_SIZE = 50
MAX_CURRENT_PAGES = 200
_CURRENT_STATUS_ORDER = ("office", "remote", "customer_site", "break", "absence", "no_status")
_LOGGER = structlog.get_logger(__name__)


class AuthenticatedMcpSession(Protocol):
    async def list_tools(self) -> tuple[DiscoveredMcpTool, ...]: ...
    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> (
        AttendanceEventPage | ResolvedEmployee | CurrentAttendancePage | EmployeeSuggestionPage
    ): ...


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
        decision = await self.pre_auth_decision(message=message, display_name=display_name)
        if isinstance(decision, BotResponse):
            return decision
        return await self.handle_selected(
            message=message,
            mcp_access_token=mcp_access_token,
            selection=decision,
            display_name=display_name,
        )

    async def pre_auth_decision(
        self, *, message: str, display_name: str | None = None
    ) -> BotResponse | SelectedAttendanceAction:
        """Route without credentials using only bot-owned attendance action definitions."""
        language: ReplyLanguage = "en"
        correlation_id = self.correlation_id_factory()
        try:
            turn = await self.language_model.complete(
                ModelRequest(
                    message,
                    self.reference_date_factory(),
                    "Europe/Ljubljana",
                    tuple(policy.definition for policy in pre_auth_tool_policies()),
                    display_name=display_name,
                    pre_auth_guidance=True,
                )
            )
            if isinstance(turn, FinalResponse):
                if (
                    turn.guidance_kind is None
                    or turn.language not in {"en", "sl"}
                    or not is_safe_model_markdown(turn.markdown)
                ):
                    return self._unavailable_with_outcome(
                        language, display_name, correlation_id, "malformed_final"
                    )
                return BotResponse(text=turn.markdown)
            policy = next(
                (item for item in pre_auth_tool_policies() if item.definition.name == turn.name),
                None,
            )
            if policy is None:
                return self._unavailable_with_outcome(
                    language, display_name, correlation_id, "invalid_tool_selection"
                )
            validated = policy.validate_arguments(turn.arguments)
            if validated is None:
                return self._unavailable_with_outcome(
                    language, display_name, correlation_id, "invalid_tool_selection"
                )
            _, language = validated
            return SelectedAttendanceAction(turn.name, dict(turn.arguments), language)
        except asyncio.CancelledError:
            raise
        except LanguageModelUnavailable as error:
            return self._unavailable_with_outcome(
                language, display_name, correlation_id, "model_unavailable", error
            )
        except Exception as error:
            return self._unavailable_with_outcome(
                language, display_name, correlation_id, "unexpected_exception", error
            )

    async def handle_selected(
        self,
        *,
        message: str,
        mcp_access_token: SecretStr,
        selection: SelectedAttendanceAction,
        display_name: str | None = None,
    ) -> BotResponse:
        """Authenticate, rediscover, and execute a pre-auth selection only if still admitted."""
        return await self._handle_authenticated(
            message=message,
            mcp_access_token=mcp_access_token,
            selection=selection,
            display_name=display_name,
        )

    async def _handle_authenticated(
        self,
        *,
        message: str,
        mcp_access_token: SecretStr,
        selection: SelectedAttendanceAction,
        display_name: str | None = None,
    ) -> BotResponse:
        language = selection.language
        correlation_id = self.correlation_id_factory()
        try:
            async with self.mcp_session_factory.open(
                access_token=mcp_access_token, correlation_id=correlation_id
            ) as session:
                catalog = admit_mcp_catalog(await session.list_tools())
                if catalog is None:
                    return self._unavailable_with_outcome(
                        language, display_name, correlation_id, "catalog_rejected"
                    )
                policy = catalog.selected_tool(selection.name)
                if policy is None:
                    return self._unavailable_with_outcome(
                        language, display_name, correlation_id, "selection_not_admitted"
                    )
                validated = policy.validate_arguments(selection.arguments)
                if validated is None:
                    return self.presenter.present(
                        InvalidRequestPresentation(language, display_name)
                    )
                arguments, language = validated
                result = await self._execute(session, policy, arguments)
                raw_result = result
                final = await self.language_model.complete(
                    ModelRequest(
                        message,
                        self.reference_date_factory(),
                        "Europe/Ljubljana",
                        (),
                        (ToolResultView("execution", policy.definition.name, raw_result),),
                    )
                )
                if (
                    not isinstance(final, FinalResponse)
                    or final.guidance_kind is not None
                    or final.language != language
                    or not final.messages
                    or len(final.messages) > 12
                    or not all(is_safe_model_markdown(item) for item in final.messages)
                    or _mentions_sensitive(final.messages, _sensitive_values(raw_result))
                ):
                    return self._unavailable_with_outcome(
                        language, display_name, correlation_id, "malformed_final"
                    )
                return BotResponse.batch(final.messages)
        except asyncio.CancelledError:
            raise
        except AttendanceToolFailure as error:
            return self.presenter.present(
                ToolFailurePresentation(error.failure.code, language, display_name)
            )
        except LanguageModelUnavailable as error:
            return self._unavailable_with_outcome(
                language,
                display_name,
                correlation_id,
                "model_unavailable",
                error,
            )
        except AttendanceMcpUnavailable, McpContractIncompatible:
            return self._unavailable(language, display_name)
        except Exception as error:
            return self._unavailable_with_outcome(
                language,
                display_name,
                correlation_id,
                "unexpected_exception",
                error,
            )
        return self._unavailable(language, display_name)

    async def _execute(
        self, session: AuthenticatedMcpSession, policy: ToolPolicy, arguments: Mapping[str, object]
    ) -> dict[str, object]:
        if policy.kind == "self":
            return await _all_attendance_pages(session, SELF_ATTENDANCE_TOOL, arguments)
        if policy.kind == "other":
            selector = {
                key: value
                for key, value in arguments.items()
                if key in {"employee_id", "username", "email"}
            }
            resolved = await session.call_tool(name=RESOLVE_EMPLOYEE_TOOL, arguments=selector)
            if not isinstance(resolved, ResolvedEmployee):
                raise McpContractIncompatible
            return await _all_attendance_pages(
                session,
                OTHER_ATTENDANCE_TOOL,
                {
                    "employee_id": resolved.employee_id,
                    "start_date": arguments["start_date"],
                    "end_date": arguments["end_date"],
                },
            )
        statuses = arguments.get("statuses")
        if not isinstance(statuses, tuple) or not all(
            isinstance(status, str) for status in statuses
        ):
            return await _all_current_pages(session, None)
        return await _all_current_pages(session, statuses)

    def _unavailable(self, language: ReplyLanguage, display_name: str | None) -> BotResponse:
        return self.presenter.present(UnavailablePresentation(language, display_name))

    def _unavailable_with_outcome(
        self,
        language: ReplyLanguage,
        display_name: str | None,
        correlation_id: UUID,
        outcome: str,
        error: BaseException | None = None,
    ) -> BotResponse:
        fields: dict[str, str] = {
            "correlation_id": str(correlation_id),
            "outcome": outcome,
        }
        if error is not None:
            fields["error_type"] = type(error).__name__
        _LOGGER.warning("attendance_agent_unavailable", **fields)
        return self._unavailable(language, display_name)


async def _all_attendance_pages(
    session: AuthenticatedMcpSession, tool_name: str, arguments: Mapping[str, object]
) -> dict[str, object]:
    overall_range = OverallAttendanceRange(
        date.fromisoformat(str(arguments["start_date"])),
        date.fromisoformat(str(arguments["end_date"])),
    )
    result = await AttendanceWindowExecutor(
        McpAttendancePageReader(
            session,
            tool_name,
            {key: value for key, value in arguments.items() if key == "employee_id"},
        )
    ).execute(overall_range)
    return {
        "items": [event.model_dump(mode="json") for event in result.events],
        "records_omitted": result.records_omitted,
        "complete": True,
    }


async def _all_current_pages(
    session: AuthenticatedMcpSession, statuses: tuple[str, ...] | None
) -> dict[str, object]:
    items: list[dict[str, object]] = []
    offset = 0
    for _ in range(MAX_CURRENT_PAGES):
        arguments: dict[str, object] = {"limit": PAGE_SIZE, "offset": offset}
        if statuses is not None:
            arguments["statuses"] = list(statuses)
        page = await session.call_tool(name=CURRENT_ATTENDANCE_TOOL, arguments=arguments)
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
            result: dict[str, object] = {"items": items, "complete": True}
            return result
        if page.next_offset <= offset:
            raise McpContractIncompatible
        offset = page.next_offset
    raise McpContractIncompatible


def _current_status_names(result: Mapping[str, object]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Retain a reply-safe status projection helper for callers outside the model path."""
    groups: dict[str, list[str]] = {}
    items = result.get("items")
    if not isinstance(items, list):
        return ()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        status = item.get("status")
        name = item.get("display_name", item.get("employee_name", item.get("name")))
        if not isinstance(name, str):
            first_name, last_name = item.get("first_name"), item.get("last_name")
            if isinstance(first_name, str) and isinstance(last_name, str):
                name = f"{first_name} {last_name}"
        if not isinstance(status, str) or not isinstance(name, str):
            continue
        normalized_name = " ".join(name.split())
        if normalized_name and len(normalized_name) <= 160:
            groups.setdefault(status, []).append(normalized_name)
    return tuple(
        (status, tuple(sorted(set(groups[status]), key=str.casefold)))
        for status in _CURRENT_STATUS_ORDER
        if groups.get(status)
    )


def _sensitive_values(value: object, key: str = "") -> set[str]:
    values: set[str] = set()
    if isinstance(value, Mapping):
        for name, child in value.items():
            values.update(_sensitive_values(child, str(name)))
    elif isinstance(value, list | tuple):
        for child in value:
            values.update(_sensitive_values(child, key))
    elif key.endswith("_id") or key in {"employee_id", "attendance_event_id", "note", "location"}:
        values.add(str(value))
    return values


def _mentions_sensitive(messages: tuple[str, ...], values: set[str]) -> bool:
    return any(value and any(value in message for message in messages) for value in values)
