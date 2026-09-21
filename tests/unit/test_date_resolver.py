from datetime import date

import pytest

from attendance_teams_bot.agent.date_resolver import resolve_attendance_range


@pytest.mark.parametrize(
    ("message", "start", "end", "language"),
    [
        ("from 2026-08-10 to 2026-08-12", date(2026, 8, 10), date(2026, 8, 12), "en"),
        ("August", date(2026, 8, 1), date(2026, 8, 31), "en"),
        ("avgusta", date(2026, 8, 1), date(2026, 8, 31), "sl"),
        ("February 2024", date(2024, 2, 1), date(2024, 2, 29), "en"),
        ("this month", date(2026, 9, 1), date(2026, 9, 15), "en"),
        ("prejšnji mesec", date(2026, 8, 1), date(2026, 8, 31), "sl"),
        ("this week", date(2026, 9, 14), date(2026, 9, 15), "en"),
        ("prejšnji teden", date(2026, 9, 7), date(2026, 9, 13), "sl"),
        ("last 3 months", date(2026, 6, 15), date(2026, 9, 15), "en"),
        ("zadnje 3 mesece", date(2026, 6, 15), date(2026, 9, 15), "sl"),
    ],
)
def test_resolves_supported_calendar_vocabulary(
    message: str, start: date, end: date, language: str
) -> None:
    resolution = resolve_attendance_range(message, reference_date=date(2026, 9, 15))

    assert resolution is not None
    assert resolution.range.start_date == start
    assert resolution.range.end_date == end
    assert resolution.language == language


def test_month_without_year_uses_most_recent_non_future_occurrence() -> None:
    resolution = resolve_attendance_range("January", reference_date=date(2026, 1, 2))

    assert resolution is not None
    assert resolution.range.start_date == date(2026, 1, 1)
    assert resolution.range.end_date == date(2026, 1, 2)


def test_unsupported_date_wording_uses_model_fallback() -> None:
    assert (
        resolve_attendance_range("the fortnight before payroll", reference_date=date(2026, 9, 15))
        is None
    )
