from datetime import datetime

import pytest
from pydantic import ValidationError

from attendance_teams_bot.mcp.contracts import AttendanceEventPage, McpToolFailure


def test_attendance_event_page_parses_the_requester_scoped_wire_payload() -> None:
    page = AttendanceEventPage.model_validate(
        {
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
            "limit": 50,
            "offset": 0,
            "next_offset": None,
        }
    )

    assert page.items[0].checked_in_at == datetime.fromisoformat("2026-08-10T08:00:00+02:00")
    assert page.items[0].employee_id == 42


def test_tool_failure_rejects_a_malformed_server_payload() -> None:
    with pytest.raises(ValidationError):
        McpToolFailure.model_validate({"code": "FORBIDDEN"})
