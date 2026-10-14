import asyncio
from dataclasses import dataclass
from datetime import date

import pytest

from attendance_teams_bot.agent.language_model import (
    FinalResponse,
    LanguageModelUnavailable,
    PresentationPlan,
    ToolCall,
    ToolDefinition,
    ToolResultView,
)
from attendance_teams_bot.agent.model_contract import presentation_request, selection_request
from attendance_teams_bot.agent.openai import OpenAiLanguageModel


@dataclass(frozen=True)
class Function:
    name: str
    arguments: str


@dataclass(frozen=True)
class Call:
    id: str
    function: Function


@dataclass(frozen=True)
class Message:
    content: str | None = None
    tool_calls: tuple[Call, ...] | None = None


@dataclass(frozen=True)
class Choice:
    message: Message


@dataclass(frozen=True)
class Completion:
    choices: tuple[Choice, ...]


class FakeCompletion:
    def __init__(self, response: object):
        self.response, self.requests = response, []

    async def __call__(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        return self.response


def selection(tools: tuple[ToolDefinition, ...] | None = None):
    return selection_request(
        message="show",
        reference_date=date(2026, 8, 15),
        history=(),
        tools=tools or (ToolDefinition("list_my_attendance_events", "owned", {"type": "object"}),),
    )


@pytest.mark.anyio
async def test_openai_translates_prepared_selection_and_preserves_metadata() -> None:
    schema = {"type": "object", "properties": {"start_date": {"type": "string"}}}
    examples = {"examples": [{"start_date": "2026-08-01"}]}
    request = selection((ToolDefinition("remote", "Server text", schema, {}, examples),))
    completion = FakeCompletion(
        Completion((Choice(Message(tool_calls=(Call("x", Function("remote", "{}")),))),))
    )

    assert await OpenAiLanguageModel(completion, "test").complete(request) == ToolCall(
        "x", "remote", {}
    )

    sent = completion.requests[0]
    assert sent["messages"] == [
        {"role": message.role, "content": message.content} for message in request.messages
    ]
    assert sent["response_format"]["json_schema"]["schema"] is request.response_schema
    tool = sent["tools"][0]
    assert tool["function"]["description"] == "Server text"
    assert tool["function"]["parameters"] is schema and tool["_meta"] is examples
    assert sent["parallel_tool_calls"] is False
    assert sent["tool_choice"] == "required"


@pytest.mark.anyio
async def test_openai_rejects_parallel_or_forbidden_presentation_tool_calls() -> None:
    parallel = OpenAiLanguageModel(
        FakeCompletion(
            Completion(
                (
                    Choice(
                        Message(
                            tool_calls=(
                                Call("a", Function("x", "{}")),
                                Call("b", Function("x", "{}")),
                            )
                        )
                    ),
                )
            )
        ),
        "test",
    )
    with pytest.raises(LanguageModelUnavailable):
        await parallel.complete(selection())

    presentation = presentation_request(
        message="show",
        reference_date=date(2026, 8, 15),
        history=(),
        tool_results=(ToolResultView("execution", "tool", {"safe": True}),),
    )
    forbidden = OpenAiLanguageModel(
        FakeCompletion(
            Completion((Choice(Message(tool_calls=(Call("x", Function("tool", "{}")),))),))
        ),
        "test",
    )
    with pytest.raises(LanguageModelUnavailable):
        await forbidden.complete(presentation)


@pytest.mark.anyio
async def test_openai_preserves_cancellation() -> None:
    async def cancelled(**_: object) -> object:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await OpenAiLanguageModel(cancelled, "test").complete(selection())


@pytest.mark.anyio
async def test_openai_normalizes_provider_errors_and_allows_no_tools_presentation() -> None:
    async def unavailable(**_: object) -> object:
        raise RuntimeError("provider failed")

    with pytest.raises(LanguageModelUnavailable):
        await OpenAiLanguageModel(unavailable, "test").complete(selection())

    request = presentation_request(
        message="show",
        reference_date=date(2026, 8, 15),
        history=(),
        tool_results=(ToolResultView("execution", "tool", {"safe": True}),),
    )
    completion = FakeCompletion(
        Completion(
            (
                Choice(
                    Message(
                        '{"markdown":"Done","language":"en","presentation":'
                        '{"title":"Attendance","context":null}}'
                    )
                ),
            )
        )
    )
    assert await OpenAiLanguageModel(completion, "test").complete(request) == FinalResponse(
        "Done", "en", presentation=PresentationPlan("Attendance")
    )
    assert "tools" not in completion.requests[0]
    assert "parallel_tool_calls" not in completion.requests[0]
    assert "tool_choice" not in completion.requests[0]
