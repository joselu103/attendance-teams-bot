from datetime import date
from uuid import UUID

import httpx
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import SecretStr

from attendance_teams_bot.mcp.client import StreamableHttpAttendanceMcpClient
from attendance_teams_bot.mcp.contracts import (
    ATTENDANCE_MCP_CONTRACT_HEADER,
    ATTENDANCE_MCP_CONTRACT_MAJOR,
    CORRELATION_ID_HEADER,
)


class ContractHeaderApp:
    def __init__(self, app) -> None:
        self._app = app
        self.requests: list[dict[str, str]] = []

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            self.requests.append(
                {
                    ":method": scope["method"],
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
        start_date: str,
        end_date: str,
        limit: int = 50,
        offset: int = 0,
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
                }
            ],
            "limit": limit,
            "offset": offset,
            "next_offset": None,
        }

    return ContractHeaderApp(server.streamable_http_app())


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_official_client_calls_requester_scoped_tool(mcp_app: ContractHeaderApp) -> None:
    def http_client_factory(headers: dict[str, str]) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_app),
            base_url="http://localhost",
            headers=headers,
        )

    client = StreamableHttpAttendanceMcpClient(
        endpoint="http://localhost/mcp",
        timeout_seconds=10,
        http_client_factory=http_client_factory,
    )

    async with mcp_app._app.router.lifespan_context(mcp_app._app):
        page = await client.list_my_attendance_events(
            access_token=SecretStr("downstream-token"),
            correlation_id=UUID("11111111-1111-1111-1111-111111111111"),
            start_date=date(2026, 8, 10),
            end_date=date(2026, 8, 10),
        )

    assert page.items[0].punch_type == "Remote work"
    mcp_requests = [request for request in mcp_app.requests if request[":method"] != "OPTIONS"]
    assert mcp_requests
    missing_authorization = [
        request[":method"] for request in mcp_requests if "authorization" not in request
    ]
    assert not missing_authorization
    assert all(request["authorization"] == "Bearer downstream-token" for request in mcp_requests)
    assert all(
        request[CORRELATION_ID_HEADER.lower()] == "11111111-1111-1111-1111-111111111111"
        for request in mcp_requests
    )
    assert all(
        request[ATTENDANCE_MCP_CONTRACT_HEADER.lower()] == ATTENDANCE_MCP_CONTRACT_MAJOR
        for request in mcp_requests
    )
