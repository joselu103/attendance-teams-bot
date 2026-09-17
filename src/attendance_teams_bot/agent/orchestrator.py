from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from time import perf_counter
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import SecretStr

from attendance_teams_bot.agent.attendance_window import (
    AttendanceWindowExecutor,
    McpAttendancePageReader,
)
from attendance_teams_bot.agent.contracts import BotResponse, ReplyLanguage
from attendance_teams_bot.agent.date_resolver import resolve_attendance_range
from attendance_teams_bot.agent.language_model import (
    LanguageModel,
    LanguageModelUnavailable,
    ModelRequest,
    NoTool,
    ToolDefinition,
)
from attendance_teams_bot.agent.mcp_catalog import DiscoveredMcpTool, admit_mcp_catalog
from attendance_teams_bot.agent.rendering import TOOL_FAILURE_REPLIES, UNAVAILABLE_REPLY
from attendance_teams_bot.mcp.client import (
    AttendanceMcpUnavailable,
    AttendanceToolFailure,
    McpContractIncompatible,
)
from attendance_teams_bot.mcp.contracts import AttendanceEventPage
from attendance_teams_bot.observability import (
    authentication_event,
    current_correlation_id,
    get_logger,
    message_input_metadata,
    operation_event,
)

INVALID_REQUEST_REPLY = "Please provide a date range of no more than 12 calendar months."
CLARIFICATION_REPLY = "Please clarify the attendance date range you want to view."
GUIDANCE_TOOL = "respond_with_guidance"
_GUIDANCE_INTENTS = frozenset({"unsupported", "date_ambiguous"})


class AuthenticatedMcpSession(Protocol):
    async def list_tools(self) -> tuple[DiscoveredMcpTool, ...]: ...

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> AttendanceEventPage: ...


class McpSessionFactory(Protocol):
    def open(
        self, *, access_token: SecretStr, correlation_id: UUID
    ) -> AbstractAsyncContextManager[AuthenticatedMcpSession]: ...


def guidance_tool() -> ToolDefinition:
    """Return the bot-only tool used for deterministic, non-attendance guidance."""
    return ToolDefinition(
        name=GUIDANCE_TOOL,
        description=(
            "Respond with safe guidance for an unsupported request or ambiguous date range."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": sorted(_GUIDANCE_INTENTS)},
                "language": {"type": "string", "enum": ["en", "sl"]},
            },
            "required": ["intent", "language"],
            "additionalProperties": False,
        },
        annotations={"readOnlyHint": True},
    )


@dataclass(frozen=True, slots=True)
class AttendanceAgent:
    language_model: LanguageModel
    mcp_session_factory: McpSessionFactory
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
        correlation_id = self.correlation_id_factory()
        started_at = perf_counter()
        logger = get_logger("agent").bind(correlation_id=str(correlation_id))
        input_metadata = message_input_metadata(message)
        operation_event(
            logger,
            event="operation_started",
            handler="AttendanceAgent.handle",
            operation="attendance_orchestration",
            step="mcp_open",
            input_metadata=input_metadata,
        )
        stage = "mcp_open"
        outcome = "success"
        error_code: str | None = None
        error_type: str | None = None
        cancelled = False
        reply_language: ReplyLanguage = "en"
        try:
            async with self.mcp_session_factory.open(
                access_token=mcp_access_token, correlation_id=correlation_id
            ) as session:
                operation_event(
                    logger,
                    event="operation_step_completed",
                    handler="AttendanceAgent.handle",
                    operation="attendance_orchestration",
                    step="mcp_open",
                    duration_ms=_duration_ms(started_at),
                    input_metadata=input_metadata,
                )
                stage = "mcp_catalog"
                catalog = admit_mcp_catalog(await session.list_tools())
                operation_event(
                    logger,
                    event="operation_step_completed",
                    handler="AttendanceAgent.handle",
                    operation="attendance_orchestration",
                    step="mcp_catalog",
                    duration_ms=_duration_ms(started_at),
                    input_metadata=input_metadata,
                )
                if catalog is None:
                    outcome = "catalog_incompatible"
                    return BotResponse(text=UNAVAILABLE_REPLY)
                reference_date = self.reference_date_factory()
                resolution = resolve_attendance_range(message, reference_date=reference_date)
                policy = catalog.requester_attendance_tool
                if resolution is not None:
                    arguments, language = resolution.range, resolution.language
                else:
                    stage = "model_completion"
                    turn = await self.language_model.complete(
                        ModelRequest(
                            user_message=message,
                            reference_date=reference_date,
                            timezone="Europe/Ljubljana",
                            tools=catalog.model_tools + (guidance_tool(),),
                        )
                    )
                    operation_event(
                        logger,
                        event="operation_step_completed",
                        handler="AttendanceAgent.handle",
                        operation="attendance_orchestration",
                        step="model_completion",
                        duration_ms=_duration_ms(started_at),
                        input_metadata=input_metadata,
                    )
                    if isinstance(turn, NoTool):
                        outcome = "clarification"
                        return _response(CLARIFICATION_REPLY, "en", display_name)
                    if turn.name == GUIDANCE_TOOL:
                        guidance = _guidance_reply(turn.arguments)
                        if guidance is None:
                            outcome = "tool_rejected"
                            return _response(UNAVAILABLE_REPLY, "en", display_name)
                        outcome = "guidance"
                        return _response(*guidance, display_name)
                    model_policy = catalog.selected_tool(turn.name)
                    if model_policy is None:
                        outcome = "tool_rejected"
                        return _response(UNAVAILABLE_REPLY, "en", display_name)
                    policy = model_policy
                    stage = "model_validation"
                    validated = policy.validate_arguments(turn.arguments)
                    if validated is None:
                        outcome = "invalid_request"
                        return _response(INVALID_REQUEST_REPLY, "en", display_name)
                    arguments, language = validated
                reply_language = language
                stage = "mcp_tool_call"
                result = await AttendanceWindowExecutor(
                    McpAttendancePageReader(session, policy.definition.name)
                ).execute(arguments)
                operation_event(
                    logger,
                    event="operation_step_completed",
                    handler="AttendanceAgent.handle",
                    operation="attendance_orchestration",
                    step="mcp_tool_call",
                    duration_ms=_duration_ms(started_at),
                    input_metadata=input_metadata,
                )
        except asyncio.CancelledError:
            cancelled = True
            raise
        except AttendanceToolFailure as error:
            outcome = "tool_failure"
            error_code = error.failure.code
            error_type = type(error).__name__
            if error.failure.code in {"FORBIDDEN", "IDENTITY_UNMAPPED", "IDENTITY_AMBIGUOUS"}:
                authentication_event(
                    logger,
                    event="permission_denied",
                    scheme="attendance_mcp",
                    failure_reason=error.failure.code,
                )
            elif error.failure.code in {"AUTHENTICATION_REQUIRED", "TOKEN_INVALID"}:
                authentication_event(
                    logger,
                    event="auth_failed",
                    scheme="attendance_mcp",
                    failure_reason=error.failure.code,
                )
            return _response(
                _localized_safe_reply(
                    TOOL_FAILURE_REPLIES.get(error.failure.code, UNAVAILABLE_REPLY), reply_language
                ),
                reply_language,
                display_name,
            )
        except (
            AttendanceMcpUnavailable,
            McpContractIncompatible,
            LanguageModelUnavailable,
        ) as error:
            outcome = "dependency_unavailable"
            error_type = type(error).__name__
            return _response(
                _localized_safe_reply(UNAVAILABLE_REPLY, reply_language),
                reply_language,
                display_name,
            )
        except Exception as error:
            outcome = "unexpected_failure"
            error_type = type(error).__name__
            return _response(
                _localized_safe_reply(UNAVAILABLE_REPLY, reply_language),
                reply_language,
                display_name,
            )
        finally:
            if not cancelled:
                operation_event(
                    logger,
                    event="operation_succeeded" if outcome == "success" else "operation_failed",
                    handler="AttendanceAgent.handle",
                    operation="attendance_orchestration",
                    step="rendering" if outcome == "success" else stage,
                    duration_ms=_duration_ms(started_at),
                    error_type=error_type,
                    input_metadata={**input_metadata, "outcome": outcome, "error_code": error_code},
                )
        return _response(
            policy.render(result.events, language, records_omitted=result.records_omitted),
            language,
            display_name,
        )


