from datetime import date

from attendance_teams_bot.agent.contracts import Clarification, ListMyAttendanceIntent
from attendance_teams_bot.agent.rule_based import RuleBasedIntentSelector


def test_selector_refuses_an_explicit_employee_target() -> None:
    selector = RuleBasedIntentSelector()

    intent = selector.select_intent("Show employee 42 attendance from 2026-08-10 to 2026-08-12")

    assert intent == Clarification(
        "I can only show your own attendance. "
        "Use: Show my attendance from YYYY-MM-DD to YYYY-MM-DD."
    )


def test_selector_builds_a_bounded_personal_attendance_intent() -> None:
    selector = RuleBasedIntentSelector()

    intent = selector.select_intent("Show my attendance from 2026-08-10 to 2026-08-12")

    assert intent == ListMyAttendanceIntent(date(2026, 8, 10), date(2026, 8, 12))


def test_selector_requests_clarification_for_an_ambiguous_date_range() -> None:
    selector = RuleBasedIntentSelector()

    intent = selector.select_intent("Show my attendance")

    assert intent == Clarification("Please provide a start and end date in YYYY-MM-DD format.")
