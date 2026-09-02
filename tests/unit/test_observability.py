from __future__ import annotations

import json
import logging
from uuid import UUID, uuid4

import pytest

from attendance_teams_bot.observability import (
    JsonLogFormatter,
    correlation_scope,
    current_correlation_id,
    log_event,
)


def test_json_formatter_emits_only_the_event_and_explicit_safe_fields() -> None:
    logger = logging.getLogger("test.observability")
    record = logger.makeRecord(
        logger.name,
        logging.INFO,
        __file__,
        1,
        "teams_callback_completed",
        (),
        None,
        extra={"event_fields": {"correlation_id": "request-123", "status_code": 401}},
    )
    record.untrusted_detail = "must-not-be-serialized"  # type: ignore[attr-defined]

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["event"] == "teams_callback_completed"
    assert payload["correlation_id"] == "request-123"
    assert payload["status_code"] == 401
    assert "timestamp" in payload
    assert "untrusted_detail" not in payload


def test_correlation_scope_exposes_the_supplied_id_and_resets_after_exit() -> None:
    correlation_id = uuid4()

    with correlation_scope(correlation_id) as active_id:
        assert active_id == correlation_id
        assert current_correlation_id() == correlation_id

    assert current_correlation_id() != correlation_id


@pytest.mark.anyio
async def test_correlation_scopes_are_isolated_across_concurrent_tasks() -> None:
    import anyio

    observed_ids: list[UUID] = []

    async def record_id() -> None:
        with correlation_scope() as correlation_id:
            await anyio.sleep(0)
            assert current_correlation_id() == correlation_id
            observed_ids.append(correlation_id)

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(record_id)
        task_group.start_soon(record_id)

    assert len(observed_ids) == 2
    assert observed_ids[0] != observed_ids[1]


def test_log_event_supplies_only_explicit_fields(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("attendance_teams_bot.test")

    with caplog.at_level(logging.INFO, logger=logger.name):
        log_event(logger, logging.INFO, "attendance_turn_completed", stage="mcp_open")

    assert caplog.records[-1].message == "attendance_turn_completed"
    assert caplog.records[-1].event_fields == {"stage": "mcp_open"}