def _duration_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def _guidance_reply(arguments: Mapping[str, object]) -> tuple[str, ReplyLanguage] | None:
    if len(arguments) != 2 or set(arguments) != {"intent", "language"}:
        return None
    intent = arguments.get("intent")
    language = _guidance_reply_language(arguments.get("language"))
    if intent not in _GUIDANCE_INTENTS or language is None:
        return None
    if intent == "date_ambiguous":
        return (
            (
                "Prosimo, pojasnite obdobje prisotnosti, ki ga želite prikazati."
                if language == "sl"
                else CLARIFICATION_REPLY
            ),
            language,
        )
    return (
        (
            "Lahko vam prikažem dogodke vaše prisotnosti za določeno obdobje."
            if language == "sl"
            else "I can show your attendance events for a specific date range."
        ),
        language,
    )


def _guidance_reply_language(value: object) -> ReplyLanguage | None:
    if value == "en" or value == "sl":
        return value
    return None


def _response(text: str, language: ReplyLanguage, display_name: str | None) -> BotResponse:
    name = _safe_display_name(display_name)
    if name:
        greeting = "Pozdravljeni" if language == "sl" else "Hello"
        text = f"{greeting}, {name}!\n\n{text}"
    return BotResponse(text=text)


def _safe_display_name(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())[:80]
    if not normalized:
        return None
    replacements: dict[str, str | int | None] = {
        character: f"\\{character}" for character in r"\\`*_{}[]<>#()+-.!|"
    }
    return normalized.translate(str.maketrans(replacements))


def _localized_safe_reply(text: str, language: ReplyLanguage) -> str:
    if language == "en":
        return text
    slovene = {
        UNAVAILABLE_REPLY: "Podatki o prisotnosti trenutno niso na voljo. Poskusite znova pozneje.",
        INVALID_REQUEST_REPLY: "Prosimo, navedite obdobje največ 31 dni.",
        TOOL_FAILURE_REPLIES[
            "INVALID_ARGUMENT"
        ]: "Preverite obdobje prisotnosti in poskusite znova.",
        TOOL_FAILURE_REPLIES["FORBIDDEN"]: "Nimate dovoljenja za ogled te prisotnosti.",
        TOOL_FAILURE_REPLIES["IDENTITY_UNMAPPED"]: (
            "Vaš račun Teams ni povezan z aktivnim zaposlenim za evidenco prisotnosti. "
            "Obrnite se na skrbnika."
        ),
        TOOL_FAILURE_REPLIES["IDENTITY_AMBIGUOUS"]: (
            "Vašega računa Teams ni mogoče varno povezati. Obrnite se na skrbnika."
        ),
        TOOL_FAILURE_REPLIES["AUTHENTICATION_REQUIRED"]: "Prijavite se in poskusite znova.",
        TOOL_FAILURE_REPLIES["TOKEN_INVALID"]: "Prijavite se in poskusite znova.",
    }
    return slovene.get(text, slovene[UNAVAILABLE_REPLY])
