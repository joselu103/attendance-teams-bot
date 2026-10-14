from datetime import UTC, date, datetime, timedelta, timezone

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
from attendance_teams_bot.mcp.contracts import (
    AttendanceEvent,
    AttendanceEventSummary,
    McpToolErrorCode,
)

_ZERO_SUMMARY = AttendanceEventSummary(page_duration_minutes=0, period_duration_minutes=0)
_HISTORY_DIVIDER = rendering._HISTORY_DIVIDER

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
        duration_minutes=(
            int(
                (checked_out_at.astimezone(UTC) - checked_in_at.astimezone(UTC)).total_seconds()
                // 60
            )
            if checked_in_at is not None
            and checked_out_at is not None
            and checked_out_at.astimezone(UTC) >= checked_in_at.astimezone(UTC)
            else None
        ),
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
                summary=_ZERO_SUMMARY,
            )
        )
        .text
    )


def test_presentation_groups_dates_orders_events_and_hides_sensitive_fields() -> None:
    result = _present_events(
        _event(
            2,
            checked_in_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
            checked_out_at=datetime(2026, 8, 11, 16, tzinfo=UTC),
        ),
        _event(1, checked_in_at=datetime(2026, 8, 10, 8, tzinfo=UTC)),
    )

    assert result == "\n".join(
        (
            f"📅 **Attendance Record** (Page 1)<br>{_HISTORY_DIVIDER}",
            "* 🗓️ **Mon, Aug 10, 2026**",
            "  * `10:00–18:00` — 🏢 Office *(Total: 8h 00m)*",
            "",
            "* 🗓️ **Tue, Aug 11, 2026**",
            "  * `11:00–18:00` — 🏢 Office *(Total: 7h 00m)*",
            "",
            f"{_HISTORY_DIVIDER}<br>Period Total: 0h 00m<br>End of results.",
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

    assert "📅 **Evidenca prisotnosti** (Stran 1)" in result
    assert "`10:00–V teku` — 🏠 Delo od doma *(Seštevek: ni na voljo)*" in result
    assert "`Začetni čas ni na voljo–18:00` — \\*Other\\* *(Seštevek: ni na voljo)*" in result
    assert "Seštevek obdobja: 0h 00m" in result


def test_presentation_translates_only_controlled_attendance_type_aliases() -> None:
    result = _present_events(_event(1, punch_type="Delo na firmi", location="Lokacija"))

    assert "Office" in result and "Lokacija" not in result


def test_daily_total_sums_breaks_and_excludes_incomplete_entries() -> None:
    result = _present_events(
        _event(1, checked_out_at=datetime(2026, 8, 10, 12, tzinfo=UTC)),
        _event(
            2,
            punch_type="Lunch break",
            checked_in_at=datetime(2026, 8, 10, 12, tzinfo=UTC),
            checked_out_at=datetime(2026, 8, 10, 12, 15, tzinfo=UTC),
        ),
        _event(3, checked_out_at=None),
        _event(4, checked_in_at=None),
        _event(5, checked_in_at=None, checked_out_at=None),
    )
    assert "*(Total: 0h 15m)*" in result
    assert "🍽️ Lunch break" in result
    assert result.count("Total: unavailable") == 3
    assert "08:00" not in result  # timestamps are rendered in Ljubljana time
    assert "Date unavailable" in result


@pytest.mark.parametrize(("label", "icon"), tuple(rendering._PUNCH_TYPE_ICONS.items()))
def test_known_types_receive_their_icon_in_both_languages(label: str, icon: str) -> None:
    for language in ("en", "sl"):
        assert f"— {icon} " in _present_events(_event(1, punch_type=label), language=language)


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

    assert "* 🗓️ **Mon, Aug 10, 2026**" in result
    assert result.count("  * `") == 3
    assert "*(Total: 15h 00m)*" not in result
    assert "*(Total: 7h 00m)*" in result
    assert "* 🗓️ **Tue, Aug 11, 2026**" in result
    assert "**Part" not in result


def test_presentation_splits_multiday_events_into_local_daily_segments() -> None:
    result = (
        AttendanceResultPresenter()
        .present(
            SafeHistoryPresentation(
                events=(
                    _event(
                        1,
                        punch_type="Remote work",
                        checked_in_at=datetime(2026, 5, 22, 8, tzinfo=timezone(timedelta(hours=2))),
                        checked_out_at=datetime(
                            2026, 5, 23, 16, tzinfo=timezone(timedelta(hours=2))
                        ),
                    ),
                ),
                language="en",
                display_name=None,
                plan=None,
                summary=_ZERO_SUMMARY,
                start_date=date(2026, 5, 22),
                end_date=date(2026, 5, 23),
            )
        )
        .text
    )

    assert "Fri, May 22, 2026**\n  * `08:00–24:00` — 🏠 Remote work" in result
    assert "Sat, May 23, 2026**\n  * `00:00–16:00` — 🏠 Remote work" in result
    assert result.count("*(Total: 16h 00m)*") == 2


def test_null_duration_remains_unavailable_on_split_completed_rows() -> None:
    event = _event(
        1,
        checked_in_at=datetime(2026, 5, 22, 8, tzinfo=UTC),
        checked_out_at=datetime(2026, 5, 23, 8, tzinfo=UTC),
    ).model_copy(update={"duration_minutes": None})
    result = _present_events(event)

    assert result.count("*(Total: unavailable)*") == 2


def test_whole_rows_trust_api_minutes_and_midnight_segments_truncate_independently() -> None:
    whole = _event(1).model_copy(update={"duration_minutes": 123})
    midnight = _event(
        2,
        checked_in_at=datetime(2026, 8, 10, 23, 59, 30, tzinfo=timezone(timedelta(hours=2))),
        checked_out_at=datetime(2026, 8, 11, 0, 0, 30, tzinfo=timezone(timedelta(hours=2))),
    )
    result = (
        AttendanceResultPresenter()
        .present(
            SafeHistoryPresentation(
                events=(whole, midnight),
                language="en",
                display_name=None,
                plan=None,
                summary=AttendanceEventSummary(
                    page_duration_minutes=124, period_duration_minutes=124
                ),
                start_date=date(2026, 8, 10),
                end_date=date(2026, 8, 11),
            )
        )
        .text
    )

    assert "`10:00–18:00` — 🏢 Office *(Total: 2h 03m)*" in result
    assert result.count("*(Total: 0h 00m)*") == 2
    assert "Period Total: 2h 04m" in result


@pytest.mark.parametrize(
    ("language", "total", "next_page", "old_daily"),
    [
        ("en", "Period Total: 7h 05m", 'Type "next" for the next page.', "Daily Total"),
        (
            "sl",
            "Seštevek obdobja: 7h 05m",
            'Za naslednjo stran vnesite "naslednja".',
            "Dnevni seštevek",
        ),
    ],
)
def test_empty_intermediate_history_pages_keep_the_nonzero_localized_period_total(
    language: ReplyLanguage, total: str, next_page: str, old_daily: str
) -> None:
    result = (
        AttendanceResultPresenter()
        .present(
            SafeHistoryPresentation(
                events=(),
                language=language,
                display_name=None,
                plan=None,
                summary=AttendanceEventSummary(
                    page_duration_minutes=0, period_duration_minutes=425
                ),
                has_more=True,
            )
        )
        .text
    )

    assert total in result
    assert result.endswith(f"{total}<br>{next_page}")
    assert old_daily not in result
    assert "excluded" not in result


@pytest.mark.parametrize(
    ("language", "total"),
    [("en", "Period Total: 8h 00m"), ("sl", "Seštevek obdobja: 8h 00m")],
)
def test_split_history_page_puts_one_nonzero_footer_in_its_last_message(
    monkeypatch: pytest.MonkeyPatch, language: ReplyLanguage, total: str
) -> None:
    null_duration = _event(1).model_copy(update={"duration_minutes": None})
    presentation = SafeHistoryPresentation(
        events=(
            null_duration,
            _event(2, checked_in_at=datetime(2026, 8, 11, 8, tzinfo=UTC)),
        ),
        language=language,
        display_name=None,
        plan=None,
        summary=AttendanceEventSummary(page_duration_minutes=480, period_duration_minutes=480),
    )
    presenter = AttendanceResultPresenter()
    monkeypatch.setattr(
        rendering, "MAX_REPLY_CHARACTERS", len(presenter.present(presentation).text) - 1
    )
    response = presenter.present(presentation)

    assert len(response.messages) == 2
    assert sum(total in message for message in response.messages) == 1
    assert total in response.messages[-1]
    unavailable = "Total: unavailable" if language == "en" else "Seštevek: ni na voljo"
    assert unavailable in "\n".join(response.messages)


def test_history_presentation_requires_an_authoritative_summary() -> None:
    with pytest.raises(TypeError, match="summary"):
        SafeHistoryPresentation(events=(), language="en", display_name=None, plan=None)  # type: ignore[call-arg]


def test_presentation_clips_multiday_events_to_the_requested_period() -> None:
    result = (
        AttendanceResultPresenter()
        .present(
            SafeHistoryPresentation(
                events=(
                    _event(
                        1,
                        checked_in_at=datetime(
                            2026, 5, 21, 20, tzinfo=timezone(timedelta(hours=2))
                        ),
                        checked_out_at=datetime(
                            2026, 5, 23, 4, tzinfo=timezone(timedelta(hours=2))
                        ),
                    ),
                ),
                language="en",
                display_name=None,
                plan=None,
                summary=_ZERO_SUMMARY,
                start_date=date(2026, 5, 22),
                end_date=date(2026, 5, 22),
            )
        )
        .text
    )

    assert "Fri, May 22, 2026" in result
    assert "Thu, May 21, 2026" not in result
    assert "Sat, May 23, 2026" not in result
    assert "`00:00–24:00`" in result
    assert "*(Total: 24h 00m)*" in result


def test_presentation_uses_elapsed_time_and_offsets_for_dst_fallback() -> None:
    result = _present_events(
        _event(
            1,
            checked_in_at=datetime(2026, 10, 25, 2, 30, tzinfo=timezone(timedelta(hours=2))),
            checked_out_at=datetime(2026, 10, 25, 2, 30, tzinfo=timezone(timedelta(hours=1))),
        )
    )

    assert "`02:30 UTC+02:00–02:30 UTC+01:00`" in result
    assert "*(Total: 1h 00m)*" in result


def test_presentation_uses_elapsed_time_for_dst_spring_forward() -> None:
    result = _present_events(
        _event(
            1,
            checked_in_at=datetime(2026, 3, 29, 1, 30, tzinfo=timezone(timedelta(hours=1))),
            checked_out_at=datetime(2026, 3, 29, 3, 30, tzinfo=timezone(timedelta(hours=2))),
        )
    )

    assert "`01:30–03:30`" in result
    assert "*(Total: 1h 00m)*" in result


def test_presentation_preserves_nonpositive_events_without_splitting() -> None:
    result = _present_events(
        _event(
            1,
            checked_in_at=datetime(2026, 5, 22, 16, tzinfo=timezone(timedelta(hours=2))),
            checked_out_at=datetime(2026, 5, 22, 8, tzinfo=timezone(timedelta(hours=2))),
        )
    )

    assert result.count("  * `") == 1
    assert "`16:00–08:00`" in result
    assert "*(Total: unavailable)*" in result


def test_presentation_reports_no_data_and_keeps_every_event_in_a_history_page() -> None:
    presenter = AttendanceResultPresenter()
    no_data = presenter.present(
        SafeHistoryPresentation(
            events=(), language="sl", display_name=None, plan=None, summary=_ZERO_SUMMARY
        )
    )
    response = presenter.present(
        SafeHistoryPresentation(
            events=tuple(_event(index) for index in range(50)),
            language="en",
            display_name=None,
            plan=None,
            summary=_ZERO_SUMMARY,
        )
    )
    assert no_data.text == "\n".join(
        (
            f"📅 **Evidenca prisotnosti** (Stran 1)<br>{_HISTORY_DIVIDER}",
            "Ni najdenih evidenc prisotnosti.",
            (f"{_HISTORY_DIVIDER}<br>Seštevek obdobja: 0h 00m<br>Konec rezultatov."),
        )
    )
    assert all(len(message) <= MAX_REPLY_CHARACTERS for message in response.messages)
    assert "\n".join(response.messages).count("`10:00–18:00` — 🏢 Office") == 50
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


def test_guidance_replies_greet_with_the_full_escaped_display_name() -> None:
    presenter = AttendanceResultPresenter()

    greeted = presenter.present(
        ClarificationPresentation(language="en", display_name="  Ana *Example*  ")
    )
    catalog = presenter.present(
        CatalogUnavailablePresentation(language="en", display_name="Ana Example")
    )

    assert greeted.text == (
        "Hello, Ana \\*Example\\*<br>"
        "Please restate the complete attendance period with both start and end dates."
    )
    assert catalog.text == (
        "Hello, Ana Example<br>Attendance data is temporarily unavailable. Please try again later."
    )


def test_reports_never_greet_even_when_a_display_name_is_available() -> None:
    presenter = AttendanceResultPresenter()

    history = presenter.present(
        SafeHistoryPresentation(
            events=(_event(1),),
            language="en",
            display_name="Ana Example",
            plan=None,
            summary=_ZERO_SUMMARY,
        )
    )
    current = presenter.present(
        CurrentAttendancePresentation(
            status_names=(),
            language="en",
            display_name="Ana Example",
            plan=PresentationPlan("Current status", None),
        )
    )

    assert not history.text.startswith("Hello,")
    assert current.text == "No current attendance records were found."


def test_history_header_and_footer_use_forced_teams_line_breaks() -> None:
    response = AttendanceResultPresenter().present(
        SafeHistoryPresentation(
            events=(_event(1),),
            language="en",
            display_name=None,
            plan=None,
            summary=_ZERO_SUMMARY,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 10, 10),
            has_more=True,
        )
    )

    assert response.text.startswith(
        "📅 **Attendance Record** (Page 1)<br>"
        "Period: 2026-01-01 to 2026-10-10<br>"
        f"Timezone: Europe/Ljubljana<br>{_HISTORY_DIVIDER}"
    )
    assert response.text.endswith(
        f'{_HISTORY_DIVIDER}<br>Period Total: 0h 00m<br>Type "next" for the next page.'
    )


def test_catalog_unavailable_reply_is_localized() -> None:
    response = AttendanceResultPresenter().present(
        CatalogUnavailablePresentation(language="sl", display_name=None)
    )

    assert response.text == "Podatki o prisotnosti trenutno niso na voljo. Poskusite znova pozneje."


def test_safe_history_batches_only_at_complete_chronological_date_groups(monkeypatch) -> None:
    monkeypatch.setattr(rendering, "MAX_REPLY_CHARACTERS", 260)
    response = AttendanceResultPresenter().present(
        SafeHistoryPresentation(
            events=(
                _event(1, checked_in_at=datetime(2026, 8, 10, 8, tzinfo=UTC)),
                _event(2, checked_in_at=datetime(2026, 8, 11, 8, tzinfo=UTC)),
            ),
            language="en",
            display_name=None,
            plan=PresentationPlan("Attendance", "Returned records"),
            summary=_ZERO_SUMMARY,
        )
    )

    assert len(response.messages) == 2
    assert "📅 **Attendance Record** (Page 1)" in response.messages[0]
    assert "Mon, Aug 10, 2026" in response.messages[0]
    assert "Tue, Aug 11, 2026" in response.messages[1]
    assert "Attendance Record" not in response.messages[1]
    assert "End of results." in response.messages[1]
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
