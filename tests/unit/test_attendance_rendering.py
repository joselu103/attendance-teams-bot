from datetime import UTC, datetime

from attendance_teams_bot.agent.rendering import render_attendance_page
from attendance_teams_bot.mcp.contracts import AttendanceEvent, AttendanceEventPage


def _event(
    identifier: int,
    *,
    punch_type: str | None = "Office",
    location: str | None = "Company",
    checked_in_at: datetime | None = datetime(2026, 8, 10, 8, tzinfo=UTC),
    checked_out_at: datetime | None = datetime(2026, 8, 10, 16, tzinfo=UTC),
) -> AttendanceEvent:
    return AttendanceEvent(
        attendance_event_id=identifier,
        employee_id=7,
        punch_type=punch_type,
        location=location,
        checked_in_at=checked_in_at,
        checked_out_at=checked_out_at,
        note=None,
    )


def _page(*items: AttendanceEvent, next_offset: int | None = None) -> AttendanceEventPage:
    return AttendanceEventPage(items=items, limit=50, offset=0, next_offset=next_offset)


def test_rendering_groups_offset_local_dates_orders_events_and_hides_offsets() -> None:
    result = render_attendance_page(
        _page(
            _event(2, checked_in_at=datetime(2026, 8, 11, 9, tzinfo=UTC)),
            _event(1, checked_in_at=datetime(2026, 8, 10, 8, tzinfo=UTC)),
        )
    )

    assert result == "\n".join(
        (
            "**Attendance: August 10, 2026–August 11, 2026**",
            "**Monday, August 10, 2026**",
            "- 08:00–16:00: Office (Company)",
            "**Tuesday, August 11, 2026**",
            "- 09:00–16:00: Office (Company)",
        )
    )
    assert "+00:00" not in result


def test_rendering_localizes_types_active_partial_unknown_and_pagination() -> None:
    result = render_attendance_page(
        _page(
            _event(1, punch_type="Remote work", location="Dom", checked_out_at=None),
            _event(2, punch_type="*Other*", location="[x]", checked_in_at=None),
            next_offset=50,
        ),
        language="sl",
    )

    assert "**Prisotnost: 10 avgust 2026–10 avgust 2026**" in result
    assert "- 08:00–Prisoten (v teku): Delo na daljavo (Dom) *(Aktivno)*" in result
    assert "- Začetni čas ni na voljo–16:00: \\*Other\\* (\\[x\\])" in result
    assert "Prikazanih je prvih 50 dogodkov" in result


def test_rendering_handles_no_data() -> None:
    assert render_attendance_page(_page(), language="sl") == (
        "Za to obdobje ni evidentiranih dogodkov prisotnosti."
    )
