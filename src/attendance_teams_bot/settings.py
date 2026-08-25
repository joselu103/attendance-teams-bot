from enum import StrEnum

from pydantic import Field, HttpUrl
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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    mcp_endpoint: HttpUrl
