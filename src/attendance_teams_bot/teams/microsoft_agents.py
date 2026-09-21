from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol, cast

from fastapi import FastAPI, Request
from microsoft_agents.activity import load_configuration_from_env
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.core import (
    AgentApplication,
    AuthHandler,
    Authorization,
    MemoryStorage,
    Storage,
    TurnContext,
    TurnState,
)
from microsoft_agents.hosting.fastapi import (
    CloudAdapter,
    jwt_authorization_decorator,
    start_agent_process,
)
from pydantic import SecretStr
from starlette.responses import JSONResponse, Response

from attendance_teams_bot.auth.obo import MsalOboTokenExchange
from attendance_teams_bot.observability import (
    authentication_event,
    get_logger,
    install_http_request_observability,
    log_event,
)
from attendance_teams_bot.settings import TeamsConnectionSettings
from attendance_teams_bot.teams.authenticated import (
    AttendanceApplication,
    AuthenticatedAttendanceTurnHandler,
    AuthenticatedTurnContext,
    ChannelAuthenticatedMessageHandler,
)


class _Authorization(Protocol):
    async def get_token(
        self,
        context: TurnContext,
        auth_handler_id: str | None = None,
    ) -> object: ...


class TeamsAuthorizationSsoTokenProvider:
    """Translate Microsoft Agents authorization results into the neutral SSO port."""

    def __init__(self, *, authorization: _Authorization, auth_handler_id: str) -> None:
        self._authorization = authorization
        self._auth_handler_id = auth_handler_id

    async def get_token(self, context: AuthenticatedTurnContext) -> SecretStr:
        try:
            response = await self._authorization.get_token(
                cast(TurnContext, context), self._auth_handler_id
            )
        except Exception as error:
            authentication_event(
                get_logger("teams.sso"),
                event="auth_failed",
                scheme="teams_sso",
                failure_reason=type(error).__name__,
            )
            raise RuntimeError("Teams SSO token is unavailable") from error
        token = getattr(response, "token", None)
        if not isinstance(token, str) or not token:
            authentication_event(
                get_logger("teams.sso"),
                event="auth_failed",
                scheme="teams_sso",
                failure_reason="token_unavailable",
            )
            raise RuntimeError("Teams SSO token is unavailable")
        authentication_event(get_logger("teams.sso"), event="auth_validated", scheme="teams_sso")
        return SecretStr(token)


def install_teams_callback_observability(app: FastAPI) -> None:
    """Install shared request lifecycle logging for the Teams HTTP entry point."""
    install_http_request_observability(app, logger_name="teams.http")


def normalize_oauth_invoke_response(
    *,
    activity: object,
    response: Response | None,
    oauth_connection_name: str,
) -> Response | None:
    """Fall back to interactive sign-in when Teams SSO token exchange is empty."""
    if response is None or response.status_code != 501:
        return response

    activity_type = activity.get("type") if isinstance(activity, Mapping) else None
    activity_name = activity.get("name") if isinstance(activity, Mapping) else None
    value = activity.get("value") if isinstance(activity, Mapping) else None
    exchange_id = value.get("id") if isinstance(value, Mapping) else None
    has_exchange_id = isinstance(exchange_id, str) and bool(exchange_id.strip())
    is_token_exchange = activity_type == "invoke" and activity_name == "signin/tokenExchange"
    matches = is_token_exchange and has_exchange_id
    if matches:
        reason = "matching_token_exchange"
    elif is_token_exchange:
        reason = "missing_exchange_id"
    else:
        reason = "unrelated_invoke"

    log_event(
        get_logger("teams.sso"),
        logging.WARNING,
        "teams_sso_token_exchange_fallback",
        activity_type=activity_type if isinstance(activity_type, str) else None,
        activity_name=activity_name if isinstance(activity_name, str) else None,
        has_exchange_id=has_exchange_id,
        upstream_status_code=501,
        outcome="interactive_sign_in_requested" if matches else "response_preserved",
        reason=reason,
    )
    if not matches:
        return response
    return JSONResponse(
        status_code=412,
        content={
            "id": exchange_id,
            "connectionName": oauth_connection_name,
            "failureDetail": "Token exchange failed; continue with interactive sign-in.",
        },
    )


