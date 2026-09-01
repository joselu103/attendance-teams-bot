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


def test_disabled_flag_suppresses_complete_attendance_configuration(monkeypatch) -> None:
    monkeypatch.setenv("BOT_RUNTIME_MODE", "teams")
    monkeypatch.setenv("ATTENDANCE_INTEGRATION_ENABLED", "false")
    monkeypatch.setenv("MCP_ENDPOINT", "https://attendance-crmt.example.test/mcp")
    monkeypatch.setenv("MCP_SCOPE", "api://11111111-1111-1111-1111-111111111111/attendance.access")
    monkeypatch.setenv("TEAMS_SSO_OAUTH_CONNECTION_NAME", "AttendanceTeamsSso")
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET", "test-only-value")

    assert Settings().attendance_integration is None


def test_enabled_attendance_integration_requires_teams_runtime(monkeypatch) -> None:
    monkeypatch.setenv("BOT_RUNTIME_MODE", "local")
    monkeypatch.setenv("ATTENDANCE_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("MCP_ENDPOINT", "https://attendance-crmt.example.test/mcp")
    monkeypatch.setenv("MCP_SCOPE", "api://11111111-1111-1111-1111-111111111111/attendance.access")
    monkeypatch.setenv("TEAMS_SSO_OAUTH_CONNECTION_NAME", "AttendanceTeamsSso")

    with pytest.raises(ValidationError, match="requires teams runtime mode"):
        Settings()


def test_enabled_attendance_integration_requires_openai_configuration(monkeypatch) -> None:
    monkeypatch.setenv("ATTENDANCE_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("BOT_RUNTIME_MODE", "teams")
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET", "test-only-value")
    monkeypatch.setenv("MCP_ENDPOINT", "https://attendance-crmt.example.test/mcp")
    monkeypatch.setenv("MCP_SCOPE", "api://11111111-1111-1111-1111-111111111111/attendance.access")
    monkeypatch.setenv("TEAMS_SSO_OAUTH_CONNECTION_NAME", "AttendanceTeamsSso")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    with pytest.raises(ValidationError, match="OpenAI"):
        Settings()


def test_settings_build_complete_attendance_integration(monkeypatch) -> None:
    monkeypatch.setenv("ATTENDANCE_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("BOT_RUNTIME_MODE", "teams")
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID", str(uuid4()))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET", "test-only-value")
    monkeypatch.setenv("MCP_ENDPOINT", "https://attendance-crmt.example.test/mcp")
    monkeypatch.setenv("MCP_SCOPE", "api://11111111-1111-1111-1111-111111111111/attendance.access")
    monkeypatch.setenv("TEAMS_SSO_OAUTH_CONNECTION_NAME", "AttendanceTeamsSso")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-openai-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5-mini")

    settings = Settings()

    assert settings.attendance_integration is not None
    assert (
        str(settings.attendance_integration.endpoint) == "https://attendance-crmt.example.test/mcp"
    )
    assert (
        settings.attendance_integration.delegated_scope
        == "api://11111111-1111-1111-1111-111111111111/attendance.access"
    )
    assert settings.attendance_integration.openai.model == "gpt-5-mini"
