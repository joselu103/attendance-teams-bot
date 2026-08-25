from __future__ import annotations

from dataclasses import dataclass

from attendance_teams_bot.agent.contracts import BotResponse

_LOCAL_UNCONFIGURED_REPLY = (
    "Attendance Teams Bot is running locally, but authentication, MCP, "
    "and LLM adapters are not configured yet."
)


@dataclass(frozen=True, slots=True)
class LocalUnconfiguredHandler:
    """Connectivity-only handler used before external adapters are configured."""

    def handle(self, *, user_id: str, message: str) -> BotResponse:
        del user_id, message
        return BotResponse(text=_LOCAL_UNCONFIGURED_REPLY)
