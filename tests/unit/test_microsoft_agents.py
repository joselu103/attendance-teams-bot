from dataclasses import dataclass, field

import pytest

from attendance_teams_bot.agent.contracts import BotResponse
from attendance_teams_bot.teams.microsoft_agents import route_authenticated_turn


@dataclass
class FakeActivity:
    type: str
    text: str | None


@dataclass
class FakeTurnContext:
    activity: FakeActivity
    sent_texts: list[str] = field(default_factory=list)

    async def send_activity(self, text: str) -> None:
        self.sent_texts.append(text)


@dataclass
class RecordingAsyncHandler:
    messages: list[str] = field(default_factory=list)

    async def handle(self, *, message: str) -> BotResponse:
        self.messages.append(message)
        return BotResponse(text="safe reply")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_authenticated_message_routes_trimmed_text_and_sends_the_reply() -> None:
    handler = RecordingAsyncHandler()
    context = FakeTurnContext(activity=FakeActivity(type="message", text="  Hello  "))

    await route_authenticated_turn(context=context, handler=handler)

    assert handler.messages == ["Hello"]
    assert context.sent_texts == ["safe reply"]
