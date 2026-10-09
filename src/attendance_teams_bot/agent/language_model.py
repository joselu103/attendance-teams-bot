from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Protocol

from attendance_teams_bot.memory.models import ChatMessage

ReplyLanguage = Literal["en", "sl"]
GuidanceKind = Literal["greeting", "attendance_clarification", "attendance_scope_guidance"]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A bot-owned model tool; provider and MCP metadata never enter this type."""

    name: str
    description: str
    input_schema: Mapping[str, object]
    annotations: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class FinalResponse:
    """The constrained final Teams Markdown supplied by the model."""

    markdown: str
    language: ReplyLanguage
    guidance_kind: GuidanceKind | None = None
    presentation: PresentationPlan | None = None


@dataclass(frozen=True, slots=True)
class PresentationPlan:
    """Model-authored non-factual framing for an immutable safe projection."""

    title: str
    context: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResultView:
    """Approved, data-only result projection made available in a later model turn."""

    call_id: str
    tool_name: str
    result: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ModelRequest:
    user_message: str
    reference_date: date
    timezone: str
    tools: tuple[ToolDefinition, ...]
    tool_results: tuple[ToolResultView, ...] = ()
    display_name: str | None = None
    pre_auth_guidance: bool = False
    history: tuple[ChatMessage, ...] = ()


ModelTurn = ToolCall | FinalResponse


class LanguageModelUnavailable(Exception):
    """The configured language model did not return a safe, usable response."""


class LanguageModel(Protocol):
    """Complete one bounded agent-loop turn without provider-specific types."""

    async def complete(self, request: ModelRequest) -> ModelTurn: ...
