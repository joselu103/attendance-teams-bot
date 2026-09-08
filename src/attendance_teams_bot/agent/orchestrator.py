from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from time import perf_counter
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import SecretStr, ValidationError

from attendance_teams_bot.agent.contracts import BotResponse
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
    render_attendance_page,
)
from attendance_teams_bot.mcp.client import (
    AttendanceMcpUnavailable,
    AttendanceToolFailure,
    McpContractIncompatible,
)
from attendance_teams_bot.mcp.contracts import (
    SELF_ATTENDANCE_TOOL,
    AttendanceEventPage,
    ListMyAttendanceArguments,
)
from attendance_teams_bot.observability import current_correlation_id, get_logger, log_event

INVALID_REQUEST_REPLY = "Please provide a date range of no more than 31 days."
CLARIFICATION_REPLY = "Please ask for your attendance and include a date range."


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
    ) -> ListMyAttendanceArguments | None:
        if not _has_bounded_arguments(arguments):
            return None
        try:
            return ListMyAttendanceArguments.model_validate(dict(arguments))
        except ValidationError:
            return None

    async def call(
        self, session: AuthenticatedMcpSession, arguments: ListMyAttendanceArguments
    ) -> AttendanceEventPage:
        return await session.call_tool(
            name=self.definition.name,
            arguments={
                "start_date": arguments.start_date.isoformat(),
                "end_date": arguments.end_date.isoformat(),
                "limit": 50,
                "offset": 0,
            },
        )

    def render(self, page: AttendanceEventPage) -> str:
        return render_attendance_page(page)


def canonical_self_attendance_tool() -> ToolDefinition:
    """Return the bot-owned prompt schema; remote metadata is never prompted."""
    return ToolDefinition(
        name=SELF_ATTENDANCE_TOOL,
        description=(
            "List the authenticated requester's attendance events for an inclusive date range."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
            },
            "required": ["start_date", "end_date"],
            "additionalProperties": False,
        },
        annotations={"readOnlyHint": True},
    )


def frozen_read_only_catalog() -> tuple[FrozenReadOnlyTool, ...]:
    """The first-party catalog is intentionally code-owned and exact-match only."""
    return (FrozenReadOnlyTool(definition=canonical_self_attendance_tool()),)


@dataclass(frozen=True, slots=True)
class AttendanceAgent:
    language_model: LanguageModel
    mcp_session_factory: McpSessionFactory
    correlation_id_factory: Callable[[], UUID] = current_correlation_id
    reference_date_factory: Callable[[], date] = lambda: datetime.now(
        ZoneInfo("Europe/Ljubljana")
    ).date()

    async def handle(self, *, message: str, mcp_access_token: SecretStr) -> BotResponse:
        correlation_id = self.correlation_id_factory()
        started_at = perf_counter()
        stage = "mcp_open"
        outcome = "success"
        error_code: str | None = None
        error_type: str | None = None
        cancelled = False
        try:
            async with self.mcp_session_factory.open(
                access_token=mcp_access_token, correlation_id=correlation_id
            ) as session:
                stage = "mcp_catalog"
                catalog = _catalog_intersection(
                    await session.list_tools(), frozen_read_only_catalog()
                )
                if catalog is None:
                    outcome = "catalog_incompatible"
                    return BotResponse(text=UNAVAILABLE_REPLY)
                stage = "model_completion"
                turn = await self.language_model.complete(
                    ModelRequest(
                        user_message=message,
                        reference_date=self.reference_date_factory(),
                        timezone="Europe/Ljubljana",
                        tools=tuple(policy.definition for policy in catalog.values()),
                    )
                )
                if isinstance(turn, NoTool):
                    outcome = "clarification"
                    return BotResponse(text=CLARIFICATION_REPLY)
                policy = catalog.get(turn.name)
                if policy is None:
                    outcome = "tool_rejected"
                    return BotResponse(text=UNAVAILABLE_REPLY)
                stage = "model_validation"
                arguments = policy.validate_arguments(turn.arguments)
                if arguments is None:
                    outcome = "invalid_request"
                    return BotResponse(text=INVALID_REQUEST_REPLY)
                stage = "mcp_tool_call"
                page = await policy.call(session, arguments)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except AttendanceToolFailure as error:
            outcome = "tool_failure"
            error_code = error.failure.code
            error_type = type(error).__name__
            return BotResponse(text=TOOL_FAILURE_REPLIES.get(error.failure.code, UNAVAILABLE_REPLY))
        except (
            AttendanceMcpUnavailable,
            McpContractIncompatible,
            LanguageModelUnavailable,
        ) as error:
            outcome = "dependency_unavailable"
            error_type = type(error).__name__
            return BotResponse(text=UNAVAILABLE_REPLY)
        except Exception as error:
            outcome = "unexpected_failure"
            error_type = type(error).__name__
            return BotResponse(text=UNAVAILABLE_REPLY)
        finally:
            if not cancelled:
                log_event(
                    get_logger("agent"),
                    logging.INFO if outcome == "success" else logging.WARNING,
                    "attendance_turn_completed",
                    correlation_id=str(correlation_id),
                    stage="rendering" if outcome == "success" else stage,
                    outcome=outcome,
                    error_code=error_code,
                    error_type=error_type,
                    duration_ms=max(0, round((perf_counter() - started_at) * 1000)),
                )
        return BotResponse(text=policy.render(page))


def _catalog_intersection(
    remote_tools: tuple[ToolDefinition, ...], policies: tuple[FrozenReadOnlyTool, ...]
) -> dict[str, FrozenReadOnlyTool] | None:
    configured = {policy.definition.name: policy for policy in policies}
    remote_by_name = {tool.name: tool for tool in remote_tools}
    if (
        len(configured) != len(policies)
        or len(remote_by_name) != len(remote_tools)
        or remote_by_name.keys() != configured.keys()
    ):
        return None
    if not all(policy.supports_remote(remote_by_name[name]) for name, policy in configured.items()):
        return None
    return configured


def _has_bounded_arguments(arguments: Mapping[str, object]) -> bool:
    """Keep unusably large model output out of validation and downstream calls."""
    return len(arguments) <= 2 and all(
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
