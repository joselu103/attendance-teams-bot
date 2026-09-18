from datetime import UTC, datetime

import pytest

from attendance_teams_bot.agent.contracts import ReplyLanguage
from attendance_teams_bot.agent.rendering import (
    MAX_REPLY_CHARACTERS,
    TOOL_FAILURE_REPLIES,
    AttendanceResultPresenter,
    CatalogUnavailablePresentation,
    ClarificationPresentation,
    EventResultPresentation,
    GuidancePresentation,
    InvalidRequestPresentation,
    ToolFailurePresentation,
    UnavailablePresentation,
)
from attendance_teams_bot.mcp.contracts import AttendanceEvent, McpToolErrorCode

_TOOL_FAILURE_CODES: tuple[McpToolErrorCode, ...] = (
    "INVALID_ARGUMENT",
    "FORBIDDEN",
    "IDENTITY_UNMAPPED",
    "IDENTITY_AMBIGUOUS",
    "BACKEND_UNAVAILABLE",
    "AUTHENTICATION_REQUIRED",
    "TOKEN_INVALID",
    "INTERNAL_ERROR",
    "CORRELATION_ID_INVALID",
)


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
        note="Internal note",
    )


def _present_events(*events: AttendanceEvent, language: ReplyLanguage = "en") -> str:
    return (
        AttendanceResultPresenter()
        .present(
            EventResultPresentation(
                events=events,
                records_omitted=False,
                language=language,
                display_name=None,
            )
        )
        .text
    )


def test_presentation_groups_dates_orders_events_and_hides_sensitive_fields() -> None:
    result = _present_events(
        _event(2, checked_in_at=datetime(2026, 8, 11, 9, tzinfo=UTC)),
        _event(1, checked_in_at=datetime(2026, 8, 10, 8, tzinfo=UTC)),
    )

    assert result == "\n\n".join(
        (
            "**Attendance: August 10, 2026–August 11, 2026**",
            "**Monday, August 10, 2026**",
            "- 08:00–16:00: Office (Company)",
            "**Tuesday, August 11, 2026**",
            "- 09:00–16:00: Office (Company)",
        )
    )
    assert "+00:00" not in result
    assert "Internal" not in result
    assert "attendance_event_id" not in result


def test_presentation_localizes_types_active_and_escaped_values() -> None:
    result = _present_events(
        _event(1, punch_type="Remote work", location="Dom", checked_out_at=None),
        _event(2, punch_type="*Other*", location="[x]", checked_in_at=None),
        language="sl",
    )

    assert "**Prisotnost: 10 avgust 2026–10 avgust 2026**" in result
    assert "- 08:00–Prisoten (v teku): Delo na daljavo (Dom) *(Aktivno)*" in result
    assert "- Začetni čas ni na voljo–16:00: \\*Other\\* (\\[x\\])" in result


def test_presentation_separates_date_blocks_and_keeps_same_day_events_contiguous() -> None:
    result = _present_events(
        _event(1, checked_in_at=datetime(2026, 8, 10, 8, tzinfo=UTC)),
        _event(2, checked_in_at=datetime(2026, 8, 10, 9, tzinfo=UTC)),
        _event(3, checked_in_at=datetime(2026, 8, 11, 8, tzinfo=UTC)),
    )

    assert result == (
        "**Attendance: August 10, 2026–August 11, 2026**\n\n"
        "**Monday, August 10, 2026**\n\n"
        "- 08:00–16:00: Office (Company)\n"
        "- 09:00–16:00: Office (Company)\n\n"
        "**Tuesday, August 11, 2026**\n\n"
        "- 08:00–16:00: Office (Company)"
    )


def test_presentation_reports_no_data_and_character_budget_omissions() -> None:
    presenter = AttendanceResultPresenter()

    no_data = presenter.present(
        EventResultPresentation(events=(), records_omitted=False, language="sl", display_name=None)
    )
    events = tuple(_event(index, location="x" * 160) for index in range(200))
    omitted = presenter.present(
        EventResultPresentation(
            events=events, records_omitted=False, language="en", display_name=None
        )
    )

    assert no_data.text == "Za to obdobje ni evidentiranih dogodkov prisotnosti."
    assert len(omitted.text) <= MAX_REPLY_CHARACTERS
    assert "additional records were omitted" in omitted.text
    assert "older" not in omitted.text
    assert "newer" not in omitted.text


@pytest.mark.parametrize(
    ("presentation", "expected"),
    [
        (
            ClarificationPresentation(language="en", display_name=None),
            "Please clarify the attendance date range you want to view.",
        ),
        (
            GuidancePresentation(intent="unsupported", language="sl", display_name=None),
            "Lahko vam prikažem dogodke vaše prisotnosti za določeno obdobje.",
        ),
        (
            InvalidRequestPresentation(language="sl", display_name=None),
            "Prosimo, navedite obdobje največ 31 dni.",
        ),
        (
            ToolFailurePresentation(code="FORBIDDEN", language="sl", display_name=None),
            "Nimate dovoljenja za ogled te prisotnosti.",
        ),
        (
            UnavailablePresentation(language="sl", display_name=None),
            "Podatki o prisotnosti trenutno niso na voljo. Poskusite znova pozneje.",
        ),
    ],
)
def test_presentation_owns_localized_safe_replies(
    presentation: ClarificationPresentation
    | GuidancePresentation
    | InvalidRequestPresentation
    | ToolFailurePresentation
    | UnavailablePresentation,
    expected: str,
) -> None:
    assert AttendanceResultPresenter().present(presentation).text == expected


@pytest.mark.parametrize("code", _TOOL_FAILURE_CODES)
def test_presentation_maps_every_tool_failure_code_to_safe_english_copy(
    code: McpToolErrorCode,
) -> None:
    presentation = ToolFailurePresentation(code=code, language="en", display_name=None)

    assert AttendanceResultPresenter().present(presentation).text == TOOL_FAILURE_REPLIES[code]


def test_presentation_greets_only_regular_reply_variants_and_escapes_display_name() -> None:
    presenter = AttendanceResultPresenter()

    greeted = presenter.present(
        ClarificationPresentation(language="en", display_name="  Ana *Example*  ")
    )
    catalog = presenter.present(
        CatalogUnavailablePresentation(language="en", display_name="Ana Example")
    )

    assert greeted.text.startswith("Hello, Ana \\*Example\\*!")
    assert catalog.text == "Attendance data is temporarily unavailable. Please try again later."
