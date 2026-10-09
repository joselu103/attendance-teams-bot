from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from attendance_teams_bot.agent.contracts import BotResponse, ReplyLanguage
from attendance_teams_bot.agent.language_model import PresentationPlan
from attendance_teams_bot.mcp.contracts import AttendanceEvent, EmployeeSuggestion, McpToolErrorCode

MAX_REPLY_CHARACTERS = 12_000
_REPLY_TIMEZONE = ZoneInfo("Europe/Ljubljana")


def is_safe_model_markdown(markdown: object) -> bool:
    """Accept a small Teams Markdown subset and reject links, HTML, and control data."""
    if (
        not isinstance(markdown, str)
        or not markdown.strip()
        or len(markdown) > MAX_REPLY_CHARACTERS
    ):
        return False
    if any(ord(character) < 32 and character not in "\n\r\t" for character in markdown):
        return False
    forbidden = ("<", ">", "`", "![", "](", "http://", "https://")
    return not any(value in markdown.casefold() for value in forbidden)


UNAVAILABLE_REPLY = "Attendance data is temporarily unavailable. Please try again later."
INVALID_REQUEST_REPLY = "Please provide a valid attendance request."
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
    "NOT_FOUND": "No matching employee was found.",
}

GuidanceIntent = Literal["unsupported", "date_ambiguous", "greeting"]


@dataclass(frozen=True, slots=True)
class EventResultPresentation:
    events: tuple[AttendanceEvent, ...]
    records_omitted: bool
    language: ReplyLanguage
    display_name: str | None


@dataclass(frozen=True, slots=True)
class CurrentAttendancePresentation:
    status_names: tuple[tuple[str, tuple[str, ...]], ...]
    language: ReplyLanguage
    display_name: str | None
    plan: PresentationPlan | None


@dataclass(frozen=True, slots=True)
class SafeHistoryPresentation:
    events: tuple[AttendanceEvent, ...]
    language: ReplyLanguage
    display_name: str | None
    plan: PresentationPlan | None


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
class EmployeeCandidatesPresentation:
    candidates: tuple[EmployeeSuggestion, ...]
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
    | CurrentAttendancePresentation
    | SafeHistoryPresentation
    | ClarificationPresentation
    | GuidancePresentation
    | InvalidRequestPresentation
    | ToolFailurePresentation
    | EmployeeCandidatesPresentation
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
        elif isinstance(presentation, SafeHistoryPresentation):
            return _history_batch(presentation)
        elif isinstance(presentation, CurrentAttendancePresentation):
            return _current_batch(presentation)
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
        elif isinstance(presentation, EmployeeCandidatesPresentation):
            text = _employee_candidates(presentation.candidates, presentation.language)
        elif isinstance(presentation, UnavailablePresentation):
            text = _localized_safe_reply(UNAVAILABLE_REPLY, presentation.language)
        else:
            return BotResponse(text=_localized_safe_reply(UNAVAILABLE_REPLY, presentation.language))
        return _response(text, presentation.language, presentation.display_name)


