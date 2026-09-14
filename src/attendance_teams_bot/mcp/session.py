from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import cast
from uuid import UUID

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import ToolAnnotations
from pydantic import SecretStr

from attendance_teams_bot.agent.language_model import ToolDefinition
from attendance_teams_bot.mcp.client import (
    AttendanceMcpUnavailable,
    AttendanceToolFailure,
    McpContractIncompatible,
)
from attendance_teams_bot.mcp.contracts import (
    ATTENDANCE_MCP_CONTRACT_HEADER,
    ATTENDANCE_MCP_CONTRACT_MAJOR,
    CORRELATION_ID_HEADER,
    AttendanceEventPage,
    McpToolFailure,
)
from attendance_teams_bot.observability import get_logger, operation_event

_VERSION = re.compile(r"^(?P<major>[0-9]+)\.[0-9]+\.[0-9]+$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ANNOTATION_TYPES: dict[str, type[str] | type[bool]] = {
    "title": str,
    "readOnlyHint": bool,
    "destructiveHint": bool,
    "idempotentHint": bool,
    "openWorldHint": bool,
}
HttpClientFactory = Callable[[dict[str, str]], httpx.AsyncClient]


@dataclass(frozen=True, slots=True)
class StreamableHttpAttendanceSession:
    session: ClientSession

    async def list_tools(self) -> tuple[ToolDefinition, ...]:
        started_at = perf_counter()
        logger = get_logger("mcp.session")
        operation_event(
            logger,
            event="operation_started",
            handler="StreamableHttpAttendanceSession.list_tools",
            operation="mcp_tool_discovery",
            step="request",
        )
        try:
            result = await self.session.list_tools()
            tools: list[ToolDefinition] = []
            for tool in result.tools:
                name = getattr(tool, "name", None)
                description = getattr(tool, "description", None)
                input_schema = getattr(tool, "inputSchema", None)
                annotations = getattr(tool, "annotations", None)
                if not _is_safe_tool_field(name, description, input_schema):
                    raise AttendanceMcpUnavailable
                tools.append(
                    ToolDefinition(
                        name=cast(str, name),
                        description=cast(str, description),
                        input_schema=dict(cast(Mapping[str, object], input_schema)),
                        annotations=_normalize_annotations(annotations),
                    )
                )
        except Exception as error:
            operation_event(
                logger,
                event="operation_failed",
                handler="StreamableHttpAttendanceSession.list_tools",
                operation="mcp_tool_discovery",
                step="request",
                duration_ms=_duration_ms(started_at),
                error_type=type(error).__name__,
            )
            raise
        operation_event(
            logger,
            event="operation_succeeded",
            handler="StreamableHttpAttendanceSession.list_tools",
            operation="mcp_tool_discovery",
            step="response_validated",
            duration_ms=_duration_ms(started_at),
            input_metadata={"tool_count": len(tools)},
        )
        return tuple(tools)

    async def call_tool(
        self,
        *,
        name: str,
        arguments: Mapping[str, object],
    ) -> AttendanceEventPage:
        started_at = perf_counter()
        logger = get_logger("mcp.session")
        metadata = {"tool_name": name, "argument_count": len(arguments)}
        operation_event(
            logger,
            event="operation_started",
            handler="StreamableHttpAttendanceSession.call_tool",
            operation="mcp_tool_execution",
            step="request",
            input_metadata=metadata,
        )
        try:
            result = await self.session.call_tool(name, dict(arguments))
            text = _tool_result_text(result.content)
            if result.isError:
                raise AttendanceToolFailure(McpToolFailure.model_validate_json(text))
            page = AttendanceEventPage.model_validate(json.loads(text))
        except Exception as error:
            operation_event(
                logger,
                event="operation_failed",
                handler="StreamableHttpAttendanceSession.call_tool",
                operation="mcp_tool_execution",
                step="request",
                duration_ms=_duration_ms(started_at),
                error_type=type(error).__name__,
                input_metadata=metadata,
            )
            raise
        operation_event(
            logger,
            event="operation_succeeded",
            handler="StreamableHttpAttendanceSession.call_tool",
            operation="mcp_tool_execution",
            step="response_validated",
            duration_ms=_duration_ms(started_at),
            input_metadata=metadata,
        )
        return page


