from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol, cast

import structlog
from fastapi import FastAPI, Request
from microsoft_agents.activity import Activity, Attachment, load_configuration_from_env
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

from attendance_teams_bot.agent.continuation import HISTORY_VERB, ContinuationSigner, TeamsBinding
from attendance_teams_bot.agent.contracts import BotAttachment
from attendance_teams_bot.auth.obo import MsalOboTokenExchange
from attendance_teams_bot.observability import (
    authentication_event,
    install_http_request_observability,
)
from attendance_teams_bot.settings import TeamsConnectionSettings
from attendance_teams_bot.teams.authenticated import (
    AttendanceApplication,
    BotServiceAuthenticatedTurnContext,
    BotServiceOnlyMessageHandler,
    SsoOboAttendanceTurnHandler,
)

_LOGGER = structlog.get_logger(__name__)
_ATTENDANCE_AUTH_HANDLER_ID = "attendance-teams-sso"
_KNOWN_SSO_FAILURE_CODES = frozenset(
    {
        "resourcematchfailed",
        "installedappnotfound",
        "authrequestfailed",
        "tokenmissing",
        "oauthcardnotvalid",
        "installappfailed",
        "invokeerror",
        "userconsentrequired",
        "interactionrequired",
    }
)


class _Authorization(Protocol):
    async def get_token(
        self,
        context: TurnContext,
        auth_handler_id: str | None = None,
    ) -> object: ...


class SdkAttendanceContext:
    """Keep SDK activities and attachment translation behind the transport adapter."""

    def __init__(self, context: TurnContext) -> None:
        self.sdk_context = context

    @property
    def activity(self) -> Any:
        return self.sdk_context.activity

    @property
    def history_binding(self) -> TeamsBinding | None:
        activity = self.sdk_context.activity
        conversation, sender = activity.conversation, activity.from_property
        try:
            return TeamsBinding(
                conversation.tenant_id or "" if conversation else "",
                sender.aad_object_id or "" if sender else "",
                conversation.id or "" if conversation else "",
            )
        except ValueError:
            return None

    async def send_activity(self, text: str) -> object:
        return await self.sdk_context.send_activity(text)

    async def send_attachment(self, attachment: BotAttachment) -> object:
        return await self.sdk_context.send_activity(
            Activity(
                type="message",
                attachments=[
                    Attachment(
                        content_type=attachment.content_type, content=dict(attachment.content)
                    )
                ],
            )
        )


class TeamsAuthorizationSsoTokenProvider:
    """Translate Microsoft Agents authorization results into the neutral SSO port."""

    def __init__(self, *, authorization: _Authorization, auth_handler_id: str) -> None:
        self._authorization = authorization
        self._auth_handler_id = auth_handler_id

    async def get_token(self, context: BotServiceAuthenticatedTurnContext) -> SecretStr:
        """Retrieve a non-empty Teams SSO token or raise the neutral runtime error."""
        try:
            response = await self._authorization.get_token(
                context.sdk_context
                if isinstance(context, SdkAttendanceContext)
                else cast(TurnContext, context),
                self._auth_handler_id,
            )
        except Exception as error:
            authentication_event(
                _LOGGER,
                event="auth_failed",
                scheme="teams_sso",
                failure_reason=type(error).__name__,
            )
            raise RuntimeError("Teams SSO token is unavailable") from error
        token = getattr(response, "token", None)
        if not isinstance(token, str) or not token:
            authentication_event(
                _LOGGER,
                event="auth_failed",
                scheme="teams_sso",
                failure_reason="token_unavailable",
            )
            raise RuntimeError("Teams SSO token is unavailable")
        authentication_event(_LOGGER, event="auth_validated", scheme="teams_sso")
        return SecretStr(token)


def install_teams_callback_observability(app: FastAPI) -> None:
    """Install shared request lifecycle logging for the Teams HTTP entry point."""
    install_http_request_observability(app)


