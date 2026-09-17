from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import Tool, ToolAnnotations

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
async def test_list_tools_preserves_untrusted_sdk_fields_for_catalog_admission() -> None:
    sdk_tool = SimpleNamespace(
        name=None,
        description="x",
        inputSchema=["not", "a", "schema"],
        annotations={"readOnlyHint": "true"},
    )
    client = SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(tools=[sdk_tool])))

    tools = await StreamableHttpAttendanceSession(session=client).list_tools()

    assert tools[0].name is None
    assert tools[0].input_schema == ["not", "a", "schema"]
    assert tools[0].annotations == {"readOnlyHint": "true"}
