from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from openai import AsyncOpenAI
from pydantic import SecretStr

from attendance_teams_bot.agent.language_model import (
    LanguageModelUnavailable,
    ModelRequest,
    ModelTurn,
    NoTool,
    ToolCall,
    ToolDefinition,
)
from attendance_teams_bot.observability import (
    OperationLifecycle,
    get_logger,
    message_input_metadata,
)

CompletionCallable = Callable[..., Awaitable[object]]


@dataclass(frozen=True, slots=True)
class OpenAiLanguageModel:
    """OpenAI translation boundary; no OpenAI types cross this class boundary."""

    completion: CompletionCallable
    model: str

    async def complete(self, request: ModelRequest) -> ModelTurn:
        logger = get_logger("agent.openai")
        metadata = {
            **message_input_metadata(request.user_message),
            "tool_count": len(request.tools),
        }
        lifecycle = OperationLifecycle(
            logger,
            handler="OpenAiLanguageModel.complete",
            operation="language_model_completion",
            input_metadata=metadata,
        )
        lifecycle.start(step="request")
        try:
            completion = await self.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": _system_prompt(request)},
                    {"role": "user", "content": request.user_message},
                ],
                tools=[_as_openai_tool(tool) for tool in request.tools],
                parallel_tool_calls=False,
            )
            turn = _parse_completion(completion)
        except LanguageModelUnavailable as error:
            lifecycle.fail(error, step="response_validation")
            raise
        except asyncio.CancelledError:
            lifecycle.cancel()
            raise
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise
            lifecycle.fail(error)
            raise LanguageModelUnavailable from None
        lifecycle.succeed(step="response_validation")
        return turn


def create_openai_language_model(*, api_key: SecretStr, model: str) -> OpenAiLanguageModel:
    client = AsyncOpenAI(api_key=api_key.get_secret_value())
    return OpenAiLanguageModel(completion=client.chat.completions.create, model=model)


def _system_prompt(request: ModelRequest) -> str:
    return (
        "You select only supplied requester attendance tools. Use no invented facts, identity, "
        "authorization, or results. Today is "
        f"{request.reference_date.isoformat()} in {request.timezone}. "
        "Tool dates must be ISO calendar dates in that timezone and cover no more than "
        "12 rolling calendar months. The bot resolves explicit ISO dates, named months, "
        "this/last month, this/last week, and last/past 1–12 months locally before you run. "
        "For other date wording, either choose the supplied tool with an unambiguous range "
        "or use the guidance tool."
    )


def _as_openai_tool(tool: ToolDefinition) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.input_schema),
        },
    }


def _parse_completion(completion: object) -> ModelTurn:
    choices = getattr(completion, "choices", None)
    if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)) or len(choices) != 1:
        raise LanguageModelUnavailable
    message = getattr(choices[0], "message", None)
    if message is None:
        raise LanguageModelUnavailable
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls is None:
        return NoTool()
    if (
        not isinstance(tool_calls, Sequence)
        or isinstance(tool_calls, (str, bytes))
        or len(tool_calls) != 1
    ):
        raise LanguageModelUnavailable
    return _parse_tool_call(tool_calls[0])


def _parse_tool_call(tool_call: object) -> ToolCall:
    call_id = getattr(tool_call, "id", None)
    function = getattr(tool_call, "function", None)
    name = getattr(function, "name", None)
    arguments_json = getattr(function, "arguments", None)
    if (
        not isinstance(call_id, str)
        or not call_id.strip()
        or not isinstance(name, str)
        or not name.strip()
        or not isinstance(arguments_json, str)
    ):
        raise LanguageModelUnavailable
    try:
        arguments = json.loads(arguments_json)
    except json.JSONDecodeError:
        raise LanguageModelUnavailable from None
    if not isinstance(arguments, Mapping) or not all(isinstance(key, str) for key in arguments):
        raise LanguageModelUnavailable
    return ToolCall(id=call_id, name=name, arguments=dict(arguments))
