from __future__ import annotations

import asyncio
import json
from io import StringIO
from uuid import UUID, uuid4

import pytest

import attendance_teams_bot.observability as observability
from attendance_teams_bot.observability import (
    correlation_scope,
    current_correlation_id,
    get_logger,
    log_event,
    trusted_context_scope,
)


def _configure_json(monkeypatch: pytest.MonkeyPatch) -> StringIO:
    output = StringIO()
    monkeypatch.setattr(observability.sys, "stdout", output)
    observability.configure_logging(environment="production")
    return output


def test_production_logs_are_single_line_json_with_required_metadata(monkeypatch) -> None:
    output = _configure_json(monkeypatch)

    log_event(get_logger("test"), 20, "completed", status_code=200)

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

    log_event(get_logger("test"), 10, "debug_event")

    rendered = output.getvalue()
    assert "\x1b[" in rendered
    assert "debug_event" in rendered


def test_redacts_nested_values_and_exception_text(monkeypatch) -> None:
    output = _configure_json(monkeypatch)

    try:
        raise RuntimeError("authorization: Bearer real-token, access_token: downstream-secret")
    except RuntimeError:
        log_event(
            get_logger("test"),
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
                log_event(get_logger("test"), 20, "context_bound")
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

    log_event(get_logger("test"), 10, "not_emitted")

    assert output.getvalue() == ""
