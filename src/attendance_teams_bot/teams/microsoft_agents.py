from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from time import perf_counter
from typing import Protocol, cast

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

from attendance_teams_bot.agent.contracts import BotResponse
from attendance_teams_bot.auth.obo import DelegatedAuthenticationUnavailable, MsalOboTokenExchange
from attendance_teams_bot.observability import (
    correlation_scope,
    current_correlation_id,
    get_logger,
    log_event,
)
from attendance_teams_bot.settings import TeamsConnectionSettings
from attendance_teams_bot.teams.authenticated import (
    AttendanceMessageHandler,
    ChannelAuthenticatedMessageHandler,
)


class _Conversation(Protocol):
    conversation_type: str | None


class _Activity(Protocol):
    type: str
    text: str | None
    conversation: _Conversation | None


class _TurnContext(Protocol):
    @property
    def activity(self) -> _Activity: ...

    async def send_activity(self, text: str) -> object: ...


class _Authorization(Protocol):
    async def get_token(
        self,
        context: TurnContext,
        auth_handler_id: str | None = None,
    ) -> object: ...


class TeamsAuthorizationSsoTokenProvider:
    def __init__(self, *, authorization: _Authorization, auth_handler_id: str) -> None:
        self._authorization = authorization
        self._auth_handler_id = auth_handler_id

    async def get_token(self, context: _TurnContext) -> SecretStr:
        response = await self._authorization.get_token(
            cast(TurnContext, context), self._auth_handler_id
        )
        token = getattr(response, "token", None)
        if not isinstance(token, str) or not token:
            raise RuntimeError("Teams SSO token is unavailable")
        return SecretStr(token)


class _AttendanceHandler(Protocol):
    async def handle(self, *, message: str, mcp_access_token: SecretStr) -> BotResponse: ...


class _SsoTokenProvider(Protocol):
    async def get_token(self, context: _TurnContext) -> SecretStr: ...


class _OboTokenExchange(Protocol):
    async def exchange(self, user_assertion: SecretStr) -> SecretStr: ...