class StreamableHttpAttendanceSessionFactory:
    def __init__(
        self,
        *,
        endpoint: str,
        timeout_seconds: float,
        http_client_factory: HttpClientFactory | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._timeout_seconds = timeout_seconds
        self._http_client_factory = http_client_factory

    @asynccontextmanager
    async def open(
        self,
        *,
        access_token: SecretStr,
        correlation_id: UUID,
    ) -> AsyncIterator[StreamableHttpAttendanceSession]:
        started_at = perf_counter()
        logger = get_logger("mcp.session")
        operation_event(
            logger,
            event="operation_started",
            handler="StreamableHttpAttendanceSessionFactory.open",
            operation="mcp_session_connection",
            step="connect",
        )
        headers = {
            "Authorization": f"Bearer {access_token.get_secret_value()}",
            CORRELATION_ID_HEADER: str(correlation_id),
            ATTENDANCE_MCP_CONTRACT_HEADER: ATTENDANCE_MCP_CONTRACT_MAJOR,
        }
        try:
            async with AsyncExitStack() as stack:
                http_client = self._create_http_client(headers)
                stack.push_async_callback(http_client.aclose)
                read_stream, write_stream, _ = await stack.enter_async_context(
                    streamable_http_client(self._endpoint, http_client=http_client)
                )
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
                await session.initialize()
                operation_event(
                    logger,
                    event="operation_step_completed",
                    handler="StreamableHttpAttendanceSessionFactory.open",
                    operation="mcp_session_connection",
                    step="initialized",
                    duration_ms=_duration_ms(started_at),
                )
                yield StreamableHttpAttendanceSession(session)
                operation_event(
                    logger,
                    event="operation_succeeded",
                    handler="StreamableHttpAttendanceSessionFactory.open",
                    operation="mcp_session_connection",
                    step="closed",
                    duration_ms=_duration_ms(started_at),
                )
        except (AttendanceToolFailure, McpContractIncompatible) as error:
            operation_event(
                logger,
                event="operation_failed",
                handler="StreamableHttpAttendanceSessionFactory.open",
                operation="mcp_session_connection",
                step="connect",
                duration_ms=_duration_ms(started_at),
                error_type=type(error).__name__,
            )
            raise
        except Exception as error:
            operation_event(
                logger,
                event="operation_failed",
                handler="StreamableHttpAttendanceSessionFactory.open",
                operation="mcp_session_connection",
                step="connect",
                duration_ms=_duration_ms(started_at),
                error_type=type(error).__name__,
            )
            raise AttendanceMcpUnavailable from None

    def _create_http_client(self, headers: dict[str, str]) -> httpx.AsyncClient:
        if self._http_client_factory is not None:
            client = self._http_client_factory(headers)
            client.event_hooks.setdefault("response", []).append(self._validate_contract_version)
            return client
        return httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(self._timeout_seconds),
            event_hooks={"response": [self._validate_contract_version]},
        )

    async def _validate_contract_version(self, response: httpx.Response) -> None:
        version = response.headers.get(ATTENDANCE_MCP_CONTRACT_HEADER)
        match = _VERSION.fullmatch(version or "")
        if match is None or match.group("major") != ATTENDANCE_MCP_CONTRACT_MAJOR:
            raise McpContractIncompatible


def _tool_result_text(content: Sequence[object]) -> str:
    for item in content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            return text
    raise AttendanceMcpUnavailable


def _is_safe_tool_field(name: object, description: object, input_schema: object) -> bool:
    return (
        isinstance(name, str)
        and _TOOL_NAME.fullmatch(name) is not None
        and isinstance(description, str)
        and len(description) <= 4_096
        and isinstance(input_schema, Mapping)
        and _is_safe_json_value(input_schema)
    )


def _normalize_annotations(annotations: object) -> dict[str, object]:
    """Accept only the MCP SDK's standard, scalar tool annotation fields."""
    if annotations is None:
        return {}
    if isinstance(annotations, ToolAnnotations):
        values = cast(Mapping[object, object], annotations.model_dump(exclude_none=True))
    elif isinstance(annotations, Mapping):
        values = annotations
    else:
        raise AttendanceMcpUnavailable
    normalized: dict[str, object] = {}
    for key, value in values.items():
        if not isinstance(key, str) or key not in _ANNOTATION_TYPES:
            raise AttendanceMcpUnavailable
        if type(value) is not _ANNOTATION_TYPES[key]:
            raise AttendanceMcpUnavailable
        normalized[key] = value
    return normalized


def _is_safe_json_value(value: object, depth: int = 0) -> bool:
    if depth > 16:
        return False
    if value is None or isinstance(value, bool | int | float):
        return True
    if isinstance(value, str):
        return len(value) <= 4_096
    if isinstance(value, Mapping):
        return len(value) <= 256 and all(
            isinstance(key, str) and len(key) <= 256 and _is_safe_json_value(item, depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return len(value) <= 256 and all(_is_safe_json_value(item, depth + 1) for item in value)
    return False


def _duration_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))
