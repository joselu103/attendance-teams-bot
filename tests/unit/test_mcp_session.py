from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import Tool, ToolAnnotations

from attendance_teams_bot.mcp.client import AttendanceMcpUnavailable
from attendance_teams_bot.mcp.session import StreamableHttpAttendanceSession


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_list_tools_normalizes_sdk_tool_annotations() -> None:
    sdk_tool = Tool(
        name="list_my_attendance_events",
        description="Remote description",
        inputSchema={"type": "object"},
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    client = SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(tools=[sdk_tool])))

    tools = await StreamableHttpAttendanceSession(session=client).list_tools()

    assert tools[0].annotations == {"readOnlyHint": True}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "tool",
    [
        SimpleNamespace(name=None, description="x", inputSchema={"type": "object"}),
        SimpleNamespace(name="safe_name", description=None, inputSchema={"type": "object"}),
        SimpleNamespace(name="safe_name", description="x", inputSchema=["not", "a", "schema"]),
        SimpleNamespace(
            name="safe_name",
            description="x",
            inputSchema={"type": "object"},
            annotations={"readOnlyHint": "true"},
        ),
        SimpleNamespace(
            name="safe_name",
            description="x",
            inputSchema={"type": "object"},
            annotations={"unrecognized": True},
        ),
    ],
)
async def test_list_tools_rejects_malformed_required_fields_and_annotations(tool: object) -> None:
    client = SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(tools=[tool])))

    with pytest.raises(AttendanceMcpUnavailable):
        await StreamableHttpAttendanceSession(session=client).list_tools()
