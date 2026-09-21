from fastapi import FastAPI

from attendance_teams_bot.agent.openai import create_openai_language_model
from attendance_teams_bot.agent.orchestrator import AttendanceAgent
from attendance_teams_bot.local import LocalUnconfiguredHandler
from attendance_teams_bot.mcp.session import StreamableHttpAttendanceSessionFactory
from attendance_teams_bot.settings import (
    AttendanceIntegrationSettings,
    RuntimeMode,
    Settings,
    TeamsConnectionSettings,
)
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
    """Select the configured local, connectivity-only, or attendance app."""
    if settings.mode is RuntimeMode.LOCAL:
        return create_local_http_app()
    if settings.mode is RuntimeMode.TEAMS:
        if settings.attendance_integration is None:
            return create_connectivity_only_teams_http_app(settings)
        return create_attendance_enabled_teams_http_app(settings)

    raise ValueError(f"Unsupported runtime mode: {settings.mode}")


def create_local_http_app() -> FastAPI:
    """Compose a connectivity-only HTTP app with no external adapters."""
    handler = LocalUnconfiguredHandler()
    adapter = TeamsActivityAdapter(handler=handler)
    return create_teams_http_app(adapter)


def create_connectivity_only_teams_http_app(settings: Settings) -> FastAPI:
    """Compose the authenticated Teams endpoint without attendance integrations."""
    return create_authenticated_teams_http_app(
        connection=_teams_connection(settings),
        handler=BotServiceConnectivityHandler(),
    )


def create_attendance_enabled_teams_http_app(settings: Settings) -> FastAPI:
    """Compose the authenticated Teams endpoint with the attendance flow enabled."""
    integration = _attendance_integration(settings)
    return create_attendance_teams_http_app(
        connection=_teams_connection(settings),
        attendance_application=_attendance_application(integration),
        oauth_connection_name=integration.teams_sso_oauth_connection_name,
        delegated_scope=integration.delegated_scope,
    )


def _attendance_application(integration: AttendanceIntegrationSettings) -> AttendanceAgent:
    """Build the attendance orchestration boundary from validated integration settings."""
    return AttendanceAgent(
        language_model=create_openai_language_model(
            api_key=integration.openai.api_key,
            model=integration.openai.model,
        ),
        mcp_session_factory=_attendance_session_factory(integration),
    )


def _attendance_session_factory(
    integration: AttendanceIntegrationSettings,
) -> StreamableHttpAttendanceSessionFactory:
    """Create the authenticated MCP session factory for the configured endpoint."""
    return StreamableHttpAttendanceSessionFactory(
        endpoint=str(integration.endpoint),
        timeout_seconds=integration.timeout_seconds,
    )


def _teams_connection(settings: Settings) -> TeamsConnectionSettings:
    """Return the required Bot Service connection for a Teams composition path."""
    connection = settings.teams_connection
    if connection is None:
        raise ValueError("teams mode requires Bot Service configuration")
    return connection


def _attendance_integration(settings: Settings) -> AttendanceIntegrationSettings:
    """Return the required validated attendance integration for the enabled path."""
    integration = settings.attendance_integration
    if integration is None:
        raise ValueError("attendance-enabled Teams app requires integration configuration")
    return integration
