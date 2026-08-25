from __future__ import annotations

from typing import Protocol

from attendance_teams_bot.teams.authenticated import ChannelAuthenticatedMessageHandler


class _Activity(Protocol):
    type: str
    text: str | None


class _TurnContext(Protocol):
    activity: _Activity

    async def send_activity(self, text: str) -> None: ...


async def route_authenticated_turn(
    *,
    context: _TurnContext,
    handler: ChannelAuthenticatedMessageHandler,
) -> None:
    if context.activity.type != "message" or context.activity.text is None:
        return

    response = await handler.handle(message=context.activity.text.strip())
    await context.send_activity(response.text)
