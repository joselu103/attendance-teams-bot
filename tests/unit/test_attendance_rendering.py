from datetime import UTC, datetime

import pytest

import attendance_teams_bot.agent.rendering as rendering
from attendance_teams_bot.agent.contracts import ReplyLanguage
from attendance_teams_bot.agent.language_model import PresentationPlan
from attendance_teams_bot.agent.rendering import (
    MAX_REPLY_CHARACTERS,
    TOOL_FAILURE_REPLIES,
    AttendanceResultPresenter,
    CatalogUnavailablePresentation,
    ClarificationPresentation,
    CurrentAttendancePresentation,
    GuidancePresentation,
    InvalidRequestPresentation,
    SafeHistoryPresentation,
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
            SafeHistoryPresentation(
                events=events,
                language=language,
                display_name=None,
                plan=None,
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
            "**Part 1 of 1**",
            "**Attendance**",
            "**Monday, August 10, 2026**",
            "- 10:00–18:00: Office",
            "**Tuesday, August 11, 2026**",
            "- 11:00–18:00: Office",
            "**End of results**",
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

    assert "**Prisotnost**" in result
    assert "- 10:00–Prisoten (v teku): Delo od doma *(Aktivno)*" in result
    assert "- Začetni čas ni na voljo–18:00: \\*Other\\*" in result


def test_presentation_translates_only_controlled_attendance_type_aliases() -> None:
    result = _present_events(_event(1, punch_type="Delo na firmi", location="Lokacija"))

    assert "Office" in result and "Lokacija" not in result


_PUNCH_TYPE_EQUIVALENTS = (
    ("Delo na firmi", "Office"),
    ("Delo od doma", "Remote work"),
    ("Delo pri stranki", "Customer site"),
    ("Na malici", "Lunch break"),
    ("Dopust", "Leave"),
    ("Bolniška", "Sick leave"),
    ("Nega otroka", "Childcare leave"),
    ("Izredni dopust", "Emergency leave"),
    ("Neplačani dopust", "Unpaid leave"),
    ("Darovanje krvi", "Blood donation leave"),
    ("Spremstvo", "Accompaniment leave"),
    ("Očetovski dopust", "Paternity leave"),
    ("Porodniška", "Maternity leave"),
)


@pytest.mark.parametrize(("slovene", "english"), _PUNCH_TYPE_EQUIVALENTS)
def test_presentation_translates_every_database_slovenian_label_to_english(
    slovene: str, english: str
) -> None:
    assert english in _present_events(_event(1, punch_type=slovene), language="en")


@pytest.mark.parametrize(("slovene", "english"), _PUNCH_TYPE_EQUIVALENTS)
def test_presentation_translates_english_to_the_exact_database_slovenian_label(
    slovene: str, english: str
) -> None:
    assert slovene in _present_events(_event(1, punch_type=english), language="sl")


def test_presentation_separates_date_blocks_and_keeps_same_day_events_contiguous() -> None:
    result = _present_events(
        _event(1, checked_in_at=datetime(2026, 8, 10, 8, tzinfo=UTC)),
        _event(2, checked_in_at=datetime(2026, 8, 10, 9, tzinfo=UTC)),
        _event(3, checked_in_at=datetime(2026, 8, 11, 8, tzinfo=UTC)),
    )

    assert result == (
        "**Part 1 of 1**\n\n**Attendance**\n\n"
        "**Monday, August 10, 2026**\n\n"
        "- 10:00–18:00: Office\n"
        "- 11:00–18:00: Office\n\n"
        "**Tuesday, August 11, 2026**\n\n"
        "- 10:00–18:00: Office\n\n**End of results**"
    )


def test_presentation_reports_no_data_and_keeps_every_event_in_a_history_page() -> None:
    presenter = AttendanceResultPresenter()
    no_data = presenter.present(
        SafeHistoryPresentation(events=(), language="sl", display_name=None, plan=None)
    )
    response = presenter.present(
        SafeHistoryPresentation(
            events=tuple(_event(index) for index in range(50)),
            language="en",
            display_name=None,
            plan=None,
        )
    )
    assert no_data.text == "Za to obdobje ni evidentiranih dogodkov prisotnosti."
    assert all(len(message) <= MAX_REPLY_CHARACTERS for message in response.messages)
    assert "\n".join(response.messages).count("10:00–18:00: Office") == 50
    assert "omitted" not in "\n".join(response.messages)


@pytest.mark.parametrize(
    ("presentation", "expected"),
    [
        (
            ClarificationPresentation(language="en", display_name=None),
            "Please restate the complete attendance period with both start and end dates.",
        ),
        (
            GuidancePresentation(intent="unsupported", language="sl", display_name=None),
            "Lahko vam prikažem dogodke vaše prisotnosti za določeno obdobje.",
        ),
        (
            InvalidRequestPresentation(language="sl", display_name=None),
            "Prosimo, navedite veljavno obdobje prisotnosti z začetnim in končnim datumom.",
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


def test_catalog_unavailable_reply_is_localized() -> None:
    response = AttendanceResultPresenter().present(
        CatalogUnavailablePresentation(language="sl", display_name=None)
    )

    assert response.text == "Podatki o prisotnosti trenutno niso na voljo. Poskusite znova pozneje."


def test_safe_history_batches_only_at_complete_chronological_date_groups(monkeypatch) -> None:
    monkeypatch.setattr(rendering, "MAX_REPLY_CHARACTERS", 150)
    response = AttendanceResultPresenter().present(
        SafeHistoryPresentation(
            events=(
                _event(1, checked_in_at=datetime(2026, 8, 10, 8, tzinfo=UTC)),
                _event(2, checked_in_at=datetime(2026, 8, 11, 8, tzinfo=UTC)),
            ),
            language="en",
            display_name=None,
            plan=PresentationPlan("Attendance", "Returned records"),
        )
    )

    assert len(response.messages) == 2
    assert "Monday, August 10, 2026" in response.messages[0]
    assert "Tuesday, August 11, 2026" in response.messages[1]
    assert "Part 1 of 2" in response.messages[0]
    assert "End of results" in response.messages[1]
    assert all(
        "Company" not in message and "Internal" not in message for message in response.messages
    )


def test_current_attendance_exposes_only_status_grouped_names() -> None:
    response = AttendanceResultPresenter().present(
        CurrentAttendancePresentation(
            status_names=(("office", ("Ada Example",)), ("remote", ("Blaž Example",))),
            language="sl",
            display_name=None,
            plan=PresentationPlan("Trenutna prisotnost", None),
        )
    )

    assert "**Trenutna prisotnost**" in response.text
    assert "**Delo na firmi**\n- Ada Example" in response.text
    assert "**Delo od doma**\n- Blaž Example" in response.text
    assert "Konec rezultatov" in response.text


def test_current_status_splits_long_name_groups_without_dropping_records(monkeypatch) -> None:
    monkeypatch.setattr(rendering, "MAX_REPLY_CHARACTERS", 160)
    names = tuple(f"Employee {index}" for index in range(20))
    response = AttendanceResultPresenter().present(
        CurrentAttendancePresentation(
            status_names=(("office", names),),
            language="en",
            display_name=None,
            plan=PresentationPlan("Current status", None),
        )
    )

    assert len(response.messages) > 1
    combined = "\n".join(response.messages)
    assert all(name in combined for name in names)
    assert "End of results" in response.messages[-1]
    assert all(len(message) <= 160 for message in response.messages)
