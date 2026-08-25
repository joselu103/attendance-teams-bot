from __future__ import annotations

from os import environ
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
from starlette.responses import Response

from attendance_teams_bot.settings import TeamsConnectionSettings
from attendance_teams_bot.teams.authenticated import ChannelAuthenticatedMessageHandler


class _Activity(Protocol):
    type: str
    text: str | None


class _TurnContext(Protocol):
    @property
    def activity(self) -> _Activity: ...

    async def send_activity(self, text: str) -> object: ...


async def route_authenticated_turn(
    *,
    context: _TurnContext,
    handler: ChannelAuthenticatedMessageHandler,
) -> None:
    if context.activity.type != "message" or context.activity.text is None:
        return

    response = await handler.handle(message=context.activity.text.strip())
    await context.send_activity(response.text)


def create_authenticated_teams_http_app(
    *,
    handler: ChannelAuthenticatedMessageHandler,
) -> FastAPI:
    TeamsConnectionSettings()  # type: ignore[call-arg]
    sdk_configuration = load_configuration_from_env(dict(environ))
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
