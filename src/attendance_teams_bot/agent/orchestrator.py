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

from attendance_teams_bot.agent.continuation import (
    HistoryContinuation,
)
from attendance_teams_bot.agent.contracts import BotResponse, SelectedAttendanceAction
from attendance_teams_bot.agent.date_resolver import has_ambiguous_numeric_date
from attendance_teams_bot.agent.language_model import (
    FinalResponse,
    LanguageModel,
    LanguageModelUnavailable,
    ModelRequest,
    PresentationPlan,
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
    CurrentAttendancePresentation,
    EmployeeCandidatesPresentation,
    GuidanceIntent,
    GuidancePresentation,
    InvalidRequestPresentation,
    SafeHistoryPresentation,
    ToolFailurePresentation,
    UnavailablePresentation,
    is_safe_model_markdown,
    valid_presentation_plan,
)
from attendance_teams_bot.mcp.client import (
    AttendanceMcpUnavailable,
    AttendanceToolFailure,
    McpContractIncompatible,
)
from attendance_teams_bot.mcp.contracts import (
    CURRENT_WORK_STATUS_TOOL,
    OTHER_ATTENDANCE_TOOL,
    RESOLVE_EMPLOYEE_TOOL,
    SEARCH_EMPLOYEES_TOOL,
    SELF_ATTENDANCE_TOOL,
    AttendanceEvent,
    AttendanceEventPage,
    CurrentWorkStatusPage,
    EmployeeSuggestionPage,
    ResolvedEmployee,
)
from attendance_teams_bot.memory.models import ChatMessage
from attendance_teams_bot.observability import current_correlation_id

PAGE_SIZE = 50
MAX_CURRENT_PAGES = 200
_CURRENT_STATUS_ORDER = ("office", "remote", "customer_site", "break", "absence", "no_status")
_LOGGER = structlog.get_logger(__name__)


def _guidance_memory(intent: GuidanceIntent, language: ReplyLanguage) -> str:
    english = {
        "greeting": "I can help with attendance.",
        "date_ambiguous": "Please clarify the attendance date range you want to view.",
        "unsupported": "I can help with attendance requests.",
    }
    slovene = {
        "greeting": "Pomagam lahko pri prisotnosti.",
        "date_ambiguous": "Navedite obdobje prisotnosti.",
        "unsupported": "Pomagam lahko pri zahtevah glede prisotnosti.",
    }
    return (slovene if language == "sl" else english)[intent]


def _plan_memory(plan: PresentationPlan, has_continuation: bool) -> str:
    del plan
    return (
        "Attendance response delivered. A history continuation is available."
        if has_continuation
        else "Attendance response delivered."
    )


