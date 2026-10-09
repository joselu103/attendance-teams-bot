from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ChatRole = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """A neutral, minimized message eligible for conversation context."""

    role: ChatRole
    content: str
    created_at: datetime
