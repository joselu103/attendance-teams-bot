from attendance_teams_bot.agent.date_resolver import has_ambiguous_numeric_date


def test_numeric_day_month_is_not_guessed() -> None:
    assert has_ambiguous_numeric_date("show 6/8")
    assert not has_ambiguous_numeric_date("show 2026-08-06")