async def route_authenticated_turn(
    *,
    context: AuthenticatedTurnContext,
    handler: ChannelAuthenticatedMessageHandler,
) -> None:
    if context.activity.type != "message" or context.activity.text is None:
        return
    message = context.activity.text.strip()
    if not message:
        await context.send_activity("Please send a message so I can help.")
        return
    response = await handler.handle(message=message)
    await context.send_activity(response.text)


def _sdk_configuration(connection: TeamsConnectionSettings) -> Any:
    return load_configuration_from_env(
        {
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID": str(connection.client_id),
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID": str(connection.tenant_id),
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET": (
                connection.client_secret.get_secret_value()
            ),
        }
    )


def create_authenticated_teams_http_app(
    *, connection: TeamsConnectionSettings, handler: ChannelAuthenticatedMessageHandler
) -> FastAPI:
    sdk_configuration = _sdk_configuration(connection)
    storage = MemoryStorage()
    connection_manager = MsalConnectionManager(**sdk_configuration)
    adapter = CloudAdapter(connection_manager=connection_manager)
    authorization = Authorization(storage, connection_manager, **sdk_configuration)
    agent_application: AgentApplication[TurnState] = AgentApplication(
        storage=storage, adapter=adapter, authorization=authorization, **sdk_configuration
    )

    @agent_application.activity("message")
    async def on_message(context: TurnContext, _state: TurnState) -> None:
        await route_authenticated_turn(
            context=cast(AuthenticatedTurnContext, context), handler=handler
        )

    return _create_http_app(
        connection_manager=connection_manager, agent_application=agent_application, adapter=adapter
    )


def create_attendance_teams_http_app(
    *,
    connection: TeamsConnectionSettings,
    attendance_application: AttendanceApplication,
    oauth_connection_name: str,
    delegated_scope: str,
    storage: Storage | None = None,
) -> FastAPI:
    if not oauth_connection_name.strip():
        raise ValueError("Teams SSO OAuth connection name is required")
    sdk_configuration = _sdk_configuration(connection)
    route_storage = storage or MemoryStorage()
    connection_manager = MsalConnectionManager(**sdk_configuration)
    default_connection = connection_manager.get_default_connection()
    if default_connection is None:
        raise RuntimeError("Microsoft Agents SDK default connection is unavailable")
    adapter = CloudAdapter(connection_manager=connection_manager)
    auth_handler_id = "attendance-teams-sso"
    auth_handler = AuthHandler(
        name=auth_handler_id,
        auth_type="UserAuthorization",
        abs_oauth_connection_name=oauth_connection_name,
    )
    authorization = Authorization(
        storage=route_storage,
        connection_manager=connection_manager,
        auth_handlers={auth_handler_id: auth_handler},
        **sdk_configuration,
    )
    attendance_handler = AuthenticatedAttendanceTurnHandler(
        application=attendance_application,
        sso_token_provider=TeamsAuthorizationSsoTokenProvider(
            authorization=authorization,
            auth_handler_id=auth_handler_id,
        ),
        obo_token_exchange=MsalOboTokenExchange(
            provider=default_connection,
            delegated_scope=delegated_scope,
        ),
    )
    agent_application: AgentApplication[TurnState] = AgentApplication(
        storage=route_storage, adapter=adapter, authorization=authorization, **sdk_configuration
    )

    @agent_application.activity("message", auth_handlers=[auth_handler_id])
    async def on_message(context: TurnContext, _state: TurnState) -> None:
        await attendance_handler.handle(cast(AuthenticatedTurnContext, context))

    return _create_http_app(
        connection_manager=connection_manager,
        agent_application=agent_application,
        adapter=adapter,
        oauth_connection_name=oauth_connection_name,
    )


def _create_http_app(
    *,
    connection_manager: MsalConnectionManager,
    agent_application: AgentApplication[TurnState],
    adapter: CloudAdapter,
    oauth_connection_name: str | None = None,
) -> FastAPI:
    app = FastAPI()
    install_teams_callback_observability(app)
    app.state.agent_configuration = connection_manager.get_default_connection_configuration()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/messages", response_model=None)
    @jwt_authorization_decorator  # type: ignore[untyped-decorator]
    async def messages_handler(request: Request) -> Response | None:
        activity = await request.json() if oauth_connection_name is not None else None
        response = await start_agent_process(request, agent_application, adapter)
        if oauth_connection_name is None:
            return response
        return normalize_oauth_invoke_response(
            activity=activity,
            response=response,
            oauth_connection_name=oauth_connection_name,
        )

    return app
