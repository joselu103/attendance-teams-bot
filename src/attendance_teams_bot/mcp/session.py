from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from time import perf_counter
from uuid import UUID

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import ToolAnnotations
from pydantic import SecretStr

from attendance_teams_bot.agent.mcp_catalog import DiscoveredMcpTool
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
HttpClientFactory = Callable[[dict[str, str]], httpx.AsyncClient]


@dataclass(frozen=True, slots=True)
class StreamableHttpAttendanceSession:
    session: ClientSession

    async def list_tools(self) -> tuple[DiscoveredMcpTool, ...]:
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
            tools: list[DiscoveredMcpTool] = []
            for tool in result.tools:
                name = getattr(tool, "name", None)
                description = getattr(tool, "description", None)
                input_schema = getattr(tool, "inputSchema", None)
                annotations = getattr(tool, "annotations", None)
                if isinstance(annotations, ToolAnnotations):
                    annotations = annotations.model_dump(exclude_none=True)
                tools.append(DiscoveredMcpTool(name, description, input_schema, annotations))
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


def _duration_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))
