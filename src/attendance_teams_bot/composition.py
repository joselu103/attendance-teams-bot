from fastapi import FastAPI

from attendance_teams_bot.agent.rule_based import RuleBasedIntentSelector
from attendance_teams_bot.application import create_application
from attendance_teams_bot.local import LocalUnconfiguredHandler
from attendance_teams_bot.mcp.client import StreamableHttpAttendanceMcpClient
from attendance_teams_bot.settings import RuntimeMode, Settings
from attendance_teams_bot.teams.adapter import TeamsActivityAdapter
from attendance_teams_bot.teams.authenticated import (
    AttendanceApplicationHandler,
    BotServiceConnectivityHandler,
)
from attendance_teams_bot.teams.http import create_teams_http_app
from attendance_teams_bot.teams.microsoft_agents import (
    create_attendance_teams_http_app,
    create_authenticated_teams_http_app,
)


def create_http_app(settings: Settings) -> FastAPI:
    if settings.mode is RuntimeMode.LOCAL:
        return create_local_http_app()
    if settings.mode is RuntimeMode.TEAMS:
        connection = settings.teams_connection
        if connection is None:
            raise ValueError("teams mode requires Bot Service configuration")
        integration = settings.attendance_integration
        if integration is not None:
            mcp_client = StreamableHttpAttendanceMcpClient(
                endpoint=str(integration.endpoint),
                timeout_seconds=integration.timeout_seconds,
            )
            application = create_application(
                intent_selector=RuleBasedIntentSelector(),
                mcp_client=mcp_client,
            )
            return create_attendance_teams_http_app(
                connection=connection,
                attendance_handler=AttendanceApplicationHandler(application),
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
