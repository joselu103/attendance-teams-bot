from __future__ import annotations

import pytest

from attendance_teams_bot.agent.contracts import BotResponse
from attendance_teams_bot.teams.adapter import TeamsActivityAdapter


class RecordingHandler:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def handle(self, *, user_id: str, message: str) -> BotResponse:
        self.calls.append((user_id, message))
        return BotResponse(text="Your attendance summary is ready.")


def test_message_activity_routes_a_teams_user_and_text_to_the_application() -> None:
    handler = RecordingHandler()
    adapter = TeamsActivityAdapter(handler=handler)

    reply = adapter.handle_payload(
        {
            "type": "message",
            "id": "activity-123",
            "text": "  Show my attendance for yesterday  ",
            "from": {"id": "teams-user-123"},
            "conversation": {"id": "conversation-123"},
        }
    )

    assert handler.calls == [("teams-user-123", "Show my attendance for yesterday")]
    assert reply is not None
    assert reply.text == "Your attendance summary is ready."


def test_message_activity_trims_the_teams_user_identifier_before_routing() -> None:
    handler = RecordingHandler()
    adapter = TeamsActivityAdapter(handler=handler)

    reply = adapter.handle_payload(
        {
            "type": "message",
            "text": "Hello",
            "from": {"id": "  teams-user-123  "},
        }
    )

    assert handler.calls == [("teams-user-123", "Hello")]
    assert reply is not None


def test_non_message_activity_is_ignored_without_calling_the_application() -> None:
    handler = RecordingHandler()
    adapter = TeamsActivityAdapter(handler=handler)

    reply = adapter.handle_payload({"type": "conversationUpdate", "id": "activity-123"})

    assert handler.calls == []
    assert reply is None


def test_malformed_non_message_activity_is_ignored_without_calling_the_application() -> None:
    handler = RecordingHandler()
    adapter = TeamsActivityAdapter(handler=handler)

    reply = adapter.handle_payload({"type": "conversationUpdate", "from": "malformed"})

    assert handler.calls == []
    assert reply is None


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "message", "text": "Hello"},
        {"type": "message", "from": {"id": "teams-user-123"}},
    ],
)
def test_incomplete_message_returns_a_safe_reply_without_calling_the_application(
    payload: dict[str, object],
) -> None:
    handler = RecordingHandler()
    adapter = TeamsActivityAdapter(handler=handler)

    reply = adapter.handle_payload(payload)

    assert handler.calls == []
    assert reply is not None
    assert reply.text == "Please send a text message so I can help."
