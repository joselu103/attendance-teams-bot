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
    PRESENTATION = "presentation"


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
class PresentationPlan:
    """Model-authored non-factual framing for an immutable safe projection."""

    title: str
    context: str | None = None


@dataclass(frozen=True, slots=True)
class FinalResponse:
    """The constrained final Teams Markdown supplied by the model."""

    markdown: str
    language: ReplyLanguage
    guidance_kind: GuidanceKind | None = None
    presentation: PresentationPlan | None = None


@dataclass(frozen=True, slots=True)
class ToolResultView:
    """Approved, data-only result projection made available in a later model turn."""

    call_id: str
    tool_name: str
    result: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """A fully prepared provider-neutral turn with mode invariants enforced."""

    mode: ModelMode
    messages: tuple[PreparedMessage, ...]
    response_schema: Mapping[str, object]
    reference_date: date
    tools: tuple[ToolDefinition, ...] = ()
    tool_results: tuple[ToolResultView, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ModelMode):
            raise ValueError("a model request requires a valid mode")
        if not self.messages or self.messages[0].role != "system":
            raise ValueError("a model request requires a leading system message")
        if self.mode is ModelMode.PRE_AUTH and (self.tools or self.tool_results):
            raise ValueError("pre-auth mode cannot receive tools or tool results")
        if self.mode is ModelMode.SELECTION and (not self.tools or self.tool_results):
            raise ValueError("selection mode requires tools and forbids tool results")
        if self.mode is ModelMode.PRESENTATION and (self.tools or not self.tool_results):
            raise ValueError("presentation mode requires results and forbids tools")


ModelTurn = ToolCall | FinalResponse


class LanguageModelUnavailable(Exception):
    """The configured language model did not return a safe, usable response."""


class LanguageModel(Protocol):
    """Complete one bounded agent-loop turn without provider-specific types."""

    async def complete(self, request: ModelRequest) -> ModelTurn: ...
