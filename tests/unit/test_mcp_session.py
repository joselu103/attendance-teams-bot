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


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("duration", "summary"),
    [
        ("480", {"page_duration_minutes": 480, "period_duration_minutes": 480}),
        (480.0, {"page_duration_minutes": 480, "period_duration_minutes": 480}),
        (True, {"page_duration_minutes": 480, "period_duration_minutes": 480}),
        (-1, {"page_duration_minutes": 480, "period_duration_minutes": 480}),
        (None, {"page_duration_minutes": "480", "period_duration_minutes": 480}),
        (None, {"page_duration_minutes": 480.0, "period_duration_minutes": 480}),
        (None, {"page_duration_minutes": 480, "period_duration_minutes": True}),
        (None, {"page_duration_minutes": 480, "period_duration_minutes": -1}),
        (None, {"page_duration_minutes": 480, "period_duration_minutes": None}),
    ],
)
async def test_history_session_rejects_malformed_required_metrics(
    duration: object, summary: dict[str, object]
) -> None:
    client = SimpleNamespace(
        call_tool=AsyncMock(
            return_value=SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text=json.dumps(
                            {
                                "items": [
                                    {
                                        "attendance_event_id": 7,
                                        "employee_id": 9,
                                        "punch_type": "Office",
                                        "location": "HQ",
                                        "checked_in_at": "2026-08-10T08:00:00+02:00",
                                        "checked_out_at": "2026-08-10T16:00:00+02:00",
                                        "note": None,
                                        "duration_minutes": duration,
                                    }
                                ],
                                "limit": 50,
                                "offset": 0,
                                "next_offset": None,
                                "summary": summary,
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
            name="list_my_attendance_events", arguments={}
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("remove", "duration", "summary", "expected"),
    [
        (None, None, {"page_duration_minutes": 0, "period_duration_minutes": 0}, None),
        (None, 0, {"page_duration_minutes": 0, "period_duration_minutes": 0}, None),
        (
            "duration_minutes",
            0,
            {"page_duration_minutes": 0, "period_duration_minutes": 0},
            ValidationError,
        ),
        ("summary", 0, {"page_duration_minutes": 0, "period_duration_minutes": 0}, ValidationError),
        (
            "page_duration_minutes",
            0,
            {"page_duration_minutes": 0, "period_duration_minutes": 0},
            ValidationError,
        ),
        (
            "period_duration_minutes",
            0,
            {"page_duration_minutes": 0, "period_duration_minutes": 0},
            ValidationError,
        ),
    ],
)
async def test_history_session_requires_each_metric_and_accepts_null_or_zero(
    remove: str | None,
    duration: int | None,
    summary: dict[str, int],
    expected: type[ValidationError] | None,
) -> None:
    item: dict[str, object] = {
        "attendance_event_id": 7,
        "employee_id": 9,
        "punch_type": "Office",
        "location": "HQ",
        "checked_in_at": "2026-08-10T08:00:00+02:00",
        "checked_out_at": "2026-08-10T16:00:00+02:00",
        "note": None,
        "duration_minutes": duration,
    }
    payload: dict[str, object] = {
        "items": [item],
        "limit": 50,
        "offset": 0,
        "next_offset": None,
        "summary": summary,
    }
    if remove == "duration_minutes":
        del item[remove]
    elif remove in {"page_duration_minutes", "period_duration_minutes"}:
        del summary[remove]
    elif remove == "summary":
        del payload[remove]
    client = SimpleNamespace(
        call_tool=AsyncMock(
            return_value=SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps(payload))], isError=False
            )
        )
    )

    if expected is not None:
        with pytest.raises(expected):
            await StreamableHttpAttendanceSession(session=client).call_tool(
                name="list_my_attendance_events", arguments={}
            )
    else:
        page = await StreamableHttpAttendanceSession(session=client).call_tool(
            name="list_my_attendance_events", arguments={}
        )
        assert page.items[0].duration_minutes == duration
        assert page.summary.model_dump() == {
            "page_duration_minutes": 0,
            "period_duration_minutes": 0,
        }