def _employee_candidates(
    candidates: tuple[EmployeeSuggestion, ...], language: ReplyLanguage
) -> str:
    if not candidates:
        return _copy(
            language,
            "No matching employee was found.",
            "Ujemajočega zaposlenega ni bilo mogoče najti.",
        )
    lines = [
        _copy(
            language,
            "Select one employee in a new message:",
            "V novem sporočilu izberite enega zaposlenega:",
        )
    ]
    for candidate in candidates:
        selector = candidate.username or candidate.email
        if selector:
            lines.append(f"- {candidate.display_name} — {selector}")
        else:
            lines.append(f"- {candidate.display_name}")
    lines.append(
        _copy(
            language,
            "Reply with the listed username or email.",
            "Odgovorite z navedenim uporabniškim imenom ali e-pošto.",
        )
    )
    return "\n".join(lines)


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
# One row per database label. The renderer uses the same row in either direction.
_PUNCH_TYPE_EQUIVALENTS: tuple[tuple[str, str], ...] = (
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

# Compatibility-only input aliases; output always uses a database label above.
_ENGLISH_PUNCH_TYPE_ALIASES: tuple[tuple[str, str], ...] = (
    ("work", "Office"),
    ("work from home", "Remote work"),
    ("break", "Lunch break"),
)


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
    result = f"- {time_range}: {attendance_type}"
    if event.checked_in_at is not None and event.checked_out_at is None:
        result += " *(Active)*" if language == "en" else " *(Aktivno)*"
    return result


def valid_presentation_plan(plan: PresentationPlan) -> bool:
    return _safe_plan_text(plan.title, 120) and (
        plan.context is None or _safe_plan_text(plan.context, 240)
    )


def _safe_plan_text(value: str, limit: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= limit
        and is_safe_model_markdown(value)
    )


def _history_batch(presentation: SafeHistoryPresentation) -> BotResponse:
    if presentation.plan is None or not valid_presentation_plan(presentation.plan):
        return BotResponse(_localized_safe_reply(UNAVAILABLE_REPLY, presentation.language))
    if not presentation.events:
        return _response(
            _copy(
                presentation.language,
                "No attendance events were found for that date range.",
                "Za to obdobje ni evidentiranih dogodkov prisotnosti.",
            ),
            presentation.language,
            presentation.display_name,
        )
    groups: defaultdict[date | None, list[AttendanceEvent]] = defaultdict(list)
    for event in presentation.events:
        groups[_event_date(event)].append(event)
    blocks = [
        f"**{_format_date(day, presentation.language, weekday=True)}**\n\n"
        + "\n".join(
            _render_attendance_event(event, presentation.language)
            for event in _sort_events(groups[day])
        )
        for day in sorted(day for day in groups if day is not None)
    ]
    if None in groups:
        blocks.append(
            f"**{_copy(presentation.language, 'Date unavailable', 'Datum ni na voljo')}**\n\n"
            + "\n".join(
                _render_attendance_event(event, presentation.language)
                for event in _sort_events(groups[None])
            )
        )
    prefix = f"**{_safe_text(presentation.plan.title)}**"
    if presentation.plan.context:
        prefix += "\n\n" + _safe_text(presentation.plan.context)
    response = _ordered_batch(prefix, blocks, presentation.language, presentation.display_name)
    return response or BotResponse(_localized_safe_reply(UNAVAILABLE_REPLY, presentation.language))


def _current_batch(presentation: CurrentAttendancePresentation) -> BotResponse:
    if presentation.plan is None or not valid_presentation_plan(presentation.plan):
        return BotResponse(_localized_safe_reply(UNAVAILABLE_REPLY, presentation.language))
    if not presentation.status_names:
        return _response(
            _copy(
                presentation.language,
                "No current attendance records were found.",
                "Trenutnih evidenc prisotnosti ni.",
            ),
            presentation.language,
            presentation.display_name,
        )
    prefix = f"**{_safe_text(presentation.plan.title)}**"
    if presentation.plan.context:
        prefix += "\n\n" + _safe_text(presentation.plan.context)
    blocks: list[str] = []
    for status, names in presentation.status_names:
        heading = f"**{_current_status(status, presentation.language)}**"
        group = heading
        for name in names:
            line = f"- {_safe_text(name)}"
            candidate = group + "\n" + line
            if len(candidate) > MAX_REPLY_CHARACTERS - 80 and group != heading:
                blocks.append(group)
                group = heading + "\n" + line
            elif len(candidate) > MAX_REPLY_CHARACTERS - 80:
                return BotResponse(_localized_safe_reply(UNAVAILABLE_REPLY, presentation.language))
            else:
                group = candidate
        if group != heading:
            blocks.append(group)
    response = _ordered_batch(prefix, blocks, presentation.language, presentation.display_name)
    return response or BotResponse(_localized_safe_reply(UNAVAILABLE_REPLY, presentation.language))


def _ordered_batch(
    prefix: str,
    blocks: list[str],
    language: ReplyLanguage,
    display_name: str | None,
) -> BotResponse | None:
    """Pack complete fact blocks and mark every part plus the batch end."""
    suffix = "\n\n**Konec rezultatov**" if language == "sl" else "\n\n**End of results**"
    reserve = len("**Part 9999 of 9999**\n\n") + len(suffix)
    capacity = MAX_REPLY_CHARACTERS - reserve
    if capacity < 1:
        return None
    name = _safe_display_name(display_name)
    if name:
        greeting = "Pozdravljeni" if language == "sl" else "Hello"
        prefix = f"{greeting}, {name}!\n\n{prefix}"
    bodies: list[str] = []
    current = prefix
    for block in blocks:
        candidate = current + "\n\n" + block
        if len(candidate) <= capacity:
            current = candidate
        elif current != prefix:
            bodies.append(current)
            current = block
            if len(current) > capacity:
                return None
        else:
            return None
    if current != prefix:
        bodies.append(current)
    if not bodies:
        return None
    total = len(bodies)
    part = "Del " if language == "sl" else "Part "
    of = " od " if language == "sl" else " of "
    messages = tuple(
        f"**{part}{index}{of}{total}**\n\n{body}" + (suffix if index == total else "")
        for index, body in enumerate(bodies, start=1)
    )
    if any(len(message) > MAX_REPLY_CHARACTERS for message in messages):
        return None
    return BotResponse.batch(messages)


def _current_status(value: str, language: ReplyLanguage) -> str:
    labels = {
        "office": ("Office", "Delo na firmi"),
        "remote": ("Remote work", "Delo od doma"),
        "customer_site": ("Customer site", "Delo pri stranki"),
        "break": ("Lunch break", "Na malici"),
        "absence": ("Absent", "Odsotni"),
        "no_status": ("No status", "Ni statusa"),
    }
    return labels.get(value.casefold(), (value, value))[1 if language == "sl" else 0]


def _event_date(event: AttendanceEvent) -> date | None:
    timestamp = event.checked_in_at or event.checked_out_at
    return timestamp.astimezone(_REPLY_TIMEZONE).date() if timestamp is not None else None


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
    return value.astimezone(_REPLY_TIMEZONE).strftime("%H:%M")


def _attendance_type(value: str | None, language: ReplyLanguage) -> str:
    if value is None or not value.strip():
        return _copy(language, "Unspecified attendance", "Nedoločena prisotnost")
    normalized = " ".join(value.split()).casefold()
    for slovene, english in _PUNCH_TYPE_EQUIVALENTS:
        if normalized in {slovene.casefold(), english.casefold()}:
            return english if language == "en" else slovene
    for alias, english in _ENGLISH_PUNCH_TYPE_ALIASES:
        if normalized == alias:
            for slovene, canonical_english in _PUNCH_TYPE_EQUIVALENTS:
                if canonical_english == english:
                    return canonical_english if language == "en" else slovene
    return _safe_text(value) or _copy(language, "Unspecified attendance", "Nedoločena prisotnost")


def _safe_text(value: str | None) -> str:
    if value is None:
        return ""
    normalized = " ".join(value.split())[:160]
    replacements: dict[str, str | int | None] = {
        character: f"\\{character}" for character in r"\\`*_{}[]<>#()+-.!|"
    }
    return normalized.translate(str.maketrans(replacements))


def _guidance_copy(intent: GuidanceIntent, language: ReplyLanguage) -> str:
    if intent == "greeting":
        return _copy(language, "I can help with attendance.", "Pomagam vam lahko pri prisotnosti.")
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


def _response_batch(
    messages: tuple[str, ...], language: ReplyLanguage, display_name: str | None
) -> BotResponse:
    name = _safe_display_name(display_name)
    if name:
        greeting = "Pozdravljeni" if language == "sl" else "Hello"
        messages = (f"{greeting}, {name}!\n\n{messages[0]}", *messages[1:])
    return BotResponse.batch(messages)


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
