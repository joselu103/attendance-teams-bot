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

from attendance_teams_bot.agent.contracts import BotResponse, OverallAttendanceRange, ReplyLanguage
from attendance_teams_bot.agent.date_resolver import resolve_attendance_range
from attendance_teams_bot.agent.language_model import (
    LanguageModel,
    LanguageModelUnavailable,
    ModelRequest,
    NoTool,
    ToolDefinition,
)
from attendance_teams_bot.agent.rendering import (
    TOOL_FAILURE_REPLIES,
    UNAVAILABLE_REPLY,
    render_attendance_events,
)
from attendance_teams_bot.mcp.client import (
    AttendanceMcpUnavailable,
    AttendanceToolFailure,
    McpContractIncompatible,
)
from attendance_teams_bot.mcp.contracts import (
    SELF_ATTENDANCE_TOOL,
    AttendanceEvent,
    AttendanceEventPage,
    ListMyAttendanceArguments,
)
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

# The version-1 CRMT inventory is fixed. Advertising one of these tools never
# grants it to the model; this bot selects only the requester-scoped entry below.
_LEGACY_READ_ONLY_TOOL_NAMES = frozenset(
    {
        "list_attendance_events",
        "list_my_attendance_events",
        "get_attendance_event",
        "get_daily_attendance",
        "get_planned_work",
        "get_current_attendance",
        "get_employee_attendance_analysis",
        "get_employee_attendance_summary",
        "get_exceptions",
        "get_organization_attendance_analysis",
        "list_employees",
        "get_employee",
        "list_punch_types",
        "list_locations",
    }
)


class AuthenticatedMcpSession(Protocol):
    async def list_tools(self) -> tuple[ToolDefinition, ...]: ...

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> AttendanceEventPage: ...


class McpSessionFactory(Protocol):
    def open(
        self, *, access_token: SecretStr, correlation_id: UUID
    ) -> AbstractAsyncContextManager[AuthenticatedMcpSession]: ...


@dataclass(frozen=True, slots=True)
class FrozenReadOnlyTool:
    """A bot-owned catalog entry, including its prompt, validation, and safe renderer."""

    definition: ToolDefinition

    def supports_remote(self, remote: ToolDefinition) -> bool:
        return remote.annotations.get(
            "readOnlyHint"
        ) is True and _is_compatible_self_attendance_schema(remote.input_schema)

    def validate_arguments(
        self, arguments: Mapping[str, object]
    ) -> tuple[OverallAttendanceRange, ReplyLanguage] | None:
        if not _has_bounded_arguments(arguments):
            return None
        language = _reply_language(arguments.get("reply_language"))
        if language is None:
            return None
        try:
            request_arguments = {
                key: value for key, value in arguments.items() if key != "reply_language"
            }
            return OverallAttendanceRange(
                date.fromisoformat(str(request_arguments["start_date"])),
                date.fromisoformat(str(request_arguments["end_date"])),
            ), language
        except KeyError, ValueError:
            return None

    async def call(
        self, session: AuthenticatedMcpSession, arguments: ListMyAttendanceArguments, offset: int
    ) -> AttendanceEventPage:
        return await session.call_tool(
            name=self.definition.name,
            arguments={
                "start_date": arguments.start_date.isoformat(),
                "end_date": arguments.end_date.isoformat(),
                "limit": 50,
                "offset": offset,
            },
        )

    def render(
        self,
        events: tuple[AttendanceEvent, ...],
        language: ReplyLanguage,
        *,
        records_omitted: bool,
    ) -> str:
        return render_attendance_events(events, language=language, records_omitted=records_omitted)


def canonical_self_attendance_tool() -> ToolDefinition:
    """Return the bot-owned prompt schema; remote metadata is never prompted."""
    return ToolDefinition(
        name=SELF_ATTENDANCE_TOOL,
        description=(
            "List the authenticated requester's attendance events for an inclusive date range of "
            "no more than 12 rolling calendar months."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
                "reply_language": {"type": "string", "enum": ["en", "sl"]},
            },
            "required": ["start_date", "end_date", "reply_language"],
            "additionalProperties": False,
        },
        annotations={"readOnlyHint": True},
    )


