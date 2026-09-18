from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from attendance_teams_bot.agent.contracts import BotResponse, ReplyLanguage
from attendance_teams_bot.mcp.contracts import AttendanceEvent, McpToolErrorCode

MAX_REPLY_CHARACTERS = 12_000

UNAVAILABLE_REPLY = "Attendance data is temporarily unavailable. Please try again later."
INVALID_REQUEST_REPLY = "Please provide a date range of no more than 12 calendar months."
CLARIFICATION_REPLY = "Please clarify the attendance date range you want to view."
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

GuidanceIntent = Literal["unsupported", "date_ambiguous"]


@dataclass(frozen=True, slots=True)
class EventResultPresentation:
    events: tuple[AttendanceEvent, ...]
    records_omitted: bool
    language: ReplyLanguage
    display_name: str | None


@dataclass(frozen=True, slots=True)
class ClarificationPresentation:
    language: ReplyLanguage
    display_name: str | None


@dataclass(frozen=True, slots=True)
class GuidancePresentation:
    intent: GuidanceIntent
    language: ReplyLanguage
    display_name: str | None


@dataclass(frozen=True, slots=True)
class InvalidRequestPresentation:
    language: ReplyLanguage
    display_name: str | None


@dataclass(frozen=True, slots=True)
class ToolFailurePresentation:
    code: McpToolErrorCode
    language: ReplyLanguage
    display_name: str | None


@dataclass(frozen=True, slots=True)
class UnavailablePresentation:
    language: ReplyLanguage
    display_name: str | None


@dataclass(frozen=True, slots=True)
class CatalogUnavailablePresentation:
    language: ReplyLanguage
    display_name: str | None


AttendancePresentation = (
    EventResultPresentation
    | ClarificationPresentation
    | GuidancePresentation
    | InvalidRequestPresentation
    | ToolFailurePresentation
    | UnavailablePresentation
    | CatalogUnavailablePresentation
)


class AttendanceResultPresenter:
    """Build every safe, localized Attendance reply from a typed outcome."""

    def present(self, presentation: AttendancePresentation) -> BotResponse:
        if isinstance(presentation, EventResultPresentation):
            text = render_attendance_events(
                presentation.events,
                language=presentation.language,
                records_omitted=presentation.records_omitted,
            )
        elif isinstance(presentation, ClarificationPresentation):
            text = CLARIFICATION_REPLY
        elif isinstance(presentation, GuidancePresentation):
            text = _guidance_copy(presentation.intent, presentation.language)
        elif isinstance(presentation, InvalidRequestPresentation):
            text = _localized_safe_reply(INVALID_REQUEST_REPLY, presentation.language)
        elif isinstance(presentation, ToolFailurePresentation):
            text = _localized_safe_reply(
                TOOL_FAILURE_REPLIES.get(presentation.code, UNAVAILABLE_REPLY),
                presentation.language,
            )
        elif isinstance(presentation, UnavailablePresentation):
            text = _localized_safe_reply(UNAVAILABLE_REPLY, presentation.language)
        else:
            return BotResponse(text=UNAVAILABLE_REPLY)
        return _response(text, presentation.language, presentation.display_name)


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


def render_attendance_events(
    events: tuple[AttendanceEvent, ...],
    *,
    language: ReplyLanguage = "en",
    records_omitted: bool = False,
) -> str:
    """Render an aggregate reply within the Teams character budget.

    Events are retained in safely returned source order while the existing local
    presentation sorts them by display date. The disclosure deliberately makes
    no claim about the source's ordering.
    """
    selected: list[AttendanceEvent] = []
    omitted = records_omitted
    for index, event in enumerate(events):
        candidate = tuple((*selected, event))
        needs_disclosure = records_omitted or index < len(events) - 1
        if (
            len(_render_events(candidate, language, records_omitted=needs_disclosure))
            > MAX_REPLY_CHARACTERS
        ):
            omitted = True
            break
        selected.append(event)
    return _render_events(tuple(selected), language, records_omitted=omitted)


