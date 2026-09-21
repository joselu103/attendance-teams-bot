from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeMode(StrEnum):
    LOCAL = "local"
    TEAMS = "teams"


class TeamsConnectionSettings(BaseModel):
    """Contain the Bot Service credentials required for Teams runtime wiring."""

    model_config = ConfigDict(frozen=True)

    client_id: UUID
    tenant_id: UUID
    client_secret: SecretStr


class OpenAiSettings(BaseModel):
    """Contain the OpenAI credentials and model name after configuration validation."""

    model_config = ConfigDict(frozen=True)

    api_key: SecretStr
    model: str = Field(min_length=1)


class AttendanceIntegrationSettings(BaseModel):
    """Contain all validated settings required to enable the real attendance flow."""

    model_config = ConfigDict(frozen=True)

    endpoint: HttpUrl
    delegated_scope: str
    teams_sso_oauth_connection_name: str
    timeout_seconds: float
    openai: OpenAiSettings


class Settings(BaseSettings):
    """Load runtime settings and keep attendance integration disabled unless fully configured."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    log_environment: Literal["local", "staging", "production"] = Field(
        default="local",
        validation_alias="LOG_ENV",
    )
    app_version: str = Field(default="dev", validation_alias="APP_VERSION")
    mode: RuntimeMode = Field(
        default=RuntimeMode.LOCAL,
        validation_alias="BOT_RUNTIME_MODE",
    )
    attendance_integration_enabled: bool = Field(
        default=False,
        validation_alias="ATTENDANCE_INTEGRATION_ENABLED",
    )
    mcp_endpoint: HttpUrl | None = Field(default=None, validation_alias="MCP_ENDPOINT")
    mcp_scope: str | None = Field(default=None, validation_alias="MCP_SCOPE")
    teams_sso_oauth_connection_name: str | None = Field(
        default=None,
        validation_alias="TEAMS_SSO_OAUTH_CONNECTION_NAME",
    )
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_model: str | None = Field(default=None, validation_alias="OPENAI_MODEL")
    mcp_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=30,
        validation_alias="MCP_TIMEOUT_SECONDS",
    )
    teams_client_id: UUID | None = Field(
        default=None,
        validation_alias="CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID",
    )
    teams_tenant_id: UUID | None = Field(
        default=None,
        validation_alias="CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID",
    )
    teams_client_secret: SecretStr | None = Field(
        default=None,
        validation_alias="CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET",
    )

    @field_validator("app_version")
    @classmethod
    def validate_app_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("APP_VERSION must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_teams_mode(self) -> Self:
        if self.mode is RuntimeMode.TEAMS and self.teams_connection is None:
            raise ValueError("teams mode requires Bot Service configuration")
        if self.attendance_integration_enabled and self.mode is not RuntimeMode.TEAMS:
            raise ValueError("enabled attendance integration requires teams runtime mode")
        if self.attendance_integration_enabled and self.attendance_integration is None:
            raise ValueError(
                "enabled attendance integration requires Teams, MCP, and OpenAI configuration"
            )
        return self

    @property
    def teams_connection(self) -> TeamsConnectionSettings | None:
        """Return Teams credentials only when all three Bot Service fields are configured."""
        if (
            self.teams_client_id is None
            or self.teams_tenant_id is None
            or self.teams_client_secret is None
        ):
            return None

        return TeamsConnectionSettings(
            client_id=self.teams_client_id,
            tenant_id=self.teams_tenant_id,
            client_secret=self.teams_client_secret,
        )

    @property
    def attendance_integration(self) -> AttendanceIntegrationSettings | None:
        """Return a validated integration only when the explicit enablement gate is satisfied."""
        if not self.attendance_integration_enabled:
            return None
        if (
            self.mcp_endpoint is None
            or self.mcp_scope is None
            or self.teams_sso_oauth_connection_name is None
            or self.openai_api_key is None
            or self.openai_model is None
        ):
            return None

        delegated_scope = self.mcp_scope.strip()
        oauth_connection_name = self.teams_sso_oauth_connection_name.strip()
        openai_model = self.openai_model.strip()
        endpoint = self.mcp_endpoint
        host = endpoint.host or ""
        is_loopback = host in {"localhost", "127.0.0.1", "::1"}

        if not delegated_scope or not oauth_connection_name or not openai_model:
            return None
        if endpoint.path != "/mcp" or endpoint.query is not None or endpoint.fragment is not None:
            return None
        if endpoint.scheme != "https" and not is_loopback:
            return None

        return AttendanceIntegrationSettings(
            endpoint=endpoint,
            delegated_scope=delegated_scope,
            teams_sso_oauth_connection_name=oauth_connection_name,
            timeout_seconds=self.mcp_timeout_seconds,
            openai=OpenAiSettings(api_key=self.openai_api_key, model=openai_model),
        )
