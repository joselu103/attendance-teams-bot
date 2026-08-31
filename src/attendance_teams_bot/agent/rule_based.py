from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from attendance_teams_bot.agent.contracts import Clarification, ListMyAttendanceIntent

_DATE_RANGE = re.compile(r"\bfrom\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})\b", re.I)
_SELF_ATTENDANCE = re.compile(r"\bmy\s+attendance\b", re.I)
_CLARIFICATION = "Please provide a start and end date in YYYY-MM-DD format."
_SELF_SERVICE_CLARIFICATION = (
    "I can only show your own attendance. Use: Show my attendance from YYYY-MM-DD to YYYY-MM-DD."
)


@dataclass(frozen=True, slots=True)
class RuleBasedIntentSelector:
    def select_intent(self, message: str) -> ListMyAttendanceIntent | Clarification:
        if _SELF_ATTENDANCE.search(message) is None:
            return Clarification(_SELF_SERVICE_CLARIFICATION)
        match = _DATE_RANGE.search(message)
        if match is None:
            return Clarification(_CLARIFICATION)
        try:
            start_date = date.fromisoformat(match.group(1))
            end_date = date.fromisoformat(match.group(2))
        except ValueError:
            return Clarification(_CLARIFICATION)
        if start_date > end_date or (end_date - start_date).days >= 31:
            return Clarification("Please provide a date range of no more than 31 days.")
        return ListMyAttendanceIntent(start_date, end_date)
