from dataclasses import dataclass, field
from datetime import date
from typing import cast

import pytest

from attendance_teams_bot.agent.language_model import (
    LanguageModelUnavailable,
    ModelRequest,
    NoTool,
    ToolCall,
    ToolDefinition,
)
from attendance_teams_bot.agent.openai import OpenAiLanguageModel


@dataclass(frozen=True, slots=True)
class FakeFunction:
    name: str | None
    arguments: str | None


@dataclass(frozen=True, slots=True)
class FakeToolCall:
    id: str | None
    function: FakeFunction | None


@dataclass(frozen=True, slots=True)
class FakeMessage:
    content: str | None = None
    tool_calls: tuple[FakeToolCall, ...] | None = None


@dataclass(frozen=True, slots=True)
class FakeChoice:
    message: FakeMessage | None


@dataclass(frozen=True, slots=True)
class FakeCompletion:
    choices: tuple[FakeChoice, ...]


@dataclass
class FakeCompletionCallable:
    response: object | Exception
    requests: list[dict[str, object]] = field(default_factory=list)

    async def __call__(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def request() -> ModelRequest:
    return ModelRequest(
        user_message="How was my attendance last week?",
        reference_date=date(2026, 8, 15),
        timezone="Europe/Ljubljana",
        tools=(
            ToolDefinition(
                name="list_my_attendance_events",
                description="Bot-owned description.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "format": "date"},
                        "end_date": {"type": "string", "format": "date"},
                    },
                    "required": ["start_date", "end_date"],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def completion_with_call(*calls: FakeToolCall) -> FakeCompletion:
    return FakeCompletion(choices=(FakeChoice(FakeMessage(tool_calls=calls)),))


@pytest.mark.anyio
async def test_openai_adapter_translates_one_tool_call_with_date_context() -> None:
    complete = FakeCompletionCallable(
        completion_with_call(
            FakeToolCall(
                id="call-123",
                function=FakeFunction(
                    name="list_my_attendance_events",
                    arguments='{"start_date":"2026-08-10","end_date":"2026-08-12"}',
                ),
            )
        )
    )

    turn = await OpenAiLanguageModel(completion=complete, model="test-model").complete(request())

    assert turn == ToolCall(
        id="call-123",
        name="list_my_attendance_events",
        arguments={"start_date": "2026-08-10", "end_date": "2026-08-12"},
    )
    assert complete.requests[0]["parallel_tool_calls"] is False
    assert (
        "Europe/Ljubljana"
        in cast(list[dict[str, str]], complete.requests[0]["messages"])[0]["content"]
    )
    assert (
        "2026-08-15" in cast(list[dict[str, str]], complete.requests[0]["messages"])[0]["content"]
    )


@pytest.mark.anyio
async def test_openai_adapter_converts_plain_content_to_no_tool() -> None:
    model = OpenAiLanguageModel(
        completion=FakeCompletionCallable(
            FakeCompletion(choices=(FakeChoice(FakeMessage(content="Worked.")),))
        ),
        model="test-model",
    )

    assert await model.complete(request()) == NoTool()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        FakeCompletion(choices=()),
        FakeCompletion(choices=(FakeChoice(None),)),
        completion_with_call(
            FakeToolCall(id="one", function=FakeFunction("x", "{}")),
            FakeToolCall(id="two", function=FakeFunction("x", "{}")),
        ),
        completion_with_call(FakeToolCall(id=" ", function=FakeFunction("x", "{}"))),
        completion_with_call(FakeToolCall(id="one", function=FakeFunction(" ", "{}"))),
        completion_with_call(FakeToolCall(id="one", function=FakeFunction("x", "not-json"))),
        completion_with_call(FakeToolCall(id="one", function=FakeFunction("x", "[]"))),
    ],
)
async def test_openai_adapter_rejects_malformed_or_parallel_completion(response: object) -> None:
    model = OpenAiLanguageModel(completion=FakeCompletionCallable(response), model="test-model")

    with pytest.raises(LanguageModelUnavailable):
        await model.complete(request())


@pytest.mark.anyio
async def test_openai_adapter_hides_provider_failure() -> None:
    model = OpenAiLanguageModel(
        completion=FakeCompletionCallable(RuntimeError("key=provider-secret")), model="test-model"
    )

    with pytest.raises(LanguageModelUnavailable) as captured:
        await model.complete(request())

    assert "provider-secret" not in str(captured.value)