def frozen_read_only_catalog() -> tuple[FrozenReadOnlyTool, ...]:
    """The first-party catalog is intentionally code-owned and exact-match only."""
    return (FrozenReadOnlyTool(definition=canonical_self_attendance_tool()),)


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
                catalog = _catalog_intersection(
                    await session.list_tools(), frozen_read_only_catalog()
                )
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
                policy = catalog[SELF_ATTENDANCE_TOOL]
                if resolution is not None:
                    arguments, language = resolution.range, resolution.language
                else:
                    stage = "model_completion"
                    turn = await self.language_model.complete(
                        ModelRequest(
                            user_message=message,
                            reference_date=reference_date,
                            timezone="Europe/Ljubljana",
                            tools=tuple(policy.definition for policy in catalog.values())
                            + (guidance_tool(),),
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
                    model_policy = catalog.get(turn.name)
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
                events, records_omitted = await _fetch_attendance_events(session, policy, arguments)
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
            policy.render(events, language, records_omitted=records_omitted), language, display_name
        )


async def _fetch_attendance_events(
    session: AuthenticatedMcpSession,
    policy: FrozenReadOnlyTool,
    overall_range: OverallAttendanceRange,
) -> tuple[tuple[AttendanceEvent, ...], bool]:
    """Read each bot-owned window and page; never render a partial failed result."""
    events: list[AttendanceEvent] = []
    records_omitted = False
    for window in overall_range.windows():
        offset = 0
        while True:
            page = await policy.call(
                session,
                ListMyAttendanceArguments(start_date=window.start_date, end_date=window.end_date),
                offset,
            )
            if page.limit != 50 or page.offset != offset or len(page.items) > 50:
                raise McpContractIncompatible
            remaining = 200 - len(events)
            events.extend(page.items[:remaining])
            if len(page.items) > remaining:
                records_omitted = True
            if len(events) == 200:
                return tuple(events), records_omitted or page.next_offset is not None
            if page.next_offset is None:
                break
            offset += 50
    return tuple(events), records_omitted


def _catalog_intersection(
    remote_tools: tuple[ToolDefinition, ...], policies: tuple[FrozenReadOnlyTool, ...]
) -> dict[str, FrozenReadOnlyTool] | None:
    configured = {policy.definition.name: policy for policy in policies}
    remote_by_name = {tool.name: tool for tool in remote_tools}
    if len(configured) != len(policies) or len(remote_by_name) != len(remote_tools):
        return None
    if not remote_by_name.keys() <= _LEGACY_READ_ONLY_TOOL_NAMES:
        return None
    if not all(_is_safe_legacy_read_only_tool(tool) for tool in remote_tools):
        return None
    if not all(policy.supports_remote(remote_by_name[name]) for name, policy in configured.items()):
        return None
    return configured


def _is_safe_legacy_read_only_tool(tool: ToolDefinition) -> bool:
    """Validate unprompted legacy catalog entries before admitting the session."""
    return (
        isinstance(tool.name, str)
        and tool.name in _LEGACY_READ_ONLY_TOOL_NAMES
        and isinstance(tool.description, str)
        and len(tool.description) <= 4_096
        and isinstance(tool.input_schema, Mapping)
        and _is_safe_unprompted_schema(tool.input_schema)
        and tool.annotations.get("readOnlyHint") is True
    )


def _is_safe_unprompted_schema(value: object, depth: int = 0) -> bool:
    """Bound catalog payloads even though only the canonical schema reaches the model."""
    if depth > 16:
        return False
    if value is None or isinstance(value, bool | int | float):
        return True
    if isinstance(value, str):
        return len(value) <= 4_096
    if isinstance(value, Mapping):
        return len(value) <= 256 and all(
            isinstance(key, str) and len(key) <= 256 and _is_safe_unprompted_schema(item, depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, tuple | list):
        return len(value) <= 256 and all(
            _is_safe_unprompted_schema(item, depth + 1) for item in value
        )
    return False


def _has_bounded_arguments(arguments: Mapping[str, object]) -> bool:
    """Keep unusably large model output out of validation and downstream calls."""
    return len(arguments) <= 3 and all(
        isinstance(key, str) and len(key) <= 64 and isinstance(value, str) and len(value) <= 32
        for key, value in arguments.items()
    )


def _is_compatible_self_attendance_schema(schema: Mapping[str, object]) -> bool:
    if schema.get("type") != "object":
        return False
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, Mapping) or not isinstance(required, list):
        return False
    expected_types = {
        "start_date": "string",
        "end_date": "string",
        "limit": "integer",
        "offset": "integer",
    }
    if any(name not in properties for name in expected_types):
        return False
    if any(name not in expected_types for name in required):
        return False
    for name, expected_type in expected_types.items():
        definition = properties[name]
        if not isinstance(definition, Mapping) or definition.get("type") != expected_type:
            return False
    forbidden = {"employee_id", "email", "role", "actor_id", "tenant_id", "object_id"}
    return not forbidden.intersection(properties)


def _duration_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def _reply_language(value: object) -> ReplyLanguage | None:
    if value == "en" or value == "sl":
        return value
    return None


def _guidance_reply(arguments: Mapping[str, object]) -> tuple[str, ReplyLanguage] | None:
    if len(arguments) != 2 or set(arguments) != {"intent", "language"}:
        return None
    intent = arguments.get("intent")
    language = _reply_language(arguments.get("language"))
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