def _render_events(
    events: tuple[AttendanceEvent, ...],
    language: ReplyLanguage,
    *,
    records_omitted: bool = False,
) -> str:
    if not events:
        return _copy(
            language,
            "No attendance events were found for that date range.",
            "Za to obdobje ni evidentiranih dogodkov prisotnosti.",
        )
    groups: defaultdict[date | None, list[AttendanceEvent]] = defaultdict(list)
    for event in events:
        groups[_event_date(event)].append(event)
    dated_groups = sorted(day for day in groups if day is not None)
    response_parts = [_range_heading(dated_groups, language)] if dated_groups else []
    for day in dated_groups:
        response_parts.append(f"**{_format_date(day, language, weekday=True)}**")
        response_parts.append(
            "\n".join(
                _render_attendance_event(event, language) for event in _sort_events(groups[day])
            )
        )
    if None in groups:
        response_parts.append(f"**{_copy(language, 'Date unavailable', 'Datum ni na voljo')}**")
        response_parts.append(
            "\n".join(
                _render_attendance_event(event, language) for event in _sort_events(groups[None])
            )
        )
    response = "\n\n".join(part for part in response_parts if part)
    if records_omitted:
        response += "\n" + _copy(
            language,
            "Showing returned events only; additional records were omitted.",
            "Prikazani so samo vrnjeni dogodki; dodatni zapisi so izpuščeni.",
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


def _guidance_copy(intent: GuidanceIntent, language: ReplyLanguage) -> str:
    if intent == "date_ambiguous":
        return _copy(
            language,
            CLARIFICATION_REPLY,
            "Prosimo, pojasnite obdobje prisotnosti, ki ga želite prikazati.",
        )
    return _copy(
        language,
        "I can show your attendance events for a specific date range.",
        "Lahko vam prikažem dogodke vaše prisotnosti za določeno obdobje.",
    )


def _response(text: str, language: ReplyLanguage, display_name: str | None) -> BotResponse:
    name = _safe_display_name(display_name)
    if name:
        greeting = "Pozdravljeni" if language == "sl" else "Hello"
        text = f"{greeting}, {name}!\n\n{text}"
    return BotResponse(text=text)


def _safe_display_name(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())[:80]
    if not normalized:
        return None
    replacements: dict[str, str | int | None] = {
        character: f"\\{character}" for character in r"\\`*_{}[]<>#()+-.!|"
    }
    return normalized.translate(str.maketrans(replacements))


def _localized_safe_reply(text: str, language: ReplyLanguage) -> str:
    if language == "en":
        return text
    slovene = {
        UNAVAILABLE_REPLY: "Podatki o prisotnosti trenutno niso na voljo. Poskusite znova pozneje.",
        INVALID_REQUEST_REPLY: "Prosimo, navedite obdobje največ 31 dni.",
        TOOL_FAILURE_REPLIES[
            "INVALID_ARGUMENT"
        ]: "Preverite obdobje prisotnosti in poskusite znova.",
        TOOL_FAILURE_REPLIES["FORBIDDEN"]: "Nimate dovoljenja za ogled te prisotnosti.",
        TOOL_FAILURE_REPLIES["IDENTITY_UNMAPPED"]: (
            "Vaš račun Teams ni povezan z aktivnim zaposlenim za evidenco prisotnosti. "
            "Obrnite se na skrbnika."
        ),
        TOOL_FAILURE_REPLIES["IDENTITY_AMBIGUOUS"]: (
            "Vašega računa Teams ni mogoče varno povezati. Obrnite se na skrbnika."
        ),
        TOOL_FAILURE_REPLIES["AUTHENTICATION_REQUIRED"]: "Prijavite se in poskusite znova.",
        TOOL_FAILURE_REPLIES["TOKEN_INVALID"]: "Prijavite se in poskusite znova.",
    }
    return slovene.get(text, slovene[UNAVAILABLE_REPLY])


def _copy(language: ReplyLanguage, english: str, slovene: str) -> str:
    return slovene if language == "sl" else english
