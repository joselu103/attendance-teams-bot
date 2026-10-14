from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from openai import AsyncOpenAI
from pydantic import SecretStr

from attendance_teams_bot.agent.language_model import (
    LanguageModelUnavailable,
    ModelRequest,
    ModelTurn,
    ToolDefinition,
)
from attendance_teams_bot.agent.model_contract import parse_response

CompletionCallable = Callable[..., Awaitable[object]]


@dataclass(frozen=True, slots=True)
class OpenAiLanguageModel:
    """Translate the prepared Teams contract to OpenAI transport types."""

    completion: CompletionCallable
    model: str

    async def complete(self, request: ModelRequest) -> ModelTurn:
        try:
            options: dict[str, object] = {
                "model": self.model,
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in request.messages
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "teams_markdown_reply",
                        "strict": True,
                        "schema": request.response_schema,
                    },
                },
            }
            if request.tools:
                options["tools"] = [_as_openai_tool(tool) for tool in request.tools]
                options["parallel_tool_calls"] = False
                options["tool_choice"] = "required"
            completion = await self.completion(**options)
            return _parse_completion(completion, request)
        except LanguageModelUnavailable:
            raise
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise
            raise LanguageModelUnavailable from None


def create_openai_language_model(*, api_key: SecretStr, model: str) -> OpenAiLanguageModel:
    client = AsyncOpenAI(api_key=api_key.get_secret_value())
    return OpenAiLanguageModel(completion=client.chat.completions.create, model=model)


def _as_openai_tool(tool: ToolDefinition) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
        "_meta": tool.meta,
    }


def _parse_completion(completion: object, request: ModelRequest) -> ModelTurn:
    choices = getattr(completion, "choices", None)
    if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)) or len(choices) != 1:
        raise LanguageModelUnavailable
    message = getattr(choices[0], "message", None)
    if message is None:
        raise LanguageModelUnavailable
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        if (
            not isinstance(tool_calls, Sequence)
            or isinstance(tool_calls, (str, bytes))
            or len(tool_calls) != 1
        ):
            raise LanguageModelUnavailable
        tool_call = tool_calls[0]
        function = getattr(tool_call, "function", None)
        return parse_response(
            call_id=getattr(tool_call, "id", None),
            name=getattr(function, "name", None),
            arguments_json=getattr(function, "arguments", None),
            content=getattr(message, "content", None),
            request=request,
        )
    return parse_response(
        call_id=None,
        name=None,
        arguments_json=None,
        content=getattr(message, "content", None),
        request=request,
    )
