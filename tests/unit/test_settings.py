from secrets import token_urlsafe
from uuid import uuid4

from attendance_teams_bot.settings import (
    RuntimeMode,
    RuntimeSettings,
    Settings,
    TeamsConnectionSettings,
)


def test_teams_connection_settings_read_the_sdk_environment_aliases(monkeypatch) -> None:
    client_id = uuid4()
    tenant_id = uuid4()
    secret = token_urlsafe()
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID", str(client_id))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID", str(tenant_id))
    monkeypatch.setenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET", secret)

    settings = TeamsConnectionSettings()

    assert settings.client_id == client_id
    assert settings.tenant_id == tenant_id
    assert settings.client_secret.get_secret_value() == secret


def test_runtime_settings_default_to_local_mode(monkeypatch) -> None:
    monkeypatch.delenv("BOT_RUNTIME_MODE", raising=False)

    settings = RuntimeSettings()

    assert settings.mode is RuntimeMode.LOCAL


def test_settings_reads_the_mcp_endpoint_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("MCP_ENDPOINT", "https://attendance-crmt.example.test/mcp")

    settings = Settings()

    assert str(settings.mcp_endpoint) == "https://attendance-crmt.example.test/mcp"
