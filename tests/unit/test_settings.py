from attendance_teams_bot.settings import Settings


def test_settings_reads_the_mcp_endpoint_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("MCP_ENDPOINT", "https://attendance-crmt.example.test/mcp")

    settings = Settings()

    assert str(settings.mcp_endpoint) == "https://attendance-crmt.example.test/mcp"
