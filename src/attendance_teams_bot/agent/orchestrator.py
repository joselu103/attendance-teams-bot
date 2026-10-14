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

from attendance_teams_bot.agent.continuation import HistoryContinuation
from attendance_teams_bot.agent.contracts import AttendanceNeeded, BotResponse
from attendance_teams_bot.agent.history_page import (
    AttendanceHistoryExchange,
    HistoryPageRequest,
    HistoryPageSession,
)
from attendance_teams_bot.agent.language_model import (
    FinalResponse,
    LanguageModel,
    LanguageModelUnavailable,
    ReplyLanguage,
    ToolCall,
)
from attendance_teams_bot.agent.mcp_catalog import DiscoveredMcpTool, ToolPolicy, admit_mcp_catalog
from attendance_teams_bot.agent.model_contract import (
    delivery_memory,
    guidance_memory,
    pre_auth_request,
    selection_request,
)
from attendance_teams_bot.agent.rendering import (
    AttendanceResultPresenter,
    CurrentAttendancePresentation,
    EmployeeCandidatesPresentation,
    GuidanceIntent,
    GuidancePresentation,
    InvalidRequestPresentation,
    ToolFailurePresentation,
    UnavailablePresentation,
)
from attendance_teams_bot.mcp.client import (
    AttendanceMcpUnavailable,
    AttendanceToolFailure,
    McpContractIncompatible,
)
from attendance_teams_bot.mcp.contracts import (
    CURRENT_WORK_STATUS_TOOL,
    RESOLVE_EMPLOYEE_TOOL,
    SEARCH_EMPLOYEES_TOOL,
    SELF_ATTENDANCE_TOOL,
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


class AuthenticatedMcpSession(HistoryPageSession, Protocol):
    async def list_tools(self) -> tuple[DiscoveredMcpTool, ...]: ...


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
    history_exchange: AttendanceHistoryExchange = field(default_factory=AttendanceHistoryExchange)
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
        reference_date = self.reference_date_factory()
        decision = await self._pre_auth_decision(
            message=message,
            display_name=display_name,
            history=history,
            reference_date=reference_date,
        )
        if isinstance(decision, BotResponse):
            return decision
        return await self._handle_authenticated(
            message=message,
            mcp_access_token=mcp_access_token,
            selection=decision,
            display_name=display_name,
            history=history,
            reference_date=reference_date,
        )

    async def pre_auth_decision(
        self,
        *,
        message: str,
        display_name: str | None = None,
        history: tuple[ChatMessage, ...] = (),
    ) -> BotResponse | AttendanceNeeded:
        """Route without credentials using no tools and no attendance capability metadata."""
        return await self._pre_auth_decision(
            message=message,
            display_name=display_name,
            history=history,
            reference_date=self.reference_date_factory(),
        )

    async def _pre_auth_decision(
        self,
        *,
        message: str,
        display_name: str | None,
        history: tuple[ChatMessage, ...],
        reference_date: date,
    ) -> BotResponse | AttendanceNeeded:
        language: ReplyLanguage = "en"
        correlation_id = self.correlation_id_factory()
        try:
            turn = await self.language_model.complete(
                pre_auth_request(message=message, reference_date=reference_date, history=history)
            )
            _LOGGER.debug("PRE-AUTH LLM RESPONSE", turn=turn)
            if isinstance(turn, FinalResponse):
                if turn.guidance_kind is None or turn.language not in {"en", "sl"}:
                    return self._unavailable_with_outcome(
                        language, display_name, correlation_id, "malformed_final"
                    )
                if turn.guidance_kind == "attendance_needed":
                    return AttendanceNeeded(turn.language)
                # Pre-auth guidance is rendered from a fixed, bot-owned presentation;
                # model Markdown is deliberately not sent to Teams in this branch.
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
                    response.text, response.messages, guidance_memory(intent, turn.language)
                )
            return self._unavailable_with_outcome(
                language, display_name, correlation_id, "pre_auth_tool_call"
            )
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
        selection: AttendanceNeeded,
        display_name: str | None = None,
        history: tuple[ChatMessage, ...] = (),
    ) -> BotResponse:
        """Authenticate, discover, select, and execute exactly one admitted action."""
        return await self._handle_authenticated(
            message=message,
            mcp_access_token=mcp_access_token,
            selection=selection,
            display_name=display_name,
            history=history,
            reference_date=self.reference_date_factory(),
        )

    async def _handle_authenticated(
        self,
        *,
        message: str,
        mcp_access_token: SecretStr,
        selection: AttendanceNeeded,
        display_name: str | None = None,
        history: tuple[ChatMessage, ...] = (),
        reference_date: date,
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
                turn = await self.language_model.complete(
                    selection_request(
                        message=message,
                        reference_date=reference_date,
                        history=history,
                        tools=catalog.model_tools,
                    )
                )
                _LOGGER.debug(
                    "authenticated_llm_response",
                    turn=turn,
                )
                if not isinstance(turn, ToolCall):
                    return self._unavailable_with_outcome(
                        language, display_name, correlation_id, "missing_tool_selection"
                    )
                _LOGGER.debug(
                    "authenticated_tool_selection_received",
                    tool_name=turn.name,
                    argument_count=len(turn.arguments),
                )
                policy = catalog.selected_tool(turn.name)
                if policy is None:
                    return self._unavailable_with_outcome(
                        language, display_name, correlation_id, "selection_not_admitted"
                    )
                _LOGGER.debug(
                    "authenticated_tool_selected",
                    tool_name=policy.definition.name,
                    tool_kind=policy.kind,
                )
                validated = policy.validate_arguments(turn.arguments)
                _LOGGER.debug("validated_arguments", validated=validated)
                if validated is None:
                    return self.presenter.present(
                        InvalidRequestPresentation(language, display_name)
                    )
                arguments, _ = validated
                _LOGGER.debug(
                    "authenticated_tool_arguments_validated",
                    tool_name=policy.definition.name,
                    argument_count=len(arguments),
                )
                if policy.kind in {"self", "other"}:
                    request = await self._history_page_request(session, policy, arguments, language)
                    return await self.history_exchange.execute(session, request)
                result = await self._execute(session, policy, arguments)
                if isinstance(result, EmployeeSuggestionPage):
                    return self.presenter.present(
                        EmployeeCandidatesPresentation(result.items, language, display_name)
                    )
                status_names = _current_status_names(result)
                rendering = CurrentAttendancePresentation(status_names, language, display_name)
                response = self.presenter.present(rendering)
                return BotResponse(
                    response.text,
                    response.messages,
                    delivery_memory(False),
                    attachments=response.attachments,
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
                return await self.history_exchange.execute(
                    session, HistoryPageRequest.from_continuation(query)
                )
        except asyncio.CancelledError:
            raise
        except AttendanceToolFailure as error:
            return self.presenter.present(
                ToolFailurePresentation(error.failure.code, language, display_name)
            )
        except Exception:
            return self._unavailable(language, display_name)

    async def _history_page_request(
        self,
        session: AuthenticatedMcpSession,
        policy: ToolPolicy,
        arguments: Mapping[str, object],
        language: ReplyLanguage,
    ) -> HistoryPageRequest:
        start_date = date.fromisoformat(str(arguments["start_date"]))
        end_date = date.fromisoformat(str(arguments["end_date"]))
        if policy.kind == "self":
            return HistoryPageRequest("self", start_date, end_date, 0, language)
        selector = {
            key: value
            for key, value in arguments.items()
            if key in {"employee_id", "username", "email"}
        }
        resolved = await session.call_tool(name=RESOLVE_EMPLOYEE_TOOL, arguments=selector)
        if not isinstance(resolved, ResolvedEmployee):
            raise McpContractIncompatible
        return HistoryPageRequest("admin", start_date, end_date, 0, language, resolved.employee_id)

    async def _execute(
        self, session: AuthenticatedMcpSession, policy: ToolPolicy, arguments: Mapping[str, object]
    ) -> dict[str, object] | EmployeeSuggestionPage:
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
