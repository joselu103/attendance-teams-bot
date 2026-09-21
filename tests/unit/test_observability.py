from __future__ import annotations

import asyncio
import json
from io import StringIO
from uuid import UUID, uuid4

import pytest
import structlog

import attendance_teams_bot.observability as observability
from attendance_teams_bot.observability import (
    OperationLifecycle,
    correlation_scope,
    current_correlation_id,
    trusted_context_scope,
)

_LOGGER = structlog.get_logger(__name__)


def _configure_json(monkeypatch: pytest.MonkeyPatch) -> StringIO:
    output = StringIO()
    monkeypatch.setattr(observability.sys, "stdout", output)
    observability.configure_logging(environment="production")
    return output


def test_production_logs_are_single_line_json_with_required_metadata(monkeypatch) -> None:
    output = _configure_json(monkeypatch)

    _LOGGER.log(20, "completed", status_code=200)

    payload = json.loads(output.getvalue())
    assert payload["event"] == "completed"
    assert payload["level"] == "info"
    assert payload["timestamp"].endswith("Z")
    assert isinstance(UUID(payload["trace_id"]), UUID)
    assert payload["user_or_client_id"] == "unavailable"
    assert payload["filename"] == "test_observability.py"
    assert (
        payload["func_name"] == "test_production_logs_are_single_line_json_with_required_metadata"
    )
    assert isinstance(payload["lineno"], int)


def test_local_uses_colored_console_and_debug_threshold(monkeypatch) -> None:
    output = StringIO()
    monkeypatch.setattr(observability.sys, "stdout", output)
    observability.configure_logging(environment="local")

    _LOGGER.log(10, "debug_event")

    rendered = output.getvalue()
    assert "\x1b[" in rendered
    assert "debug_event" in rendered


def test_redacts_nested_values_and_exception_text(monkeypatch) -> None:
    output = _configure_json(monkeypatch)

    try:
        raise RuntimeError("authorization: Bearer real-token, access_token: downstream-secret")
    except RuntimeError:
        _LOGGER.log(
            40,
            "request_failed",
            authorization="top-secret",
            nested={"password": "hidden", "items": [{"api_key": "also-hidden"}]},
            exc_info=True,
        )

    rendered = output.getvalue()
    payload = json.loads(rendered)
    assert payload["authorization"] == "[REDACTED]"
    assert payload["nested"]["password"] == "[REDACTED]"
    assert payload["nested"]["items"][0]["api_key"] == "[REDACTED]"
    assert "real-token" not in rendered
    assert "downstream-secret" not in rendered
    assert "RuntimeError" in payload["exception"]


def test_correlation_scope_binds_trace_id_and_resets_after_exit() -> None:
    correlation_id = uuid4()

    with correlation_scope(correlation_id) as active_id:
        assert active_id == correlation_id
        assert current_correlation_id() == correlation_id

    assert current_correlation_id() != correlation_id


@pytest.mark.anyio
async def test_context_scopes_are_isolated_across_concurrent_tasks(monkeypatch) -> None:
    output = _configure_json(monkeypatch)
    trace_ids: list[UUID] = []

    async def record_trace_id() -> None:
        with correlation_scope() as trace_id:
            await asyncio.sleep(0)
            with trusted_context_scope(user_or_client_id="validated-user", session_id="session-1"):
                _LOGGER.log(20, "context_bound")
            trace_ids.append(trace_id)

    await asyncio.gather(record_trace_id(), record_trace_id())

    payloads = [json.loads(line) for line in output.getvalue().splitlines()]
    assert {payload["trace_id"] for payload in payloads} == {
        str(trace_id) for trace_id in trace_ids
    }
    assert {payload["user_or_client_id"] for payload in payloads} == {"validated-user"}
    assert {payload["session_id"] for payload in payloads} == {"session-1"}


def test_staging_filters_debug_events(monkeypatch) -> None:
    output = _configure_json(monkeypatch)

    _LOGGER.log(10, "not_emitted")

    assert output.getvalue() == ""


def test_operation_lifecycle_emits_safe_steps_and_one_terminal_event(monkeypatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    clock = iter((10.0, 10.025, 10.075))

    def record_event(_logger, *, event: str, **fields: object) -> None:
        events.append((event, fields))

    monkeypatch.setattr(observability, "operation_event", record_event)
    lifecycle = OperationLifecycle(
        _LOGGER,
        handler="handler",
        operation="operation",
        input_metadata={"message_present": True, "message": "not allowed"},
        clock=lambda: next(clock),
    )

    lifecycle.start(step="request")
    lifecycle.step_completed(step="validated", input_metadata={"tool_count": 1})
    lifecycle.succeed(input_metadata={"outcome": "success", "token": "not allowed"})

    assert [event for event, _ in events] == [
        "operation_started",
        "operation_step_completed",
        "operation_succeeded",
    ]
    assert events[0][1]["duration_ms"] is None
    assert events[1][1]["duration_ms"] == 25
    assert events[2][1]["duration_ms"] == 75
    assert events[2][1]["step"] == "validated"
    assert events[2][1]["input_metadata"] == {
        "message_present": True,
        "tool_count": 1,
        "outcome": "success",
    }
    with pytest.raises(RuntimeError, match="terminal"):
        lifecycle.fail(RuntimeError())


def test_operation_lifecycle_requires_start_and_never_reports_negative_duration(
    monkeypatch,
) -> None:
    events: list[dict[str, object]] = []
    clock = iter((10.0, 9.0))

    def record_event(_logger, *, event: str, **fields: object) -> None:
        del event
        events.append(fields)

    monkeypatch.setattr(observability, "operation_event", record_event)
    lifecycle = OperationLifecycle(
        _LOGGER, handler="handler", operation="operation", clock=lambda: next(clock)
    )

    with pytest.raises(RuntimeError, match="not started"):
        lifecycle.step_completed()
    lifecycle.start(step="request")
    lifecycle.fail(ValueError())

    assert events[-1]["duration_ms"] == 0
    assert events[-1]["error_type"] == "ValueError"


def test_operation_lifecycle_cancellation_emits_before_reraising(monkeypatch) -> None:
    events: list[str] = []

    def record_event(_logger, *, event: str, **fields: object) -> None:
        del fields
        events.append(event)

    monkeypatch.setattr(observability, "operation_event", record_event)
    lifecycle = OperationLifecycle(_LOGGER, handler="handler", operation="operation")
    lifecycle.start(step="request")
    with pytest.raises(asyncio.CancelledError):
        try:
            raise asyncio.CancelledError
        except asyncio.CancelledError:
            lifecycle.cancel()
            assert events[-1] == "operation_cancelled"
            raise
