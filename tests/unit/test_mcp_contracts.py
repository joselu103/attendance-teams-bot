from datetime import datetime

import pytest
from pydantic import ValidationError

from attendance_teams_bot.mcp.contracts import AttendanceEventPage, McpToolFailure


def _history_payload() -> dict[str, object]:
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
        "limit": 50,
        "offset": 0,
        "next_offset": None,
        "summary": {"page_duration_minutes": 480, "period_duration_minutes": 480},
    }


def test_tool_failure_rejects_noncanonical_diagnostics() -> None:
    with pytest.raises(ValidationError, match="safe message"):
        McpToolFailure.model_validate(
            {
                "code": "FORBIDDEN",
                "message": "Denied for alice@example.test by policy row 42",
            }
        )


def test_attendance_event_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="UTC offset"):
        AttendanceEventPage.model_validate(
            {
                "items": [
                    {
                        "attendance_event_id": 100,
                        "employee_id": 42,
                        "punch_type": "Remote work",
                        "location": "Home",
                        "checked_in_at": "2026-08-10T08:00:00",
                        "checked_out_at": None,
                        "note": None,
                        "duration_minutes": None,
                    }
                ],
                "limit": 50,
                "offset": 0,
                "next_offset": None,
            }
        )


def test_attendance_event_page_parses_the_requester_scoped_wire_payload() -> None:
    page = AttendanceEventPage.model_validate(_history_payload())

    assert page.items[0].checked_in_at == datetime.fromisoformat("2026-08-10T08:00:00+02:00")
    assert page.items[0].employee_id == 42


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("items", 0, "duration_minutes"), marker)
        for marker in (None, 0, 480, "480", 480.0, True, -1)
    ]
    + [
        (("summary", field), marker)
        for field in ("page_duration_minutes", "period_duration_minutes")
        for marker in (0, 480, "480", 480.0, True, -1, None)
    ],
)
def test_history_metrics_strictly_accept_only_nullable_item_or_nonnegative_summary_integers(
    path: tuple[object, ...], value: object
) -> None:
    payload = _history_payload()
    target: object = payload
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    accepts = (path == ("items", 0, "duration_minutes") and value is None) or (
        type(value) is int and value in {0, 480}
    )
    if accepts:
        AttendanceEventPage.model_validate(payload)
    else:
        with pytest.raises(ValidationError):
            AttendanceEventPage.model_validate(payload)


@pytest.mark.parametrize(
    "missing",
    [
        ("items", 0, "duration_minutes"),
        ("summary",),
        ("summary", "page_duration_minutes"),
        ("summary", "period_duration_minutes"),
    ],
)
def test_history_metrics_are_required_on_the_wire(missing: tuple[object, ...]) -> None:
    payload = _history_payload()
    target: object = payload
    for key in missing[:-1]:
        target = target[key]  # type: ignore[index]
    del target[missing[-1]]  # type: ignore[index]

    with pytest.raises(ValidationError):
        AttendanceEventPage.model_validate(payload)


def test_tool_failure_rejects_a_malformed_server_payload() -> None:
    with pytest.raises(ValidationError):
        McpToolFailure.model_validate({"code": "FORBIDDEN"})
