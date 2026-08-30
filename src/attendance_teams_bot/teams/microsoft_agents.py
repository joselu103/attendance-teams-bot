from __future__ import annotations

from typing import Protocol, cast

from fastapi import FastAPI, Request
from microsoft_agents.activity import load_configuration_from_env
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.core import (
    AgentApplication,
    Authorization,
    MemoryStorage,
    TurnContext,
    TurnState,
)
from microsoft_agents.hosting.fastapi import (
    CloudAdapter,
    jwt_authorization_decorator,
    start_agent_process,
)
from pydantic import SecretStr
from starlette.responses import Response

from attendance_teams_bot.agent.contracts import BotResponse
from attendance_teams_bot.settings import TeamsConnectionSettings
from attendance_teams_bot.teams.authenticated import ChannelAuthenticatedMessageHandler


class _Activity(Protocol):
    type: str
    text: str | None


class _TurnContext(Protocol):
    @property
    def activity(self) -> _Activity: ...

    async def send_activity(self, text: str) -> object: ...


class _AttendanceHandler(Protocol):
    async def handle(self, *, message: str, mcp_access_token: SecretStr) -> BotResponse: ...


class _SsoTokenProvider(Protocol):
    async def get_token(self, context: _TurnContext) -> SecretStr: ...


class _OboTokenExchange(Protocol):
    async def exchange(self, user_assertion: SecretStr) -> SecretStr: ...


async def route_attendance_turn(
    *,
    context: _TurnContext,
    handler: _AttendanceHandler,
    sso_token_provider: _SsoTokenProvider,
    obo_token_exchange: _OboTokenExchange,
) -> None:
    if context.activity.type != "message" or context.activity.text is None:
        return
    message = context.activity.text.strip()
    if not message:
        await context.send_activity("Please send a message so I can help.")
        return
    token = await obo_token_exchange.exchange(await sso_token_provider.get_token(context))
    response = await handler.handle(message=message, mcp_access_token=token)
    await context.send_activity(response.text)


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
    app.state.agent_configuration = connection_manager.get_default_connection_configuration()

    @app.post("/api/messages", response_model=None)
    @jwt_authorization_decorator  # type: ignore[untyped-decorator]
    async def messages_handler(request: Request) -> Response | None:
        return await start_agent_process(request, agent_application, adapter)

    return app
