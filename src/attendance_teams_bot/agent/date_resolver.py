"""Deterministic, localized attendance-date resolution.

Only unambiguous calendar vocabulary is handled here. Other requests retain the
provider-neutral model fallback, where the same typed range boundary applies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from attendance_teams_bot.agent.contracts import OverallAttendanceRange, ReplyLanguage

_ISO_DATE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_COUNTED_MONTHS = re.compile(r"\b(?:last|past)\s+([1-9]|1[0-2])\s+months?\b", re.IGNORECASE)
_COUNTED_SLOVENE_MONTHS = re.compile(
    r"\b(?:zadnj(?:i|ih|e|a)|pretek(?:li|lih|le|la))\s+([1-9]|1[0-2])\s+"
    r"mese(?:c(?:ev|e|ih)?|ci|cev)\b",
    re.IGNORECASE,
)

_ENGLISH_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        1,
    )
}
_SLOVENE_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("januar", "januarja"),
            ("februar", "februarja"),
            ("marec", "marca"),
            ("april", "aprila"),
            ("maj", "maja"),
            ("junij", "junija"),
            ("julij", "julija"),
            ("avgust", "avgusta"),
            ("september", "septembra"),
            ("oktober", "oktobra"),
            ("november", "novembra"),
            ("december", "decembra"),
        ),
        1,
    )
    for name in names
}


@dataclass(frozen=True, slots=True)
class DateResolution:
    range: OverallAttendanceRange
    language: ReplyLanguage


def resolve_attendance_range(message: str, *, reference_date: date) -> DateResolution | None:
    """Resolve supported English or Slovene calendar forms in Ljubljana time."""
    if not isinstance(message, str):
        return None
    normalized = " ".join(message.casefold().split())
    language = _language_from_vocabulary(normalized)

    iso_dates = _iso_dates(normalized)
    if iso_dates:
        if len(iso_dates) > 2:
            return None
        return _resolution(iso_dates[0], iso_dates[-1], language or "en")

    counted = _COUNTED_MONTHS.search(normalized)
    if counted:
        return _rolling_months(reference_date, int(counted.group(1)), "en")
    counted = _COUNTED_SLOVENE_MONTHS.search(normalized)
    if counted:
        return _rolling_months(reference_date, int(counted.group(1)), "sl")

    if _has_any(normalized, "this month", "current month"):
        return _resolution(_month_start(reference_date), reference_date, "en")
    if _has_any(normalized, "ta mesec", "trenutni mesec"):
        return _resolution(_month_start(reference_date), reference_date, "sl")
    if _has_any(normalized, "last month", "previous month"):
        end = _month_start(reference_date) - timedelta(days=1)
        return _resolution(_month_start(end), end, "en")
    if _has_any(normalized, "prejšnji mesec", "zadnji mesec"):
        end = _month_start(reference_date) - timedelta(days=1)
        return _resolution(_month_start(end), end, "sl")
    if _has_any(normalized, "this week", "current week"):
        return _resolution(
            reference_date - timedelta(days=reference_date.weekday()), reference_date, "en"
        )
    if _has_any(normalized, "ta teden", "trenutni teden"):
        return _resolution(
            reference_date - timedelta(days=reference_date.weekday()), reference_date, "sl"
        )
    if _has_any(normalized, "last week", "previous week"):
        end = reference_date - timedelta(days=reference_date.weekday() + 1)
        return _resolution(end - timedelta(days=6), end, "en")
    if _has_any(normalized, "prejšnji teden", "zadnji teden"):
        end = reference_date - timedelta(days=reference_date.weekday() + 1)
        return _resolution(end - timedelta(days=6), end, "sl")

    month = _named_month(normalized)
    if month is None:
        return None
    month_number, month_language, explicit_year = month
    year = explicit_year if explicit_year is not None else reference_date.year
    if explicit_year is None and month_number > reference_date.month:
        year -= 1
    start = date(year, month_number, 1)
    if start.year == reference_date.year and start.month == reference_date.month:
        return _resolution(start, reference_date, month_language)
    return _resolution(start, _month_end(start), month_language)


def _iso_dates(message: str) -> list[date]:
    values: list[date] = []
    for match in _ISO_DATE.finditer(message):
        try:
            values.append(date.fromisoformat(match.group(1)))
        except ValueError:
            return []
    return values


def _named_month(message: str) -> tuple[int, ReplyLanguage, int | None] | None:
    names = {**_ENGLISH_MONTHS, **_SLOVENE_MONTHS}
    matches = [name for name in names if re.search(rf"\b{re.escape(name)}\b", message)]
    if len(matches) != 1:
        return None
    name = matches[0]
    language: ReplyLanguage = "en" if name in _ENGLISH_MONTHS else "sl"
    after = message[message.find(name) + len(name) :]
    year_match = re.match(r"\s*(?:,\s*)?(\d{4})\b", after)
    return names[name], language, int(year_match.group(1)) if year_match else None


def _rolling_months(reference_date: date, count: int, language: ReplyLanguage) -> DateResolution:
    return DateResolution(
        OverallAttendanceRange(_add_calendar_months(reference_date, -count), reference_date),
        language,
    )


def _add_calendar_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero_based = divmod(month_index, 12)
    month = month_zero_based + 1
    for day in range(value.day, 0, -1):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError("a calendar month always has at least one day")


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _month_end(value: date) -> date:
    return _month_start(_add_calendar_months(value, 1)) - timedelta(days=1)


def _resolution(start: date, end: date, language: ReplyLanguage) -> DateResolution | None:
    try:
        return DateResolution(OverallAttendanceRange(start, end), language)
    except ValueError:
        return None


def _has_any(message: str, *phrases: str) -> bool:
    return any(re.search(rf"\b{re.escape(phrase)}\b", message) for phrase in phrases)


def _language_from_vocabulary(message: str) -> ReplyLanguage | None:
    if any(re.search(rf"\b{re.escape(name)}\b", message) for name in _SLOVENE_MONTHS):
        return "sl"
    if any(re.search(rf"\b{re.escape(name)}\b", message) for name in _ENGLISH_MONTHS):
        return "en"
    return None
