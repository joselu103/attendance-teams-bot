from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID, uuid4
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


def canonical_self_attendance_tool() -> ToolDefinition:
    """Return a fresh bot-owned schema; remote MCP metadata is never prompted."""
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
    )


@dataclass(frozen=True, slots=True)
class AttendanceAgent:
    language_model: LanguageModel
    mcp_session_factory: McpSessionFactory
    correlation_id_factory: Callable[[], UUID] = uuid4
    reference_date_factory: Callable[[], date] = lambda: datetime.now(
        ZoneInfo("Europe/Ljubljana")
    ).date()

    async def handle(self, *, message: str, mcp_access_token: SecretStr) -> BotResponse:
        correlation_id = self.correlation_id_factory()
        try:
            async with self.mcp_session_factory.open(
                access_token=mcp_access_token, correlation_id=correlation_id
            ) as session:
                if not _catalog_supports_self_attendance(await session.list_tools()):
                    return BotResponse(text=UNAVAILABLE_REPLY)
                turn = await self.language_model.complete(
                    ModelRequest(
                        user_message=message,
                        reference_date=self.reference_date_factory(),
                        timezone="Europe/Ljubljana",
                        tools=(canonical_self_attendance_tool(),),
                    )
                )
                if isinstance(turn, NoTool):
                    return BotResponse(text=CLARIFICATION_REPLY)
                if turn.name != SELF_ATTENDANCE_TOOL:
                    return BotResponse(text=UNAVAILABLE_REPLY)
                try:
                    arguments = ListMyAttendanceArguments.model_validate(dict(turn.arguments))
                except ValidationError:
                    return BotResponse(text=INVALID_REQUEST_REPLY)
                page = await session.call_tool(
                    name=SELF_ATTENDANCE_TOOL,
                    arguments={
                        "start_date": arguments.start_date.isoformat(),
                        "end_date": arguments.end_date.isoformat(),
                        "limit": 50,
                        "offset": 0,
                    },
                )
        except asyncio.CancelledError:
            raise
        except AttendanceToolFailure as error:
            return BotResponse(text=TOOL_FAILURE_REPLIES.get(error.failure.code, UNAVAILABLE_REPLY))
        except AttendanceMcpUnavailable, McpContractIncompatible, LanguageModelUnavailable:
            return BotResponse(text=UNAVAILABLE_REPLY)
        except Exception:
            return BotResponse(text=UNAVAILABLE_REPLY)
        return BotResponse(text=render_attendance_page(page))


def _catalog_supports_self_attendance(tools: tuple[ToolDefinition, ...]) -> bool:
    matches = [tool for tool in tools if tool.name == SELF_ATTENDANCE_TOOL]
    return len(matches) == 1 and _is_compatible_self_attendance_schema(matches[0].input_schema)


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
