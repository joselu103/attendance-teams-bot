from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Literal, Protocol

ReplyLanguage = Literal["en", "sl"]
GuidanceKind = Literal[
    "greeting",
    "attendance_clarification",
    "attendance_scope_guidance",
    "attendance_needed",
]


class ModelMode(StrEnum):
    """The mutually exclusive model turns the Teams agent permits."""

    PRE_AUTH = "pre_auth"
    SELECTION = "selection"


@dataclass(frozen=True, slots=True)
class PreparedMessage:
    """A provider-neutral message assembled by the Teams agent."""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A bounded model tool whose remote metadata is opaque to the bot."""

    name: str
    description: str
    input_schema: Mapping[str, object]
    annotations: Mapping[str, object] = field(default_factory=dict)
    meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class FinalResponse:
    """A constrained structured reply for a tool-free routing turn."""

    markdown: str
    language: ReplyLanguage
    guidance_kind: GuidanceKind | None = None


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """A fully prepared provider-neutral turn with mode invariants enforced."""

    mode: ModelMode
    messages: tuple[PreparedMessage, ...]
    response_schema: Mapping[str, object]
    reference_date: date
    tools: tuple[ToolDefinition, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ModelMode):
            raise ValueError("a model request requires a valid mode")
        if not self.messages or self.messages[0].role != "system":
            raise ValueError("a model request requires a leading system message")
        if self.mode is ModelMode.PRE_AUTH and self.tools:
            raise ValueError("pre-auth mode cannot receive tools")
        if self.mode is ModelMode.SELECTION and not self.tools:
            raise ValueError("selection mode requires tools")


ModelTurn = ToolCall | FinalResponse


class LanguageModelUnavailable(Exception):
    """The configured language model did not return a safe, usable response."""


class LanguageModel(Protocol):
    """Complete one bounded agent-loop turn without provider-specific types."""

    async def complete(self, request: ModelRequest) -> ModelTurn: ...
