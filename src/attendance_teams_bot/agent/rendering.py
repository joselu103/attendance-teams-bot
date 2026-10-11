from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
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
CLARIFICATION_REPLY = "Please restate the complete attendance period with both start and end dates."
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
    start_date: date | None = None
    end_date: date | None = None
    has_more: bool = False
    page_number: int = 1


@dataclass(frozen=True, slots=True)
class _HistoryDisplayEntry:
    """A display-only portion of one authoritative attendance event."""

    event: AttendanceEvent
    checked_in_at: datetime | None
    checked_out_at: datetime | None
    day: date | None
    ends_at_day_boundary: bool = False


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
    CurrentAttendancePresentation
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
        if isinstance(presentation, SafeHistoryPresentation):
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
            text = _localized_safe_reply(UNAVAILABLE_REPLY, presentation.language)
        return _guidance_response(text, presentation.language, presentation.display_name)


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
_SHORT_MONTHS: dict[ReplyLanguage, tuple[str, ...]] = {
    "en": (
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ),
    "sl": (
        "jan.",
        "feb.",
        "mar.",
        "apr.",
        "maj",
        "jun.",
        "jul.",
        "avg.",
        "sep.",
        "okt.",
        "nov.",
        "dec.",
    ),
}
_SHORT_WEEKDAYS: dict[ReplyLanguage, tuple[str, ...]] = {
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "sl": ("pon", "tor", "sre", "čet", "pet", "sob", "ned"),
}
_HISTORY_DIVIDER = "─────────────────────────────────────────"
_TEAMS_LINE_BREAK = "<br>"
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
_PUNCH_TYPE_ICONS = {
    "Office": "🏢",
    "Remote work": "🏠",
    "Customer site": "🤝",
    "Lunch break": "🍽️",
    "Leave": "🌴",
    "Sick leave": "🤒",
    "Childcare leave": "👶",
    "Emergency leave": "⚠️",
    "Unpaid leave": "💳",
    "Blood donation leave": "🩸",
    "Accompaniment leave": "🤝",
    "Paternity leave": "👨‍🍼",
    "Maternity leave": "🤰",
}


def _render_attendance_event(entry: _HistoryDisplayEntry, language: ReplyLanguage) -> str:
    if entry.checked_in_at is None and entry.checked_out_at is None:
        time_range = _copy(language, "Time unavailable", "Čas ni na voljo")
    elif entry.checked_in_at is None:
        start = _copy(language, "Start time unavailable", "Začetni čas ni na voljo")
        time_range = f"{start}–{_time(entry.checked_out_at)}"
    elif entry.checked_out_at is None:
        present = _copy(language, "In progress", "V teku")
        time_range = f"{_time(entry.checked_in_at)}–{present}"
    else:
        time_range = _time_range(entry)
    attendance_type = _attendance_type(entry.event.punch_type, language)
    icon = _attendance_icon(entry.event.punch_type)
    result = f"  * `{time_range}` — " + (f"{icon} " if icon else "") + attendance_type
    if entry.checked_in_at is None or entry.checked_out_at is None:
        result += _copy(
            language,
            " *(Not included in daily total)*",
            " *(Ni vključen v dnevni seštevek)*",
        )
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
    prefix = _history_header(presentation)
    entries = _history_display_entries(presentation)
    if not entries:
        return BotResponse(
            "\n".join(
                (
                    prefix,
                    _copy(
                        presentation.language,
                        "No attendance entries found.",
                        "Ni najdenih evidenc prisotnosti.",
                    ),
                    _history_footer(presentation.language),
                )
            )
        )
    groups: defaultdict[date | None, list[_HistoryDisplayEntry]] = defaultdict(list)
    for entry in entries:
        groups[entry.day].append(entry)
    blocks = [
        _history_day_block(day, groups[day], presentation.language)
        for day in sorted(day for day in groups if day is not None)
    ]
    if None in groups:
        blocks.append(_history_day_block(None, groups[None], presentation.language))
    response = _ordered_batch(
        prefix,
        blocks,
        presentation.language,
        suffix=_history_footer(presentation.language, has_more=presentation.has_more),
        first_block_separator="\n",
    )
    return response or BotResponse(_localized_safe_reply(UNAVAILABLE_REPLY, presentation.language))


