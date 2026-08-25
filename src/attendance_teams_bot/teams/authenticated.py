from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from attendance_teams_bot.agent.contracts import BotResponse

_SAFE_BOT_SERVICE_REPLY = (
    "Microsoft Bot Service connectivity is verified, but Teams SSO, MCP, "
    "and LLM adapters are not configured yet."
)


class ChannelAuthenticatedMessageHandler(Protocol):
    async def handle(self, *, message: str) -> BotResponse: ...


@dataclass(frozen=True, slots=True)
class BotServiceConnectivityHandler:
    """Safe handler used after Bot Service request authentication."""

    async def handle(self, *, message: str) -> BotResponse:
        del message
        return BotResponse(text=_SAFE_BOT_SERVICE_REPLY)
