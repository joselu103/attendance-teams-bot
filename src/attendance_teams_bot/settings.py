from enum import StrEnum
from uuid import UUID

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeMode(StrEnum):
    LOCAL = "local"
    TEAMS = "teams"


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    mode: RuntimeMode = Field(
        default=RuntimeMode.LOCAL,
        validation_alias="BOT_RUNTIME_MODE",
    )


class TeamsConnectionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    client_id: UUID = Field(validation_alias="CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID")
    tenant_id: UUID = Field(validation_alias="CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID")
    client_secret: SecretStr = Field(
        validation_alias="CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET"
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    mcp_endpoint: HttpUrl
