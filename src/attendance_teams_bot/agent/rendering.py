from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from attendance_teams_bot.agent.contracts import ReplyLanguage
from attendance_teams_bot.mcp.contracts import AttendanceEvent, AttendanceEventPage

UNAVAILABLE_REPLY = "Attendance data is temporarily unavailable. Please try again later."
TOOL_FAILURE_REPLIES = {
    "INVALID_ARGUMENT": "Check the attendance date range and try again.",
    "FORBIDDEN": "You do not have permission to view that attendance.",
    "IDENTITY_UNMAPPED": (
        "Your Teams account is not linked to an active attendance employee. "
        "Contact an administrator."
    ),
    "IDENTITY_AMBIGUOUS": "Your Teams account cannot be linked safely. Contact an administrator.",
    "BACKEND_UNAVAILABLE": UNAVAILABLE_REPLY,
    "AUTHENTICATION_REQUIRED": "Please sign in and try again.",
    "TOKEN_INVALID": "Please sign in and try again.",
    "INTERNAL_ERROR": UNAVAILABLE_REPLY,
    "CORRELATION_ID_INVALID": UNAVAILABLE_REPLY,
}


_MONTHS: dict[ReplyLanguage, tuple[str, ...]] = {
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
    "sl": (
        "januar",
        "februar",
        "marec",
        "april",
        "maj",
        "junij",
        "julij",
        "avgust",
        "september",
        "oktober",
        "november",
        "december",
    ),
}
_WEEKDAYS: dict[ReplyLanguage, tuple[str, ...]] = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "sl": ("ponedeljek", "torek", "sreda", "četrtek", "petek", "sobota", "nedelja"),
}
_TYPE_LABELS: dict[ReplyLanguage, dict[str, str]] = {
    "en": {
        "office": "Office",
        "work": "Work",
        "remote work": "Remote work",
        "work from home": "Remote work",
        "break": "Break",
        "leave": "Leave",
    },
    "sl": {
        "office": "Pisarna",
        "work": "Delo",
        "remote work": "Delo na daljavo",
        "work from home": "Delo na daljavo",
        "break": "Odmor",
        "leave": "Odsotnost",
    },
}


def render_attendance_page(page: AttendanceEventPage, *, language: ReplyLanguage = "en") -> str:
    if not page.items:
        return _copy(
            language,
            "No attendance events were found for that date range.",
            "Za to obdobje ni evidentiranih dogodkov prisotnosti.",
        )
    groups: defaultdict[date | None, list[AttendanceEvent]] = defaultdict(list)
    for event in page.items:
        groups[_event_date(event)].append(event)
    dated_groups = sorted(day for day in groups if day is not None)
    response_parts = [_range_heading(dated_groups, language)] if dated_groups else []
    for day in dated_groups:
        response_parts.append(f"**{_format_date(day, language, weekday=True)}**")
        response_parts.extend(
            _render_attendance_event(event, language) for event in _sort_events(groups[day])
        )
    if None in groups:
        response_parts.append(f"**{_copy(language, 'Date unavailable', 'Datum ni na voljo')}**")
        response_parts.extend(
            _render_attendance_event(event, language) for event in _sort_events(groups[None])
        )
    response = "\n".join(part for part in response_parts if part)
    if page.next_offset is not None:
        response += "\n" + _copy(
            language,
            "Showing the first 50 events; more events are available.",
            "Prikazanih je prvih 50 dogodkov; na voljo jih je še več.",
        )
    return response


def _render_attendance_event(event: AttendanceEvent, language: ReplyLanguage) -> str:
    if event.checked_in_at is None and event.checked_out_at is None:
        time_range = _copy(language, "Time unavailable", "Čas ni na voljo")
    elif event.checked_in_at is None:
        start = _copy(language, "Start time unavailable", "Začetni čas ni na voljo")
        time_range = f"{start}–{_time(event.checked_out_at)}"
    elif event.checked_out_at is None:
        present = _copy(language, "Present (In Progress)", "Prisoten (v teku)")
        time_range = f"{_time(event.checked_in_at)}–{present}"
    else:
        time_range = f"{_time(event.checked_in_at)}–{_time(event.checked_out_at)}"
    attendance_type = _attendance_type(event.punch_type, language)
    location = _safe_text(event.location)
    result = f"- {time_range}: {attendance_type}"
    if location:
        result += f" ({location})"
    if event.checked_in_at is not None and event.checked_out_at is None:
        result += " *(Active)*" if language == "en" else " *(Aktivno)*"
    return result


def _event_date(event: AttendanceEvent) -> date | None:
    timestamp = event.checked_in_at or event.checked_out_at
    return timestamp.date() if timestamp is not None else None


def _sort_events(events: list[AttendanceEvent]) -> list[AttendanceEvent]:
    return sorted(
        events,
        key=lambda event: (
            event.checked_in_at or event.checked_out_at or datetime.max.replace(tzinfo=None),
            event.attendance_event_id,
        ),
    )


def _range_heading(days: list[date], language: ReplyLanguage) -> str:
    if not days:
        return ""
    start, end = days[0], days[-1]
    if language == "sl":
        return f"**Prisotnost: {_format_date(start, language)}–{_format_date(end, language)}**"
    return f"**Attendance: {_format_date(start, language)}–{_format_date(end, language)}**"


def _format_date(value: date, language: ReplyLanguage, *, weekday: bool = False) -> str:
    day = f"{value.day} {_MONTHS[language][value.month - 1]} {value.year}"
    if language == "en":
        day = f"{_MONTHS[language][value.month - 1]} {value.day}, {value.year}"
    return f"{_WEEKDAYS[language][value.weekday()]}, {day}" if weekday else day


def _time(value: datetime | None) -> str:
    assert value is not None
    return value.strftime("%H:%M")


def _attendance_type(value: str | None, language: ReplyLanguage) -> str:
    if value is None or not value.strip():
        return _copy(language, "Unspecified attendance", "Nedoločena prisotnost")
    normalized = " ".join(value.split()).casefold()
    return _TYPE_LABELS[language].get(normalized, _safe_text(value)) or _copy(
        language, "Unspecified attendance", "Nedoločena prisotnost"
    )


def _safe_text(value: str | None) -> str:
    if value is None:
        return ""
    normalized = " ".join(value.split())[:160]
    replacements: dict[str, str | int | None] = {
        character: f"\\{character}" for character in r"\\`*_{}[]<>#()+-.!|"
    }
    return normalized.translate(str.maketrans(replacements))


def _copy(language: ReplyLanguage, english: str, slovene: str) -> str:
    return slovene if language == "sl" else english