class AuthenticatedMcpSession(Protocol):
    async def list_tools(self) -> tuple[DiscoveredMcpTool, ...]: ...
    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> (
        AttendanceEventPage | ResolvedEmployee | CurrentWorkStatusPage | EmployeeSuggestionPage
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
        self,
        *,
        message: str,
        mcp_access_token: SecretStr,
        display_name: str | None = None,
        history: tuple[ChatMessage, ...] = (),
    ) -> BotResponse:
        decision = await self.pre_auth_decision(
            message=message, display_name=display_name, history=history
        )
        if isinstance(decision, BotResponse):
            return decision
        return await self.handle_selected(
            message=message,
            mcp_access_token=mcp_access_token,
            selection=decision,
            display_name=display_name,
            history=history,
        )

    async def pre_auth_decision(
        self,
        *,
        message: str,
        display_name: str | None = None,
        history: tuple[ChatMessage, ...] = (),
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
                    history=history,
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
                if turn.guidance_kind == "greeting":
                    intent: GuidanceIntent = "greeting"
                elif turn.guidance_kind == "attendance_clarification":
                    intent = "date_ambiguous"
                else:
                    intent = "unsupported"
                response = self.presenter.present(
                    GuidancePresentation(intent, turn.language, display_name)
                )
                return BotResponse(
                    response.text, response.messages, _guidance_memory(intent, turn.language)
                )
            policy = next(
                (item for item in pre_auth_tool_policies() if item.definition.name == turn.name),
                None,
            )
            if policy is None:
                return self._unavailable_with_outcome(
                    language, display_name, correlation_id, "invalid_tool_selection"
                )
            validated = policy.validate_arguments(turn.arguments)
            if policy.kind in {"self", "other"}:
                date_values = {
                    key: turn.arguments[key]
                    for key in ("start_date", "end_date", "reply_language")
                    if key in turn.arguments
                }
                date_policy = pre_auth_tool_policies()[0]
                if date_policy.validate_arguments(
                    date_values
                ) is None or has_ambiguous_numeric_date(message):
                    raw_language = turn.arguments.get("reply_language")
                    if raw_language not in ("en", "sl"):
                        return self._unavailable(language, display_name)
                    return self.presenter.present(
                        GuidancePresentation("date_ambiguous", raw_language, display_name)
                    )
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
        history: tuple[ChatMessage, ...] = (),
    ) -> BotResponse:
        """Authenticate, rediscover, and execute a pre-auth selection only if still admitted."""
        return await self._handle_authenticated(
            message=message,
            mcp_access_token=mcp_access_token,
            selection=selection,
            display_name=display_name,
            history=history,
        )

    async def _handle_authenticated(
        self,
        *,
        message: str,
        mcp_access_token: SecretStr,
        selection: SelectedAttendanceAction,
        display_name: str | None = None,
        history: tuple[ChatMessage, ...] = (),
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
                if isinstance(result, EmployeeSuggestionPage):
                    return self.presenter.present(
                        EmployeeCandidatesPresentation(result.items, language, display_name)
                    )
                if policy.kind in {"self", "other"}:
                    raw_items = result.get("items")
                    if not isinstance(raw_items, list):
                        raise McpContractIncompatible
                    events = tuple(AttendanceEvent.model_validate(item) for item in raw_items)
                    projection = _history_projection(events, language)
                    rendering: SafeHistoryPresentation | CurrentAttendancePresentation = (
                        SafeHistoryPresentation(
                            events,
                            language,
                            display_name,
                            None,
                            date.fromisoformat(str(result["start_date"])),
                            date.fromisoformat(str(result["end_date"])),
                            result.get("next_offset") is not None,
                        )
                    )
                else:
                    status_names = _current_status_names(result)
                    projection = _current_projection(status_names, language)
                    rendering = CurrentAttendancePresentation(
                        status_names, language, display_name, None
                    )
                final = await self.language_model.complete(
                    ModelRequest(
                        message,
                        self.reference_date_factory(),
                        "Europe/Ljubljana",
                        (),
                        (ToolResultView("execution", policy.definition.name, projection),),
                        history=history,
                    )
                )
                if (
                    not isinstance(final, FinalResponse)
                    or final.guidance_kind is not None
                    or final.language != language
                    or not is_safe_model_markdown(final.markdown)
                    or final.presentation is None
                    or not valid_presentation_plan(final.presentation)
                ):
                    return self._unavailable_with_outcome(
                        language, display_name, correlation_id, "malformed_final"
                    )
                if isinstance(rendering, SafeHistoryPresentation):
                    rendering = SafeHistoryPresentation(
                        rendering.events,
                        language,
                        display_name,
                        final.presentation,
                        rendering.start_date,
                        rendering.end_date,
                        rendering.has_more,
                    )
                else:
                    rendering = CurrentAttendancePresentation(
                        rendering.status_names, language, display_name, final.presentation
                    )
                response = self.presenter.present(rendering)
                continuation = self._continuation(result, language, policy.kind)
                return BotResponse(
                    response.text,
                    response.messages,
                    _plan_memory(final.presentation, continuation is not None),
                    attachments=response.attachments,
                    continuation=continuation,
                    continuation_complete=isinstance(rendering, SafeHistoryPresentation),
                )
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

    async def handle_continuation(
        self,
        *,
        query: HistoryContinuation,
        mcp_access_token: SecretStr,
        display_name: str | None = None,
    ) -> BotResponse:
        """Re-admit the live catalog and read exactly the signed position without a model."""
        language = query.language
        try:
            async with self.mcp_session_factory.open(
                access_token=mcp_access_token, correlation_id=self.correlation_id_factory()
            ) as session:
                catalog = admit_mcp_catalog(await session.list_tools())
                name = SELF_ATTENDANCE_TOOL if query.scope == "self" else "get_other_attendance"
                if catalog is None or catalog.selected_tool(name) is None:
                    return self._unavailable(language, display_name)
                arguments: dict[str, object] = {
                    "start_date": query.start_date.isoformat(),
                    "end_date": query.end_date.isoformat(),
                }
                if query.scope == "admin":
                    arguments["employee_id"] = query.target
                result = await _attendance_page(
                    session,
                    SELF_ATTENDANCE_TOOL if query.scope == "self" else OTHER_ATTENDANCE_TOOL,
                    arguments,
                    query.offset,
                )
                raw_items = result["items"]
                if not isinstance(raw_items, list):
                    raise McpContractIncompatible
                events = tuple(AttendanceEvent.model_validate(item) for item in raw_items)
                response = self.presenter.present(
                    SafeHistoryPresentation(
                        events,
                        language,
                        display_name,
                        None,
                        query.start_date,
                        query.end_date,
                        result["next_offset"] is not None,
                    )
                )
                continuation = self._continuation(
                    result, language, "self" if query.scope == "self" else "other"
                )
                return BotResponse(
                    response.text,
                    response.messages,
                    "Attendance response delivered. A history continuation is available."
                    if continuation is not None
                    else "Attendance response delivered.",
                    continuation=continuation,
                    continuation_complete=True,
                )
        except asyncio.CancelledError:
            raise
        except AttendanceToolFailure as error:
            return self.presenter.present(
                ToolFailurePresentation(error.failure.code, language, display_name)
            )
        except Exception:
            return self._unavailable(language, display_name)

    def _continuation(
        self, result: Mapping[str, object], language: ReplyLanguage, kind: str
    ) -> HistoryContinuation | None:
        offset = result.get("next_offset")
        if offset is None:
            return None
        if type(offset) is not int:
            return None
        target = result.get("employee_id")
        query = HistoryContinuation(
            "self" if kind == "self" else "admin",
            date.fromisoformat(str(result["start_date"])),
            date.fromisoformat(str(result["end_date"])),
            offset,
            language,
            target if type(target) is int else None,
        )
        return query

    async def _execute(
        self, session: AuthenticatedMcpSession, policy: ToolPolicy, arguments: Mapping[str, object]
    ) -> dict[str, object] | EmployeeSuggestionPage:
        if policy.kind == "self":
            return await _attendance_page(session, SELF_ATTENDANCE_TOOL, arguments)
        if policy.kind == "other":
            selector = {
                key: value
                for key, value in arguments.items()
                if key in {"employee_id", "username", "email"}
            }
            resolved = await session.call_tool(name=RESOLVE_EMPLOYEE_TOOL, arguments=selector)
            if not isinstance(resolved, ResolvedEmployee):
                raise McpContractIncompatible
            return await _attendance_page(
                session,
                OTHER_ATTENDANCE_TOOL,
                {
                    "employee_id": resolved.employee_id,
                    "start_date": arguments["start_date"],
                    "end_date": arguments["end_date"],
                },
            )
        if policy.kind == "search":
            result = await session.call_tool(name=SEARCH_EMPLOYEES_TOOL, arguments=arguments)
            if not isinstance(result, EmployeeSuggestionPage):
                raise McpContractIncompatible
            return result
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


async def _attendance_page(
    session: AuthenticatedMcpSession,
    tool_name: str,
    arguments: Mapping[str, object],
    offset: int = 0,
) -> dict[str, object]:
    page = await session.call_tool(
        name=tool_name, arguments={**arguments, "limit": 50, "offset": offset}
    )
    if (
        not isinstance(page, AttendanceEventPage)
        or type(page.limit) is not int
        or type(page.offset) is not int
        or page.limit != 50
        or page.offset != offset
        or len(page.items) > 50
        or (
            page.next_offset is not None
            and (type(page.next_offset) is not int or page.next_offset <= offset or not page.items)
        )
    ):
        raise McpContractIncompatible
    return {
        "items": [event.model_dump(mode="json") for event in page.items],
        "next_offset": page.next_offset,
        **arguments,
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
        page = await session.call_tool(name=CURRENT_WORK_STATUS_TOOL, arguments=arguments)
        if (
            not isinstance(page, CurrentWorkStatusPage)
            or page.limit != PAGE_SIZE
            or page.offset != offset
            or len(page.items) > PAGE_SIZE
        ):
            raise McpContractIncompatible
        items.extend(item.model_dump() for item in page.items)
        if page.next_offset is None:
            result: dict[str, object] = {"items": items, "complete": True}
            return result
        if page.next_offset <= offset:
            raise McpContractIncompatible
        offset = page.next_offset
    raise McpContractIncompatible


def _history_projection(
    events: tuple[AttendanceEvent, ...], language: ReplyLanguage
) -> dict[str, object]:
    """Send the model only non-sensitive shape metadata, never source records."""
    dates: set[str] = set()
    for event in events:
        timestamp = event.checked_in_at or event.checked_out_at
        if timestamp is not None:
            dates.add(timestamp.date().isoformat())
    return {
        "kind": "attendance_history",
        "language": language,
        "date_groups": sorted(dates),
        "has_records": bool(events),
    }


def _current_status_names(result: Mapping[str, object]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    groups: dict[str, list[str]] = {}
    items = result.get("items")
    if not isinstance(items, list):
        return ()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        status = item.get("status")
        first_name, last_name = item.get("first_name"), item.get("last_name")
        if isinstance(first_name, str) and isinstance(last_name, str):
            name = f"{first_name} {last_name}"
        else:
            continue
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


def _current_projection(
    status_names: tuple[tuple[str, tuple[str, ...]], ...], language: ReplyLanguage
) -> dict[str, object]:
    return {
        "kind": "current_attendance",
        "language": language,
        "statuses": [{"status": status, "count": len(names)} for status, names in status_names],
    }


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
