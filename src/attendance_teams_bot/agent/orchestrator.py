from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol, cast
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
from attendance_teams_bot.agent.rendering import (
    AttendanceResultPresenter,
    CatalogUnavailablePresentation,
    ClarificationPresentation,
    EventResultPresentation,
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
from attendance_teams_bot.mcp.contracts import AttendanceEventPage
from attendance_teams_bot.observability import (
    OperationLifecycle,
    authentication_event,
    current_correlation_id,
    get_logger,
    message_input_metadata,
)

GUIDANCE_TOOL = "respond_with_guidance"
_GUIDANCE_INTENTS = frozenset({"unsupported", "date_ambiguous"})


class AuthenticatedMcpSession(Protocol):
    """Expose discovery and tool execution within one authenticated MCP session."""

    async def list_tools(self) -> tuple[DiscoveredMcpTool, ...]: ...

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> AttendanceEventPage: ...


class McpSessionFactory(Protocol):
    """Open authenticated MCP sessions bound to one correlation identifier."""

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
    """Coordinate one requester-scoped attendance query across model and MCP boundaries.

    Attendance data is rendered deterministically after MCP execution; it is not sent back to
    the language model.
    """

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
        """Handle one authenticated message, failing closed on unsafe dependencies or output."""
        correlation_id = self.correlation_id_factory()
        logger = get_logger("agent").bind(correlation_id=str(correlation_id))
        input_metadata = message_input_metadata(message)
        lifecycle = OperationLifecycle(
            logger,
            handler="AttendanceAgent.handle",
            operation="attendance_orchestration",
            input_metadata=input_metadata,
        )
        lifecycle.start(step="mcp_open")
        stage = "mcp_open"
        outcome = "success"
        error_code: str | None = None
        terminal_error: Exception | None = None
        cancelled = False
        reply_language: ReplyLanguage = "en"
        try:
            async with self.mcp_session_factory.open(
                access_token=mcp_access_token, correlation_id=correlation_id
            ) as session:
                lifecycle.step_completed()
                stage = "mcp_catalog"
                catalog = admit_mcp_catalog(await session.list_tools())
                lifecycle.step_completed(step=stage)
                if catalog is None:
                    outcome = "catalog_incompatible"
                    return self.presenter.present(
                        CatalogUnavailablePresentation(language="en", display_name=display_name)
                    )
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
                    lifecycle.step_completed(step=stage)
                    if isinstance(turn, NoTool):
                        outcome = "clarification"
                        return self.presenter.present(
                            ClarificationPresentation(language="en", display_name=display_name)
                        )
                    if turn.name == GUIDANCE_TOOL:
                        guidance = _guidance_intent(turn.arguments)
                        if guidance is None:
                            outcome = "tool_rejected"
                            return self.presenter.present(
                                UnavailablePresentation(language="en", display_name=display_name)
                            )
                        outcome = "guidance"
                        intent, language = guidance
                        return self.presenter.present(
                            GuidancePresentation(
                                intent=intent, language=language, display_name=display_name
                            )
                        )
                    model_policy = catalog.selected_tool(turn.name)
                    if model_policy is None:
                        outcome = "tool_rejected"
                        return self.presenter.present(
                            UnavailablePresentation(language="en", display_name=display_name)
                        )
                    policy = model_policy
                    stage = "model_validation"
                    validated = policy.validate_arguments(turn.arguments)
                    if validated is None:
                        outcome = "invalid_request"
                        return self.presenter.present(
                            InvalidRequestPresentation(language="en", display_name=display_name)
                        )
                    arguments, language = validated
                reply_language = language
                stage = "mcp_tool_call"
                result = await AttendanceWindowExecutor(
                    McpAttendancePageReader(session, policy.definition.name)
                ).execute(arguments)
                lifecycle.step_completed(step=stage)
        except asyncio.CancelledError:
            cancelled = True
            lifecycle.cancel(step=stage)
            raise
        except AttendanceToolFailure as error:
            outcome = "tool_failure"
            error_code = error.failure.code
            terminal_error = error
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
            return self.presenter.present(
                ToolFailurePresentation(
                    code=error.failure.code,
                    language=reply_language,
                    display_name=display_name,
                )
            )
        except (
            AttendanceMcpUnavailable,
            McpContractIncompatible,
            LanguageModelUnavailable,
        ) as error:
            outcome = "dependency_unavailable"
            terminal_error = error
            return self.presenter.present(
                UnavailablePresentation(language=reply_language, display_name=display_name)
            )
        except Exception as error:
            outcome = "unexpected_failure"
            terminal_error = error
            return self.presenter.present(
                UnavailablePresentation(language=reply_language, display_name=display_name)
            )
        finally:
            if not cancelled:
                terminal_metadata = {"outcome": outcome, "error_code": error_code}
                if outcome == "success":
                    lifecycle.succeed(step="rendering", input_metadata=terminal_metadata)
                else:
                    lifecycle.fail(
                        terminal_error,
                        step=stage,
                        input_metadata=terminal_metadata,
                    )
        return self.presenter.present(
            EventResultPresentation(
                events=result.events,
                records_omitted=result.records_omitted,
                language=language,
                display_name=display_name,
            )
        )


def _guidance_intent(
    arguments: Mapping[str, object],
) -> tuple[GuidanceIntent, ReplyLanguage] | None:
    if len(arguments) != 2 or set(arguments) != {"intent", "language"}:
        return None
    intent = arguments.get("intent")
    language = _guidance_reply_language(arguments.get("language"))
    if intent not in _GUIDANCE_INTENTS or language is None:
        return None
    return cast(GuidanceIntent, intent), language


def _guidance_reply_language(value: object) -> ReplyLanguage | None:
    if value == "en" or value == "sl":
        return value
    return None
