from attendance_teams_bot.settings import RuntimeMode, RuntimeSettings, Settings


def test_runtime_settings_default_to_local_mode(monkeypatch) -> None:
    monkeypatch.delenv("BOT_RUNTIME_MODE", raising=False)

    settings = RuntimeSettings()

    assert settings.mode is RuntimeMode.LOCAL


def test_settings_reads_the_mcp_endpoint_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("MCP_ENDPOINT", "https://attendance-crmt.example.test/mcp")

    settings = Settings()

    assert str(settings.mcp_endpoint) == "https://attendance-crmt.example.test/mcp"
