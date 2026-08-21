from __future__ import annotations

from dataclasses import dataclass

from attendance_teams_bot.agent.contracts import BotResponse
from attendance_teams_bot.agent.language_model import LanguageModel
from attendance_teams_bot.auth.identity import IdentityProvider
from attendance_teams_bot.mcp.client import AttendanceMcpClient

_GREETING = "Hello. Ask me about your attendance for a specific date range."


@dataclass(frozen=True, slots=True)
class Application:
    identity_provider: IdentityProvider
    mcp_client: AttendanceMcpClient
    language_model: LanguageModel

    def handle(self, *, user_id: str, message: str) -> BotResponse:
        del user_id, message
        return BotResponse(text=_GREETING)


def create_application(
    *,
    identity_provider: IdentityProvider,
    mcp_client: AttendanceMcpClient,
    language_model: LanguageModel,
) -> Application:
    return Application(
        identity_provider=identity_provider,
        mcp_client=mcp_client,
        language_model=language_model,
    )