def _sso_failure_code(activity: object) -> str:
    """Return an allowlisted Teams SSO failure category without retaining raw values."""
    if not isinstance(activity, Mapping):
        return "unknown"
    if activity.get("type") != "invoke" or activity.get("name") != "signin/failure":
        return "unknown"

    value = activity.get("value")
    code = value.get("code") if isinstance(value, Mapping) else None
    normalized_code = code.casefold() if isinstance(code, str) else ""
    return normalized_code if normalized_code in _KNOWN_SSO_FAILURE_CODES else "unknown"


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

    _LOGGER.log(
        logging.WARNING,
        "teams_sso_token_exchange_fallback",
        activity_type=activity_type if isinstance(activity_type, str) else None,
        activity_name=activity_name if isinstance(activity_name, str) else None,
        has_exchange_id=has_exchange_id,
        upstream_status_code=501,
        outcome="interactive_sign_in_requested" if matches else "response_preserved",
        reason=reason,
        sso_failure_code=_sso_failure_code(activity),
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


async def route_bot_service_authenticated_turn(
    *,
    context: BotServiceAuthenticatedTurnContext,
    handler: BotServiceOnlyMessageHandler,
) -> None:
    """Forward a non-blank Bot Service-authenticated turn to a neutral handler."""
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


def create_bot_service_only_http_app(
    *, connection: TeamsConnectionSettings, handler: BotServiceOnlyMessageHandler
) -> FastAPI:
    """Create a Bot Service-only app with fixed, non-attendance replies."""
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
        await route_bot_service_authenticated_turn(
            context=cast(BotServiceAuthenticatedTurnContext, context), handler=handler
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
    continuation_signer: ContinuationSigner | None = None,
) -> FastAPI:
    """Create the authenticated Teams app that performs SSO, OBO, and attendance handling."""
    _validate_oauth_connection_name(oauth_connection_name)

    sdk_configuration = _sdk_configuration(connection)
    route_storage = storage or MemoryStorage()
    connection_manager = MsalConnectionManager(**sdk_configuration)
    authorization = _create_attendance_authorization(
        storage=route_storage,
        connection_manager=connection_manager,
        oauth_connection_name=oauth_connection_name,
        sdk_configuration=sdk_configuration,
    )

    default_connection = _require_default_connection(connection_manager)
    attendance_handler = _create_attendance_turn_handler(
        attendance_application=attendance_application,
        authorization=authorization,
        default_connection=default_connection,
        delegated_scope=delegated_scope,
        continuation_signer=continuation_signer,
    )

    adapter = CloudAdapter(connection_manager=connection_manager)
    agent_application: AgentApplication[TurnState] = AgentApplication(
        storage=route_storage, adapter=adapter, authorization=authorization, **sdk_configuration
    )
    _register_attendance_message_handler(
        agent_application=agent_application,
        attendance_handler=attendance_handler,
    )

    return _create_http_app(
        connection_manager=connection_manager,
        agent_application=agent_application,
        adapter=adapter,
        oauth_connection_name=oauth_connection_name,
    )


def _validate_oauth_connection_name(oauth_connection_name: str) -> None:
    if not oauth_connection_name.strip():
        raise ValueError("Teams SSO OAuth connection name is required")


def _create_attendance_authorization(
    *,
    storage: Storage,
    connection_manager: MsalConnectionManager,
    oauth_connection_name: str,
    sdk_configuration: Any,
) -> Authorization:
    auth_handler = AuthHandler(
        name=_ATTENDANCE_AUTH_HANDLER_ID,
        auth_type="UserAuthorization",
        abs_oauth_connection_name=oauth_connection_name,
    )
    return Authorization(
        storage=storage,
        connection_manager=connection_manager,
        auth_handlers={_ATTENDANCE_AUTH_HANDLER_ID: auth_handler},
        **sdk_configuration,
    )


def _create_attendance_turn_handler(
    *,
    attendance_application: AttendanceApplication,
    authorization: Authorization,
    default_connection: Any,
    delegated_scope: str,
    continuation_signer: ContinuationSigner | None = None,
) -> SsoOboAttendanceTurnHandler:
    return SsoOboAttendanceTurnHandler(
        application=attendance_application,
        continuation_signer=continuation_signer,
        sso_token_provider=TeamsAuthorizationSsoTokenProvider(
            authorization=authorization,
            auth_handler_id=_ATTENDANCE_AUTH_HANDLER_ID,
        ),
        obo_token_exchange=MsalOboTokenExchange(
            provider=default_connection,
            delegated_scope=delegated_scope,
        ),
    )


def _require_default_connection(connection_manager: MsalConnectionManager) -> Any:
    default_connection = connection_manager.get_default_connection()
    if default_connection is None:
        raise RuntimeError("Microsoft Agents SDK default connection is unavailable")
    return default_connection


def _register_attendance_message_handler(
    *,
    agent_application: AgentApplication[TurnState],
    attendance_handler: SsoOboAttendanceTurnHandler,
) -> None:
    @agent_application.adaptive_card.action_submit(
        HISTORY_VERB, auth_handlers=[_ATTENDANCE_AUTH_HANDLER_ID]
    )
    async def on_submit(context: TurnContext, _state: TurnState, data: Any) -> None:
        await attendance_handler.handle_submission(SdkAttendanceContext(context), data)

    @agent_application.activity("message", auth_handlers=[_ATTENDANCE_AUTH_HANDLER_ID])
    async def on_message(context: TurnContext, _state: TurnState) -> None:
        await attendance_handler.handle(SdkAttendanceContext(context))


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
