from datetime import date
from uuid import uuid4

from attendance_teams_bot.agent.continuation import (
    HISTORY_VERB,
    HistoryContinuation,
    continuation_card,
    is_typed_continuation,
)


def test_continuation_card_carries_only_an_opaque_identifier() -> None:
    identifier = uuid4()

    card = continuation_card(identifier, "en")

    assert card.content["actions"] == [
        {
            "type": "Action.Submit",
            "title": "Next page",
            "data": {"verb": HISTORY_VERB, "continuation_id": str(identifier)},
        }
    ]
    assert "offset" not in repr(card.content)
    assert "start_date" not in repr(card.content)


def test_typed_continuations_cover_english_and_slovenian() -> None:
    assert is_typed_continuation("Continue")
    assert is_typed_continuation("next page!")
    assert is_typed_continuation("Nadaljuj")
    assert is_typed_continuation("naslednja stran")
    assert not is_typed_continuation("show last week")


def test_history_continuation_rejects_invalid_admin_target() -> None:
    try:
        HistoryContinuation("admin", date(2026, 1, 1), date(2026, 1, 2), 50, "en")
    except ValueError as error:
        assert str(error) == "admin history requires a resolved target"
    else:
        raise AssertionError("an administrator continuation needs a target")
