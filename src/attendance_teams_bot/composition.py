from fastapi import FastAPI

from attendance_teams_bot.local import LocalUnconfiguredHandler
from attendance_teams_bot.settings import RuntimeMode, RuntimeSettings
from attendance_teams_bot.teams.adapter import TeamsActivityAdapter
from attendance_teams_bot.teams.http import create_teams_http_app


def create_http_app(settings: RuntimeSettings) -> FastAPI:
    if settings.mode is RuntimeMode.LOCAL:
        return create_local_http_app()

    raise ValueError("Authenticated Teams runtime composition is not configured")


def create_local_http_app() -> FastAPI:
    """Compose a connectivity-only HTTP app with no external adapters."""
    handler = LocalUnconfiguredHandler()
    adapter = TeamsActivityAdapter(handler=handler)
    return create_teams_http_app(adapter)
