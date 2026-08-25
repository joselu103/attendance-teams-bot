from uuid import uuid4

import pytest
from pydantic import ValidationError

from attendance_teams_bot.settings import RuntimeMode, Settings


def test_settings_default_to_local_mode_without_a_dotenv_file(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BOT_RUNTIME_MODE")

    settings = Settings()

    assert settings.mode is RuntimeMode.LOCAL


def test_settings_reject_teams_mode_without_bot_service_configuration(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / ".env").write_text("BOT_RUNTIME_MODE=teams", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_RUNTIME_MODE", "teams")

    with pytest.raises(ValidationError, match="requires Bot Service configuration"):
        Settings()


def test_settings_loads_complete_teams_configuration_from_one_dotenv(tmp_path, monkeypatch) -> None:
    client_id = uuid4()
    tenant_id = uuid4()
    environment_file = tmp_path / ".env"
    environment_file.write_text(
        "\n".join(
            [
                "MCP_ENDPOINT=https://attendance-crmt.example.test/mcp",
                "BOT_RUNTIME_MODE=teams",
                f"CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID={client_id}",
                f"CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID={tenant_id}",
                "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET=test-only-value",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BOT_RUNTIME_MODE")

    settings = Settings()

    assert settings.mode is RuntimeMode.TEAMS
    assert settings.teams_connection is not None
    assert settings.teams_connection.client_id == client_id
    assert settings.teams_connection.tenant_id == tenant_id


def test_settings_reads_the_mcp_endpoint_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("MCP_ENDPOINT", "https://attendance-crmt.example.test/mcp")

    settings = Settings()

    assert str(settings.mcp_endpoint) == "https://attendance-crmt.example.test/mcp"
