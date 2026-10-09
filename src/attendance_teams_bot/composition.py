from typing import cast

from fastapi import FastAPI

from attendance_teams_bot.agent.openai import create_openai_language_model
from attendance_teams_bot.agent.orchestrator import AttendanceAgent, McpSessionFactory
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
    BotServiceOnlyHandler,
)
from attendance_teams_bot.teams.http import create_teams_http_app
from attendance_teams_bot.teams.microsoft_agents import (
    create_attendance_teams_http_app,
)
from attendance_teams_bot.teams.microsoft_agents import (
    create_bot_service_only_http_app as create_bot_service_only_callback_http_app,
)


def create_http_app(settings: Settings) -> FastAPI:
    """Select the configured local, connectivity-only, or attendance app."""
    if settings.mode is RuntimeMode.LOCAL:
        return create_local_http_app()

    connection = cast(TeamsConnectionSettings, settings.teams_connection)
    integration = settings.attendance_integration
    if integration is None:
        return create_bot_service_only_app(connection)

    return create_attendance_enabled_teams_http_app(connection, integration)


def create_local_http_app() -> FastAPI:
    """Compose a connectivity-only HTTP app with no external adapters."""
    handler = LocalUnconfiguredHandler()
    adapter = TeamsActivityAdapter(handler=handler)
    return create_teams_http_app(adapter)


def create_bot_service_only_app(connection: TeamsConnectionSettings) -> FastAPI:
    """Compose the Bot Service-only endpoint without attendance integrations."""
    return create_bot_service_only_callback_http_app(
        connection=connection,
        handler=BotServiceOnlyHandler(),
    )


def create_attendance_enabled_teams_http_app(
    connection: TeamsConnectionSettings,
    integration: AttendanceIntegrationSettings,
) -> FastAPI:
    """Compose the Teams SSO/OBO attendance endpoint."""
    return create_attendance_teams_http_app(
        connection=connection,
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
        mcp_session_factory=cast(McpSessionFactory, _attendance_session_factory(integration)),
    )


def _attendance_session_factory(
    integration: AttendanceIntegrationSettings,
) -> StreamableHttpAttendanceSessionFactory:
    """Create the authenticated MCP session factory for the configured endpoint."""
    return StreamableHttpAttendanceSessionFactory(
        endpoint=str(integration.endpoint),
        timeout_seconds=integration.timeout_seconds,
    )
