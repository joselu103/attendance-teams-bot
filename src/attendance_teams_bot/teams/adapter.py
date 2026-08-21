from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from attendance_teams_bot.agent.contracts import BotResponse


class TeamsMessageHandler(Protocol):
    def handle(self, *, user_id: str, message: str) -> BotResponse: ...


class TeamsUser(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str


class TeamsConversation(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str


class TeamsActivity(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    activity_type: str = Field(alias="type")
    activity_id: str | None = Field(default=None, alias="id")
    text: str | None = None
    sender: TeamsUser | None = Field(default=None, alias="from")
    conversation: TeamsConversation | None = None


@dataclass(frozen=True, slots=True)
class TeamsTextReply:
    text: str


@dataclass(frozen=True, slots=True)
class TeamsActivityAdapter:
    handler: TeamsMessageHandler

    def handle_payload(self, payload: Mapping[str, Any]) -> TeamsTextReply | None:
        if payload.get("type") != "message":
            return None

        try:
            activity = TeamsActivity.model_validate(payload)
        except ValidationError:
            return TeamsTextReply(text="Please send a text message so I can help.")

        if activity.sender is None or activity.text is None:
            return TeamsTextReply(text="Please send a text message so I can help.")

        user_id = activity.sender.id.strip()
        message = activity.text.strip()
        if not user_id or not message:
            return TeamsTextReply(text="Please send a text message so I can help.")

        response = self.handler.handle(
            user_id=user_id,
            message=message,
        )
        return TeamsTextReply(text=response.text)
