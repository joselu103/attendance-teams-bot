from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeMode(StrEnum):
    LOCAL = "local"
    TEAMS = "teams"


class TeamsConnectionSettings(BaseModel):
    client_id: UUID
    tenant_id: UUID
    client_secret: SecretStr


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    mode: RuntimeMode = Field(
        default=RuntimeMode.LOCAL,
        validation_alias="BOT_RUNTIME_MODE",
    )
    mcp_endpoint: HttpUrl | None = Field(default=None, validation_alias="MCP_ENDPOINT")
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

    @model_validator(mode="after")
    def validate_teams_mode(self) -> Self:
        if self.mode is RuntimeMode.TEAMS and self.teams_connection is None:
            raise ValueError("teams mode requires Bot Service configuration")
        return self

    @property
    def teams_connection(self) -> TeamsConnectionSettings | None:
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
