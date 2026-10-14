import socket
import ssl
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import SecretStr
from structlog.testing import capture_logs

from attendance_teams_bot.mcp import session as mcp_session
from attendance_teams_bot.mcp.client import AttendanceMcpUnavailable, McpContractIncompatible
from attendance_teams_bot.mcp.contracts import (
    ATTENDANCE_MCP_CONTRACT_HEADER,
    ATTENDANCE_MCP_CONTRACT_MAJOR,
    CORRELATION_ID_HEADER,
)
from attendance_teams_bot.mcp.session import StreamableHttpAttendanceSessionFactory


class ContractHeaderApp:
    def __init__(self, app) -> None:
        self._app = app
        self.requests: list[dict[str, str]] = []

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            self.requests.append(
                {
                    ":method": scope["method"],
                    ":path": scope["path"],
                    **{name.decode(): value.decode() for name, value in scope["headers"]},
                }
            )

        async def send_with_contract_header(message) -> None:
            if message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (ATTENDANCE_MCP_CONTRACT_HEADER.lower().encode(), b"1.1.0"),
                    ],
                }
            await send(message)

        await self._app(scope, receive, send_with_contract_header)


@pytest.fixture
def mcp_app() -> ContractHeaderApp:
    server = FastMCP(
        "attendance-test",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        json_response=True,
        stateless_http=True,
    )

    @server.tool()
    def list_my_attendance_events(
        start_date: str, end_date: str, limit: int = 50, offset: int = 0
    ) -> dict[str, object]:
        assert start_date == "2026-08-10"
        assert end_date == "2026-08-10"
        assert limit == 50
        assert offset == 0
        return {
            "items": [
                {
                    "attendance_event_id": 100,
                    "employee_id": 42,
                    "punch_type": "Remote work",
                    "location": "Home",
                    "checked_in_at": "2026-08-10T08:00:00+02:00",
                    "checked_out_at": "2026-08-10T16:00:00+02:00",
                    "note": None,
                    "duration_minutes": 480,
                }
            ],
            "limit": limit,
            "offset": offset,
            "next_offset": None,
            "summary": {"page_duration_minutes": 480, "period_duration_minutes": 480},
        }

    return ContractHeaderApp(server.streamable_http_app())


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_authenticated_session_discovers_and_calls_with_contract_headers(
    mcp_app: ContractHeaderApp,
) -> None:
    def http_client_factory(headers: dict[str, str]) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_app), base_url="http://localhost", headers=headers
        )

    factory = StreamableHttpAttendanceSessionFactory(
        endpoint="http://localhost/mcp", timeout_seconds=10, http_client_factory=http_client_factory
    )
    correlation_id = UUID("11111111-1111-1111-1111-111111111111")
    async with mcp_app._app.router.lifespan_context(mcp_app._app):
        async with factory.open(
            access_token=SecretStr("downstream-token"), correlation_id=correlation_id
        ) as session:
            tools = await session.list_tools()
            page = await session.call_tool(
                name="list_my_attendance_events",
                arguments={
                    "start_date": "2026-08-10",
                    "end_date": "2026-08-10",
                    "limit": 50,
                    "offset": 0,
                },
            )

    assert [tool.name for tool in tools] == ["list_my_attendance_events"]
    assert page.items[0].punch_type == "Remote work"
    requests = [request for request in mcp_app.requests if request[":method"] != "OPTIONS"]
    assert requests
    assert {request["authorization"] for request in requests} == {"Bearer downstream-token"}
    assert {request[CORRELATION_ID_HEADER.lower()] for request in requests} == {str(correlation_id)}
    assert {request[ATTENDANCE_MCP_CONTRACT_HEADER.lower()] for request in requests} == {
        ATTENDANCE_MCP_CONTRACT_MAJOR
    }
    assert {request[":path"] for request in requests} == {"/mcp"}


@pytest.mark.anyio
async def test_session_connection_logs_safe_http_status_metadata_without_url_or_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://attendance.example/mcp?access_token=query-secret")
    response = httpx.Response(
        503,
        headers={"content-type": "application/json", "content-length": "99"},
        content=b'{"attendance_event_id": 100, "token": "response-secret"}',
        request=request,
    )

    @asynccontextmanager
    async def failing_streamable_client(*_args, **_kwargs):
        raise httpx.HTTPStatusError("server error", request=request, response=response)
        yield  # pragma: no cover

    monkeypatch.setattr(mcp_session, "streamable_http_client", failing_streamable_client)
    factory = StreamableHttpAttendanceSessionFactory(
        endpoint="https://attendance.example/mcp?authorization=url-secret",
        timeout_seconds=10,
    )

    with capture_logs() as events:
        with pytest.raises(AttendanceMcpUnavailable):
            async with factory.open(
                access_token=SecretStr("downstream-token"),
                correlation_id=UUID("11111111-1111-1111-1111-111111111111"),
            ):
                pass

    failure = events[-1]
    assert failure["event"] == "operation_failed"
    assert failure["error_type"] == "HTTPStatusError"
    assert failure["input_metadata"] == {
        "failure_class": "http_status",
        "exception_types": ("HTTPStatusError",),
        "http_method": "POST",
        "http_response_content_type": "application/json",
        "http_response_has_content_length": True,
        "http_status_code": 503,
    }
    rendered = repr(events)
    assert "attendance.example" not in rendered
    assert "query-secret" not in rendered
    assert "url-secret" not in rendered
    assert "downstream-token" not in rendered
    assert "response-secret" not in rendered


@pytest.mark.anyio
async def test_session_connection_classifies_nested_timeout_and_records_leaf_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeout = httpx.ReadTimeout("slow", request=httpx.Request("POST", "https://ignored"))

    @asynccontextmanager
    async def failing_streamable_client(*_args, **_kwargs):
        raise ExceptionGroup("connection failures", [RuntimeError("ignored"), timeout])
        yield  # pragma: no cover

    monkeypatch.setattr(mcp_session, "streamable_http_client", failing_streamable_client)
    factory = StreamableHttpAttendanceSessionFactory(
        endpoint="https://attendance.example/mcp", timeout_seconds=10
    )

    with capture_logs() as events:
        with pytest.raises(AttendanceMcpUnavailable):
            async with factory.open(
                access_token=SecretStr("downstream-token"),
                correlation_id=UUID("11111111-1111-1111-1111-111111111111"),
            ):
                pass

    failure = events[-1]
    assert failure["input_metadata"] == {
        "failure_class": "timeout",
        "exception_types": ("ExceptionGroup", "RuntimeError", "ReadTimeout"),
    }


@pytest.mark.parametrize(
    ("error", "phase", "expected"),
    [
        (socket.gaierror(1, "lookup failed"), "connect", "dns"),
        (ssl.SSLError("handshake failed"), "connect", "tls"),
        (httpx.ConnectError("refused"), "connect", "connect"),
        (McpContractIncompatible(), "connect", "mcp_protocol"),
        (RuntimeError("bad initialize response"), "initialize", "initialization"),
    ],
)
def test_session_connection_classifies_safe_failure_categories(
    error: BaseException, phase: str, expected: str
) -> None:
    assert mcp_session._connection_failure_class(error, phase) == expected
