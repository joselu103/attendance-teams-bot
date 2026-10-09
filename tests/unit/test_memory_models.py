from datetime import UTC, datetime

from attendance_teams_bot.memory.database import (
    MAX_CONTENT_LENGTH,
    MEMORY_SUFFIX,
    memory_session_id,
    truncate_for_memory,
)
from attendance_teams_bot.memory.models import ChatMessage


def test_chat_message_is_neutral_and_timestamped() -> None:
    message = ChatMessage("user", "and yesterday?", datetime(2026, 10, 9, tzinfo=UTC))

    assert message.role == "user"
    assert message.content == "and yesterday?"


def test_session_key_is_stable_opaque_and_requires_canonical_identifiers() -> None:
    key = memory_session_id(
        "4A9C3C7B-EB0A-4E92-8B4A-0A8F1C65C786", "63B649E6-17D4-4913-8B24-1A0B3C2DA274"
    )

    assert key == memory_session_id(
        "4a9c3c7b-eb0a-4e92-8b4a-0a8f1c65c786", "63b649e6-17d4-4913-8b24-1a0b3c2da274"
    )
    assert key is not None and len(key) == 64
    assert memory_session_id("not-an-id", "63b649e6-17d4-4913-8b24-1a0b3c2da274") is None


def test_memory_truncation_preserves_the_fixed_prefix_and_suffix() -> None:
    content = "x" * (MAX_CONTENT_LENGTH + 1)

    assert truncate_for_memory(content) == "x" * MAX_CONTENT_LENGTH + MEMORY_SUFFIX
