from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

import httpx
import structlog
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
from attendance_teams_bot.observability import OperationLifecycle

_VERSION = re.compile(r"^(?P<major>[0-9]+)\.[0-9]+\.[0-9]+$")
HttpClientFactory = Callable[[dict[str, str]], httpx.AsyncClient]
_LOGGER = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StreamableHttpAttendanceSession:
    """Adapt an initialized streamable-HTTP MCP session to the bot's typed session port."""

    session: ClientSession

    async def list_tools(self) -> tuple[DiscoveredMcpTool, ...]:
        """Return untrusted discovery fields for later bot-side catalog admission."""
        lifecycle = OperationLifecycle(
            _LOGGER,
            handler="StreamableHttpAttendanceSession.list_tools",
            operation="mcp_tool_discovery",
        )
        lifecycle.start(step="request")
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
            lifecycle.fail(error)
            raise
        except asyncio.CancelledError:
            lifecycle.cancel()
            raise
        lifecycle.succeed(step="response_validated", input_metadata={"tool_count": len(tools)})
        return tuple(tools)

    async def call_tool(
        self,
        *,
        name: str,
        arguments: Mapping[str, object],
    ) -> AttendanceEventPage:
        """Execute an admitted tool and validate its text result as an attendance page."""
        metadata = {"tool_name": name, "argument_count": len(arguments)}
        lifecycle = OperationLifecycle(
            _LOGGER,
            handler="StreamableHttpAttendanceSession.call_tool",
            operation="mcp_tool_execution",
            input_metadata=metadata,
        )
        lifecycle.start(step="request")
        try:
            result = await self.session.call_tool(name, dict(arguments))
            text = _tool_result_text(result.content)
            if result.isError:
                raise AttendanceToolFailure(McpToolFailure.model_validate_json(text))
            page = AttendanceEventPage.model_validate(json.loads(text))
        except Exception as error:
            lifecycle.fail(error)
            raise
        except asyncio.CancelledError:
            lifecycle.cancel()
            raise
        lifecycle.succeed(step="response_validated")
        return page


class StreamableHttpAttendanceSessionFactory:
    """Open streamable-HTTP MCP sessions with delegated authentication and contract checking."""

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
    ) -> AsyncGenerator[StreamableHttpAttendanceSession]:
        """Yield an initialized session, translating connection failures to the safe MCP error."""
        lifecycle = OperationLifecycle(
            _LOGGER,
            handler="StreamableHttpAttendanceSessionFactory.open",
            operation="mcp_session_connection",
        )
        lifecycle.start(step="connect")
        headers = {
            "Authorization": f"Bearer {access_token.get_secret_value()}",
            CORRELATION_ID_HEADER: str(correlation_id),
            ATTENDANCE_MCP_CONTRACT_HEADER: ATTENDANCE_MCP_CONTRACT_MAJOR,
        }
        downstream_error: BaseException | None = None
        try:
            async with AsyncExitStack() as stack:
                http_client = self._create_http_client(headers)
                stack.push_async_callback(http_client.aclose)
                read_stream, write_stream, _ = await stack.enter_async_context(
                    streamable_http_client(self._endpoint, http_client=http_client)
                )
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
                await session.initialize()
                lifecycle.step_completed(step="initialized")
                try:
                    yield StreamableHttpAttendanceSession(session)
                except BaseException as error:
                    downstream_error = error
                    raise
        except Exception as error:
            if error is downstream_error:
                lifecycle.succeed(step="closed")
                raise
            lifecycle.fail(error)
            if isinstance(error, McpContractIncompatible):
                raise
            raise AttendanceMcpUnavailable from None
        except asyncio.CancelledError as error:
            if error is downstream_error:
                lifecycle.succeed(step="closed")
                raise
            lifecycle.cancel()
            raise
        lifecycle.succeed(step="closed")

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