def _current_batch(presentation: CurrentAttendancePresentation) -> BotResponse:
    if presentation.plan is None or not valid_presentation_plan(presentation.plan):
        return BotResponse(_localized_safe_reply(UNAVAILABLE_REPLY, presentation.language))
    if not presentation.status_names:
        return BotResponse(
            _copy(
                presentation.language,
                "No current attendance records were found.",
                "Trenutnih evidenc prisotnosti ni.",
            )
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
    response = _ordered_batch(prefix, blocks, presentation.language)
    return response or BotResponse(_localized_safe_reply(UNAVAILABLE_REPLY, presentation.language))


def _ordered_batch(
    prefix: str,
    blocks: list[str],
    language: ReplyLanguage,
    *,
    suffix: str | None = None,
    first_block_separator: str = "\n\n",
) -> BotResponse | None:
    """Pack complete fact blocks while keeping the batch header and footer singular."""
    suffix = "\n\n" + (suffix or _history_footer(language))
    reserve = len(suffix)
    capacity = MAX_REPLY_CHARACTERS - reserve
    if capacity < 1:
        return None
    bodies: list[str] = []
    current = prefix
    for block in blocks:
        separator = first_block_separator if current == prefix else "\n\n"
        candidate = current + separator + block
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
    messages = tuple(
        body + (suffix if index == len(bodies) else "")
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
    return _copy(language, *labels.get(value.casefold(), (value, value)))


def _event_date(event: AttendanceEvent) -> date | None:
    timestamp = event.checked_in_at or event.checked_out_at
    return timestamp.astimezone(_REPLY_TIMEZONE).date() if timestamp is not None else None


def _history_display_entries(
    presentation: SafeHistoryPresentation,
) -> tuple[_HistoryDisplayEntry, ...]:
    entries: list[_HistoryDisplayEntry] = []
    for event in presentation.events:
        if (
            event.checked_in_at is None
            or event.checked_out_at is None
            or not _is_after(event.checked_out_at, event.checked_in_at)
        ):
            entries.append(
                _HistoryDisplayEntry(
                    event,
                    event.checked_in_at,
                    event.checked_out_at,
                    _event_date(event),
                )
            )
            continue
        entries.extend(_split_history_event(event, presentation.start_date, presentation.end_date))
    return tuple(entries)


def _split_history_event(
    event: AttendanceEvent, start_date: date | None, end_date: date | None
) -> tuple[_HistoryDisplayEntry, ...]:
    assert event.checked_in_at is not None and event.checked_out_at is not None
    interval_start = event.checked_in_at.astimezone(_REPLY_TIMEZONE)
    interval_end = event.checked_out_at.astimezone(_REPLY_TIMEZONE)
    if start_date is not None and end_date is not None:
        period_start = datetime.combine(start_date, time.min, tzinfo=_REPLY_TIMEZONE)
        period_end = datetime.combine(
            end_date + timedelta(days=1), time.min, tzinfo=_REPLY_TIMEZONE
        )
        if _is_before(interval_start, period_start):
            interval_start = period_start
        if _is_before(period_end, interval_end):
            interval_end = period_end
    if not _is_before(interval_start, interval_end):
        return ()

    entries: list[_HistoryDisplayEntry] = []
    current = interval_start
    while _is_before(current, interval_end):
        next_day = datetime.combine(
            current.date() + timedelta(days=1), time.min, tzinfo=_REPLY_TIMEZONE
        )
        segment_end = interval_end if _is_before(interval_end, next_day) else next_day
        entries.append(
            _HistoryDisplayEntry(
                event,
                current,
                segment_end,
                current.date(),
                ends_at_day_boundary=segment_end == next_day,
            )
        )
        current = segment_end
    return tuple(entries)


def _is_before(left: datetime, right: datetime) -> bool:
    return left.astimezone(UTC) < right.astimezone(UTC)


def _is_after(left: datetime, right: datetime) -> bool:
    return _is_before(right, left)


def _history_header(presentation: SafeHistoryPresentation) -> str:
    title = _copy(
        presentation.language,
        f"📅 **Attendance Record** (Page {presentation.page_number})",
        f"📅 **Evidenca prisotnosti** (Stran {presentation.page_number})",
    )
    if presentation.start_date is not None and presentation.end_date is not None:
        period = _copy(
            presentation.language,
            f"Period: {presentation.start_date.isoformat()} to {presentation.end_date.isoformat()}",
            f"Obdobje: {presentation.start_date.isoformat()} "
            f"do {presentation.end_date.isoformat()}",
        )
        timezone = _copy(
            presentation.language,
            "Timezone: Europe/Ljubljana",
            "Časovni pas: Europe/Ljubljana",
        )
        return _TEAMS_LINE_BREAK.join((title, period, timezone, _HISTORY_DIVIDER))
    return _TEAMS_LINE_BREAK.join((title, _HISTORY_DIVIDER))


def _history_footer(language: ReplyLanguage, *, has_more: bool = False) -> str:
    return _TEAMS_LINE_BREAK.join(
        (
            _HISTORY_DIVIDER,
            _copy(
                language,
                'Type "next" for the next page.' if has_more else "End of results.",
                'Za naslednjo stran vnesite "naslednja".' if has_more else "Konec rezultatov.",
            ),
        )
    )


def _history_day_block(
    day: date | None, entries: list[_HistoryDisplayEntry], language: ReplyLanguage
) -> str:
    heading = (
        _short_format_date(day, language)
        if day is not None
        else _copy(language, "Date unavailable", "Datum ni na voljo")
    )
    ordered_entries = _sort_entries(entries)
    total = sum(
        (
            entry.checked_out_at.astimezone(UTC) - entry.checked_in_at.astimezone(UTC)
            for entry in ordered_entries
            if entry.checked_in_at is not None and entry.checked_out_at is not None
        ),
        timedelta(),
    )
    return "\n".join(
        (
            f"* 🗓️ **{heading}**",
            *(_render_attendance_event(entry, language) for entry in ordered_entries),
            "  *("
            + _copy(
                language,
                f"Daily Total: {_format_duration(total)}",
                f"Dnevni seštevek: {_format_duration(total)}",
            )
            + ")*",
        )
    )


def _sort_entries(entries: list[_HistoryDisplayEntry]) -> list[_HistoryDisplayEntry]:
    return sorted(
        entries,
        key=lambda entry: (
            entry.checked_in_at or entry.checked_out_at or datetime.max.replace(tzinfo=UTC),
            entry.event.attendance_event_id,
        ),
    )


def _format_date(value: date, language: ReplyLanguage, *, weekday: bool = False) -> str:
    day = _copy(
        language,
        f"{_MONTHS[language][value.month - 1]} {value.day}, {value.year}",
        f"{value.day} {_MONTHS[language][value.month - 1]} {value.year}",
    )
    return f"{_WEEKDAYS[language][value.weekday()]}, {day}" if weekday else day


def _short_format_date(value: date, language: ReplyLanguage) -> str:
    return _copy(
        language,
        (
            f"{_SHORT_WEEKDAYS[language][value.weekday()]}, "
            f"{_SHORT_MONTHS[language][value.month - 1]} {value.day}, {value.year}"
        ),
        (
            f"{_SHORT_WEEKDAYS[language][value.weekday()]}, {value.day}. "
            f"{_SHORT_MONTHS[language][value.month - 1]} {value.year}"
        ),
    )


def _format_duration(value: timedelta) -> str:
    seconds = max(0, int(value.total_seconds()))
    hours, remainder = divmod(seconds, 3_600)
    minutes = remainder // 60
    return f"{hours}h {minutes:02d}m"


def _time(value: datetime | None) -> str:
    assert value is not None
    return value.astimezone(_REPLY_TIMEZONE).strftime("%H:%M")


def _time_range(entry: _HistoryDisplayEntry) -> str:
    assert entry.checked_in_at is not None and entry.checked_out_at is not None
    start = _time(entry.checked_in_at)
    end = "24:00" if entry.ends_at_day_boundary else _time(entry.checked_out_at)
    start_local = entry.checked_in_at.astimezone(_REPLY_TIMEZONE)
    end_local = entry.checked_out_at.astimezone(_REPLY_TIMEZONE)
    if (
        not entry.ends_at_day_boundary
        and start == end
        and start_local.utcoffset() != end_local.utcoffset()
    ):
        start = f"{start} UTC{start_local.strftime('%z')[:3]}:{start_local.strftime('%z')[3:]}"
        end = f"{end} UTC{end_local.strftime('%z')[:3]}:{end_local.strftime('%z')[3:]}"
    return f"{start}–{end}"


def _attendance_type(value: str | None, language: ReplyLanguage) -> str:
    if value is None or not value.strip():
        return _copy(language, "Unspecified attendance", "Nedoločena prisotnost")
    normalized = " ".join(value.split()).casefold()
    for slovene, english in _PUNCH_TYPE_EQUIVALENTS:
        if normalized in {slovene.casefold(), english.casefold()}:
            return _copy(language, english, slovene)
    for alias, english in _ENGLISH_PUNCH_TYPE_ALIASES:
        if normalized == alias:
            for slovene, canonical_english in _PUNCH_TYPE_EQUIVALENTS:
                if canonical_english == english:
                    return _copy(language, canonical_english, slovene)
    return _safe_text(value) or _copy(language, "Unspecified attendance", "Nedoločena prisotnost")


def _attendance_icon(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = " ".join(value.split()).casefold()
    for slovene, english in _PUNCH_TYPE_EQUIVALENTS:
        if normalized in {slovene.casefold(), english.casefold()}:
            return _PUNCH_TYPE_ICONS[english]
    for alias, english in _ENGLISH_PUNCH_TYPE_ALIASES:
        if normalized == alias:
            return _PUNCH_TYPE_ICONS[english]
    return None


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
            "Prosimo, znova navedite celotno obdobje prisotnosti z začetnim in končnim datumom.",
        )
    return _copy(
        language,
        "I can show your attendance events for a specific date range.",
        "Lahko vam prikažem dogodke vaše prisotnosti za določeno obdobje.",
    )


def _guidance_response(text: str, language: ReplyLanguage, display_name: str | None) -> BotResponse:
    name = _safe_display_name(display_name)
    if name:
        greeting = _copy(language, "Hello", "Pozdravljeni")
        text = f"{greeting}, {name}{_TEAMS_LINE_BREAK}{text}"
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
    slovene = {
        UNAVAILABLE_REPLY: "Podatki o prisotnosti trenutno niso na voljo. Poskusite znova pozneje.",
        INVALID_REQUEST_REPLY: (
            "Prosimo, navedite veljavno obdobje prisotnosti z začetnim in končnim datumom."
        ),
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
    return _copy(language, text, slovene.get(text, slovene[UNAVAILABLE_REPLY]))


def _copy(language: ReplyLanguage, english: str, slovene: str) -> str:
    return slovene if language == "sl" else english
