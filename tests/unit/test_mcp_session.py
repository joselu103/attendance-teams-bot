import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import Tool, ToolAnnotations
from pydantic import ValidationError

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


@pytest.mark.anyio
async def test_current_work_status_parses_only_the_category_safe_projection() -> None:
    client = SimpleNamespace(
        call_tool=AsyncMock(
            return_value=SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text=json.dumps(
                            {
                                "items": [
                                    {
                                        "first_name": "Ada",
                                        "last_name": "Lovelace",
                                        "status": "office",
                                    }
                                ],
                                "limit": 50,
                                "offset": 0,
                                "next_offset": None,
                            }
                        )
                    )
                ],
                isError=False,
            )
        )
    )

    page = await StreamableHttpAttendanceSession(session=client).call_tool(
        name="get_current_work_status", arguments={"statuses": ["office"], "limit": 50, "offset": 0}
    )

    assert page.items[0].model_dump() == {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "status": "office",
    }


@pytest.mark.anyio
async def test_current_work_status_rejects_unapproved_result_fields() -> None:
    client = SimpleNamespace(
        call_tool=AsyncMock(
            return_value=SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text=json.dumps(
                            {
                                "items": [
                                    {
                                        "first_name": "Ada",
                                        "last_name": "Lovelace",
                                        "status": "office",
                                        "employee_id": 7,
                                    }
                                ],
                                "limit": 50,
                                "offset": 0,
                                "next_offset": None,
                            }
                        )
                    )
                ],
                isError=False,
            )
        )
    )

    with pytest.raises(ValidationError):
        await StreamableHttpAttendanceSession(session=client).call_tool(
            name="get_current_work_status", arguments={}
        )
