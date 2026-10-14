from __future__ import annotations

import asyncio
import json
import re
import socket
import ssl
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

import httpx
import structlog
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
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
    CURRENT_ATTENDANCE_TOOL,
    CURRENT_WORK_STATUS_TOOL,
    RESOLVE_EMPLOYEE_TOOL,
    SEARCH_EMPLOYEES_TOOL,
    AttendanceEventPage,
    CurrentAttendancePage,
    CurrentWorkStatusPage,
    EmployeeSuggestionPage,
    McpToolFailure,
    ResolvedEmployee,
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
                meta = getattr(tool, "meta", getattr(tool, "_meta", None))
                if isinstance(annotations, ToolAnnotations):
                    annotations = annotations.model_dump(exclude_none=True)
                tools.append(DiscoveredMcpTool(name, description, input_schema, annotations, meta))
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
    ) -> (
        AttendanceEventPage
        | ResolvedEmployee
        | CurrentAttendancePage
        | CurrentWorkStatusPage
        | EmployeeSuggestionPage
    ):
        """Execute an admitted tool and validate its named, typed result."""
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
            payload = json.loads(text)
            page: (
                AttendanceEventPage
                | ResolvedEmployee
                | CurrentAttendancePage
                | CurrentWorkStatusPage
                | EmployeeSuggestionPage
            )
            if name == RESOLVE_EMPLOYEE_TOOL:
                page = ResolvedEmployee.model_validate(payload)
            elif name == CURRENT_ATTENDANCE_TOOL:
                page = CurrentAttendancePage.model_validate(payload)
            elif name == CURRENT_WORK_STATUS_TOOL:
                page = CurrentWorkStatusPage.model_validate(payload)
            elif name == SEARCH_EMPLOYEES_TOOL:
                page = EmployeeSuggestionPage.model_validate(payload)
            else:
                page = AttendanceEventPage.model_validate(payload)
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
        response_metadata: dict[str, object] = {}
        phase = "connect"
        try:
            async with AsyncExitStack() as stack:
                http_client = self._create_http_client(headers, response_metadata)
                stack.push_async_callback(http_client.aclose)
                read_stream, write_stream, _ = await stack.enter_async_context(
                    streamable_http_client(self._endpoint, http_client=http_client)
                )
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
                phase = "initialize"
                await session.initialize()
                phase = "active"
                lifecycle.step_completed(step="active")
                try:
                    yield StreamableHttpAttendanceSession(session)
                except BaseException as error:
                    downstream_error = error
                    raise
        except Exception as error:
            if error is downstream_error:
                lifecycle.succeed(step="closed")
                raise
            lifecycle.fail(
                error,
                input_metadata=_connection_failure_metadata(error, phase, response_metadata),
            )
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

    def _create_http_client(
        self, headers: dict[str, str], response_metadata: dict[str, object]
    ) -> httpx.AsyncClient:
        async def capture_response(response: httpx.Response) -> None:
            response_metadata.update(_safe_http_response_metadata(response))

        if self._http_client_factory is not None:
            client = self._http_client_factory(headers)
            client.event_hooks.setdefault("response", []).append(capture_response)
            client.event_hooks.setdefault("response", []).append(self._validate_contract_version)
            return client
        return httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(self._timeout_seconds),
            event_hooks={"response": [capture_response, self._validate_contract_version]},
        )

    async def _validate_contract_version(self, response: httpx.Response) -> None:
        version = response.headers.get(ATTENDANCE_MCP_CONTRACT_HEADER)
        match = _VERSION.fullmatch(version or "")
        if match is None or match.group("major") != ATTENDANCE_MCP_CONTRACT_MAJOR:
            raise McpContractIncompatible


def _connection_failure_metadata(
    error: BaseException, phase: str, response_metadata: Mapping[str, object]
) -> dict[str, object]:
    types = tuple(_exception_types(error))
    metadata: dict[str, object] = {
        "failure_class": _connection_failure_class(error, phase),
        "exception_types": types,
    }
    metadata.update(response_metadata)
    response = _http_response_from_error(error)
    if response is not None:
        metadata.update(_safe_http_response_metadata(response))
    return metadata


def _connection_failure_class(error: BaseException, phase: str) -> str:
    errors = tuple(_walk_exceptions(error))
    if any(isinstance(item, (httpx.TimeoutException, TimeoutError)) for item in errors):
        return "timeout"
    if any(isinstance(item, socket.gaierror) for item in errors):
        return "dns"
    if any(isinstance(item, ssl.SSLError) for item in errors):
        return "tls"
    if any(isinstance(item, httpx.HTTPStatusError) for item in errors):
        return "http_status"
    if any(isinstance(item, httpx.ConnectError) for item in errors):
        return "connect"
    if any(
        isinstance(item, (McpError, McpContractIncompatible, httpx.ProtocolError))
        for item in errors
    ):
        return "mcp_protocol"
    if phase == "active":
        return "operation"
    if phase == "initialize":
        return "initialization"
    return "connection"


def _exception_types(error: BaseException) -> list[str]:
    return list(dict.fromkeys(type(item).__name__ for item in _walk_exceptions(error)))


def _walk_exceptions(error: BaseException) -> list[BaseException]:
    errors = [error]
    if isinstance(error, BaseExceptionGroup):
        for nested in error.exceptions:
            errors.extend(_walk_exceptions(nested))
    if error.__cause__ is not None:
        errors.extend(_walk_exceptions(error.__cause__))
    elif error.__context__ is not None:
        errors.extend(_walk_exceptions(error.__context__))
    return errors


def _http_response_from_error(error: BaseException) -> httpx.Response | None:
    for item in _walk_exceptions(error):
        if isinstance(item, httpx.HTTPStatusError):
            return item.response
    return None


def _safe_http_response_metadata(response: httpx.Response) -> dict[str, object]:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    try:
        method = response.request.method
    except RuntimeError:
        method = "unavailable"
    return {
        "http_method": (
            method if method in {"DELETE", "GET", "HEAD", "OPTIONS", "POST"} else "other"
        ),
        "http_status_code": response.status_code,
        "http_response_content_type": (
            content_type if content_type in {"application/json", "text/event-stream"} else "other"
        ),
        "http_response_has_content_length": "content-length" in response.headers,
    }


def _tool_result_text(content: Sequence[object]) -> str:
    for item in content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            return text
    raise AttendanceMcpUnavailable
