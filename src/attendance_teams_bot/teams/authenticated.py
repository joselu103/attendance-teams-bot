from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

from attendance_teams_bot.agent.contracts import BotResponse

_SAFE_BOT_SERVICE_REPLY = (
    "Microsoft Bot Service connectivity is verified, but Teams SSO, MCP, "
    "and LLM adapters are not configured yet."
)


class ChannelAuthenticatedMessageHandler(Protocol):
    async def handle(self, *, message: str) -> BotResponse: ...


class AttendanceMessageHandler(Protocol):
    async def handle(self, *, message: str, mcp_access_token: SecretStr) -> BotResponse: ...


class AttendanceApplication(Protocol):
    async def handle(self, *, message: str, mcp_access_token: SecretStr) -> BotResponse: ...


@dataclass(frozen=True, slots=True)
class BotServiceConnectivityHandler:
    """Safe handler used after Bot Service request authentication."""

    async def handle(self, *, message: str) -> BotResponse:
        del message
        return BotResponse(text=_SAFE_BOT_SERVICE_REPLY)


@dataclass(frozen=True, slots=True)
class AttendanceApplicationHandler:
    """SDK-independent adapter from authenticated Teams turns to the application."""

    application: AttendanceApplication

    async def handle(self, *, message: str, mcp_access_token: SecretStr) -> BotResponse:
        return await self.application.handle(message=message, mcp_access_token=mcp_access_token)
