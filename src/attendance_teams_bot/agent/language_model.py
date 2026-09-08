from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ToolDefinition:
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
class NoTool:
    """The model did not select an approved action."""


@dataclass(frozen=True, slots=True)
class ModelRequest:
    user_message: str
    reference_date: date
    timezone: str
    tools: tuple[ToolDefinition, ...]


ModelTurn = ToolCall | NoTool


class LanguageModelUnavailable(Exception):
    """The configured language model did not return a safe, usable response."""


class LanguageModel(Protocol):
    async def complete(self, request: ModelRequest) -> ModelTurn: ...
