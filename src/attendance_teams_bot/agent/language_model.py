from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Describe a bot-approved tool that may be offered to a language model."""

    name: str
    description: str
    input_schema: Mapping[str, object]
    annotations: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Represent the single tool invocation selected by a language-model turn."""

    id: str
    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class NoTool:
    """The model did not select an approved action."""


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """Contain the message context and bot-approved tools for one model completion."""

    user_message: str
    reference_date: date
    timezone: str
    tools: tuple[ToolDefinition, ...]


ModelTurn = ToolCall | NoTool


class LanguageModelUnavailable(Exception):
    """The configured language model did not return a safe, usable response."""


class LanguageModel(Protocol):
    """Complete one turn without exposing provider-specific response types."""

    async def complete(self, request: ModelRequest) -> ModelTurn: ...
