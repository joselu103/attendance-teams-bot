from typing import Protocol

from attendance_teams_bot.agent.contracts import BotResponse


class TeamsMessageHandler(Protocol):
    def handle(self, *, user_id: str, message: str) -> BotResponse: ...