def install_teams_callback_observability(app: FastAPI) -> None:
    """Record callback completion without parsing or retaining activity bodies."""
    logger = get_logger("teams.callback")

    @app.middleware("http")
    async def observe_callback(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path != "/api/messages":
            return await call_next(request)
        started_at = perf_counter()
        with correlation_scope() as correlation_id:
            try:
                response = await call_next(request)
            except Exception as error:
                log_event(
                    logger,
                    logging.ERROR,
                    "teams_callback_failed",
                    correlation_id=str(correlation_id),
                    method=request.method,
                    path="/api/messages",
                    error_type=type(error).__name__,
                    duration_ms=max(0, round((perf_counter() - started_at) * 1000)),
                )
                raise
            log_event(
                logger,
                logging.INFO,
                "teams_callback_completed",
                correlation_id=str(correlation_id),
                method=request.method,
                path="/api/messages",
                status_code=response.status_code,
                duration_ms=max(0, round((perf_counter() - started_at) * 1000)),
            )
            return response


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


async def route_attendance_turn(
    *,
    context: _TurnContext,
    handler: _AttendanceHandler,
    sso_token_provider: _SsoTokenProvider,
    obo_token_exchange: _OboTokenExchange,
) -> None:
    correlation_id = current_correlation_id()
    logger = get_logger("teams.turn")
    if context.activity.type != "message" or context.activity.text is None:
        return
    conversation = context.activity.conversation
    if conversation is None or conversation.conversation_type != "personal":
        log_event(
            logger,
            logging.INFO,
            "teams_turn_rejected",
            correlation_id=str(correlation_id),
            outcome="nonpersonal_conversation",
        )
        await context.send_activity("Attendance is available only in a personal chat.")
        return
    message = context.activity.text.strip()
    if not message:
        log_event(
            logger,
            logging.INFO,
            "teams_turn_rejected",
            correlation_id=str(correlation_id),
            outcome="blank_message",
        )
        await context.send_activity("Please send a message so I can help.")
        return
    try:
        token_a = await sso_token_provider.get_token(context)
    except RuntimeError as error:
        log_event(
            logger,
            logging.WARNING,
            "teams_sso_token_unavailable",
            correlation_id=str(correlation_id),
            error_type=type(error).__name__,
        )
        await context.send_activity(
            "Authentication is temporarily unavailable. Please try again later."
        )
        return
    log_event(
        logger,
        logging.INFO,
        "teams_sso_token_acquired",
        correlation_id=str(correlation_id),
    )
    try:
        token = await obo_token_exchange.exchange(token_a)
    except DelegatedAuthenticationUnavailable as error:
        log_event(
            logger,
            logging.WARNING,
            "teams_obo_exchange_failed",
            correlation_id=str(correlation_id),
            error_type=type(error).__name__,
        )
        await context.send_activity(
            "Authentication is temporarily unavailable. Please try again later."
        )
        return
    log_event(
        logger,
        logging.INFO,
        "teams_obo_exchange_completed",
        correlation_id=str(correlation_id),
    )
    response = await handler.handle(message=message, mcp_access_token=token)
    try:
        await context.send_activity(response.text)
    except Exception as error:
        log_event(
            logger,
            logging.ERROR,
            "teams_reply_send_failed",
            correlation_id=str(correlation_id),
            error_type=type(error).__name__,
        )
        raise
    log_event(
        logger,
        logging.INFO,
        "teams_reply_sent",
        correlation_id=str(correlation_id),
    )


async def route_authenticated_turn(
    *,
    context: _TurnContext,
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


def create_authenticated_teams_http_app(
    *,
    connection: TeamsConnectionSettings,
    handler: ChannelAuthenticatedMessageHandler,
) -> FastAPI:
    sdk_configuration = load_configuration_from_env(
        {
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID": str(connection.client_id),
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID": str(connection.tenant_id),
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET": (
                connection.client_secret.get_secret_value()
            ),
        }
    )
    storage = MemoryStorage()
    connection_manager = MsalConnectionManager(**sdk_configuration)
    adapter = CloudAdapter(connection_manager=connection_manager)
    authorization = Authorization(storage, connection_manager, **sdk_configuration)
    agent_application: AgentApplication[TurnState] = AgentApplication(
        storage=storage,
        adapter=adapter,
        authorization=authorization,
        **sdk_configuration,
    )

    @agent_application.activity("message")
    async def on_message(context: TurnContext, _state: TurnState) -> None:
        await route_authenticated_turn(
            context=cast(_TurnContext, context),
            handler=handler,
        )

    app = FastAPI()
    install_teams_callback_observability(app)
    app.state.agent_configuration = connection_manager.get_default_connection_configuration()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/messages", response_model=None)
    @jwt_authorization_decorator  # type: ignore[untyped-decorator]
    async def messages_handler(request: Request) -> Response | None:
        return await start_agent_process(request, agent_application, adapter)

    return app


def create_attendance_teams_http_app(
    *,
    connection: TeamsConnectionSettings,
    attendance_handler: AttendanceMessageHandler,
    oauth_connection_name: str,
    delegated_scope: str,
    storage: Storage | None = None,
) -> FastAPI:
    if not oauth_connection_name.strip():
        raise ValueError("Teams SSO OAuth connection name is required")

    sdk_configuration = load_configuration_from_env(
        {
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID": str(connection.client_id),
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID": str(connection.tenant_id),
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET": (
                connection.client_secret.get_secret_value()
            ),
        }
    )
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
    agent_application: AgentApplication[TurnState] = AgentApplication(
        storage=route_storage,
        adapter=adapter,
        authorization=authorization,
        **sdk_configuration,
    )
    sso_token_provider = TeamsAuthorizationSsoTokenProvider(
        authorization=authorization,
        auth_handler_id=auth_handler_id,
    )
    obo_token_exchange = MsalOboTokenExchange(
        provider=default_connection,
        delegated_scope=delegated_scope,
    )

    @agent_application.activity("message", auth_handlers=[auth_handler_id])
    async def on_message(context: TurnContext, _state: TurnState) -> None:
        await route_attendance_turn(
            context=cast(_TurnContext, context),
            handler=attendance_handler,
            sso_token_provider=sso_token_provider,
            obo_token_exchange=obo_token_exchange,
        )

    app = FastAPI()
    install_teams_callback_observability(app)
    app.state.agent_configuration = connection_manager.get_default_connection_configuration()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/messages", response_model=None)
    @jwt_authorization_decorator  # type: ignore[untyped-decorator]
    async def messages_handler(request: Request) -> Response | None:
        activity = await request.json()
        response = await start_agent_process(request, agent_application, adapter)
        return normalize_oauth_invoke_response(
            activity=activity,
            response=response,
            oauth_connection_name=oauth_connection_name,
        )

    return app
