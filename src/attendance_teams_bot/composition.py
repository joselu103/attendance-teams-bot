from fastapi import FastAPI

from attendance_teams_bot.agent.openai import create_openai_language_model
from attendance_teams_bot.agent.orchestrator import AttendanceAgent
from attendance_teams_bot.local import LocalUnconfiguredHandler
from attendance_teams_bot.mcp.session import StreamableHttpAttendanceSessionFactory
from attendance_teams_bot.settings import RuntimeMode, Settings
from attendance_teams_bot.teams.adapter import TeamsActivityAdapter
from attendance_teams_bot.teams.authenticated import (
    BotServiceConnectivityHandler,
)
from attendance_teams_bot.teams.http import create_teams_http_app
from attendance_teams_bot.teams.microsoft_agents import (
    create_attendance_teams_http_app,
    create_authenticated_teams_http_app,
)


def create_http_app(settings: Settings) -> FastAPI:
    """Compose the runtime-selected local, connectivity-only, or authenticated Teams app."""
    if settings.mode is RuntimeMode.LOCAL:
        return create_local_http_app()
    if settings.mode is RuntimeMode.TEAMS:
        connection = settings.teams_connection
        if connection is None:
            raise ValueError("teams mode requires Bot Service configuration")
        integration = settings.attendance_integration
        if integration is not None:
            mcp_session_factory = StreamableHttpAttendanceSessionFactory(
                endpoint=str(integration.endpoint),
                timeout_seconds=integration.timeout_seconds,
            )
            application = AttendanceAgent(
                language_model=create_openai_language_model(
                    api_key=integration.openai.api_key,
                    model=integration.openai.model,
                ),
                mcp_session_factory=mcp_session_factory,
            )
            return create_attendance_teams_http_app(
                connection=connection,
                attendance_application=application,
                oauth_connection_name=integration.teams_sso_oauth_connection_name,
                delegated_scope=integration.delegated_scope,
            )
        return create_authenticated_teams_http_app(
            connection=connection,
            handler=BotServiceConnectivityHandler(),
        )

    raise ValueError(f"Unsupported runtime mode: {settings.mode}")


def create_local_http_app() -> FastAPI:
    """Compose a connectivity-only HTTP app with no external adapters."""
    handler = LocalUnconfiguredHandler()
    adapter = TeamsActivityAdapter(handler=handler)
    return create_teams_http_app(adapter)
