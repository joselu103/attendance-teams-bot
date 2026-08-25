from attendance_teams_bot.local import LocalUnconfiguredHandler


def test_local_handler_reports_that_external_adapters_are_unconfigured() -> None:
    handler = LocalUnconfiguredHandler()

    response = handler.handle(
        user_id="teams-user-123",
        message="Show my attendance",
    )

    assert response.text == (
        "Attendance Teams Bot is running locally, but authentication, MCP, "
        "and LLM adapters are not configured yet."
    )
    assert response.request is None
