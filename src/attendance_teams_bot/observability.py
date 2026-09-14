from __future__ import annotations

import logging
import re
import sys
from collections.abc import Awaitable, Callable, Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Final, cast
from uuid import UUID, uuid4

import structlog
from fastapi import FastAPI, Request
from starlette.responses import Response
from structlog.contextvars import bind_contextvars, reset_contextvars
from structlog.processors import CallsiteParameter, CallsiteParameterAdder

_LOGGER_NAME: Final = "attendance_teams_bot"
_REDACTED: Final = "[REDACTED]"
_UNAVAILABLE: Final = "unavailable"
_SENSITIVE_KEYS: Final = frozenset(
    {
        "authorization",
        "password",
        "secret",
        "api_key",
        "token",
        "bearer",
        "access_token",
        "client_secret",
    }
)
_TEXTUAL_SECRET: Final = re.compile(
    r"(?i)(?P<key>authorization|password|secret|api_key|token|bearer|access_token|client_secret)"
    r"(?:['\"])?(?P<separator>\s*(?:=|:)\s*)(?P<value>[^,\n;]+)"
)
_CORRELATION_ID_HEADER: Final = "X-Correlation-ID"


def _redact_text(value: str) -> str:
    return _TEXTUAL_SECRET.sub(
        lambda match: f"{match.group('key')}{match.group('separator')}{_REDACTED}", value
    )


def redact_sensitive_values(
    _: structlog.types.WrappedLogger, __: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Remove secret-bearing fields before either configured renderer sees them."""

    def redact(value: object, *, key: str | None = None) -> object:
        if key is not None and key.lower() in _SENSITIVE_KEYS:
            return _REDACTED
        if isinstance(value, Mapping):
            return {
                str(child_key): redact(child_value, key=str(child_key))
                for child_key, child_value in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(redact(item) for item in value)
        if isinstance(value, str):
            return _redact_text(value)
        return value

    return {key: redact(value, key=key) for key, value in event_dict.items()}


def _ensure_trace_id(
    _: structlog.types.WrappedLogger, __: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    event_dict.setdefault("trace_id", str(uuid4()))
    event_dict.setdefault("user_or_client_id", _UNAVAILABLE)
    return event_dict


def configure_logging(*, environment: str) -> None:
    """Configure safe structured logs for the requested deployment environment."""
    if environment not in {"local", "staging", "production"}:
        raise ValueError("LOG_ENV must be one of: local, staging, production")

    renderer: structlog.types.Processor
    minimum_level: int
    if environment == "local":
        renderer = structlog.dev.ConsoleRenderer(colors=True)
        minimum_level = logging.DEBUG
    else:
        renderer = structlog.processors.JSONRenderer()
        minimum_level = logging.INFO

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _ensure_trace_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            CallsiteParameterAdder(
                parameters={
                    CallsiteParameter.FILENAME,
                    CallsiteParameter.FUNC_NAME,
                    CallsiteParameter.LINENO,
                },
                additional_ignores=[__name__],
            ),
            structlog.processors.format_exc_info,
            redact_sensitive_values,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(minimum_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    return cast(structlog.BoundLogger, structlog.get_logger(f"{_LOGGER_NAME}.{name}"))


def log_event(logger: structlog.BoundLogger, level: int, event: str, **fields: object) -> None:
    logger.log(level, event, **fields)


def operation_event(
    logger: structlog.BoundLogger,
    *,
    event: str,
    handler: str,
    operation: str,
    step: str,
    duration_ms: int | None = None,
    error_type: str | None = None,
    input_metadata: Mapping[str, object] | None = None,
    correlation_id: UUID | str | None = None,
) -> None:
    """Emit one safe, uniformly shaped operation lifecycle event.

    Callers must supply metadata that describes input shape only (never input values).
    """
    fields: dict[str, object] = {
        "handler": handler,
        "operation": operation,
        "step": step,
        "input_metadata": dict(input_metadata or {}),
    }
    if duration_ms is not None:
        fields["duration_ms"] = duration_ms
    if error_type is not None:
        fields["error_type"] = error_type
    if correlation_id is not None:
        fields["correlation_id"] = str(correlation_id)
    log_event(
        logger,
        logging.ERROR if event == "operation_failed" else logging.INFO,
        event,
        **fields,
    )


def authentication_event(
    logger: structlog.BoundLogger,
    *,
    event: str,
    scheme: str,
    failure_reason: str | None = None,
    user_or_client_id: str = _UNAVAILABLE,
) -> None:
    """Emit authentication state without accepting unverified channel identity."""
    log_event(
        logger,
        logging.WARNING if event != "auth_validated" else logging.INFO,
        event,
        scheme=scheme,
        failure_reason=failure_reason,
        user_or_client_id=user_or_client_id,
    )


def message_input_metadata(message: str) -> dict[str, object]:
    """Describe a message without retaining any part of its content."""
    return {"message_present": bool(message), "message_length": len(message)}


@contextmanager
def correlation_scope(correlation_id: UUID | None = None) -> Iterator[UUID]:
    """Bind a generated or trusted incoming trace ID to the current async context."""
    active_id = correlation_id or uuid4()
    tokens = bind_contextvars(trace_id=str(active_id), user_or_client_id=_UNAVAILABLE)
    try:
        yield active_id
    finally:
        reset_contextvars(**tokens)


@contextmanager
def trusted_context_scope(
    *, user_or_client_id: str = _UNAVAILABLE, session_id: str | None = None
) -> Iterator[None]:
    """Bind context only after a caller has authenticated and validated it."""
    values: dict[str, object] = {"user_or_client_id": user_or_client_id}
    if session_id is not None:
        values["session_id"] = session_id
    tokens = bind_contextvars(**values)
    try:
        yield
    finally:
        reset_contextvars(**tokens)


def current_correlation_id() -> UUID:
    context = structlog.contextvars.get_contextvars()
    trace_id = context.get("trace_id")
    if isinstance(trace_id, str):
        try:
            return UUID(trace_id)
        except ValueError:
            pass
    return uuid4()


def install_http_request_observability(app: FastAPI, *, logger_name: str) -> None:
    """Emit scrubbed lifecycle events for every request handled by an HTTP app."""
    logger = get_logger(logger_name)

    @app.middleware("http")
    async def observe_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = _incoming_correlation_id(request)
        route = request.url.path
        started_at = perf_counter()
        with correlation_scope(correlation_id) as trace_id:
            log_event(
                logger,
                logging.INFO,
                "request_received",
                trace_id=str(trace_id),
                route=route,
                method=request.method,
                state="received",
            )
            try:
                response = await call_next(request)
            except Exception as error:
                log_event(
                    logger,
                    logging.ERROR,
                    "request_failed",
                    trace_id=str(trace_id),
                    route=route,
                    method=request.method,
                    status_code=500,
                    state="server_error",
                    error_type=type(error).__name__,
                    duration_ms=_duration_ms(started_at),
                )
                raise

            if response.status_code < 400:
                log_event(
                    logger,
                    logging.INFO,
                    "request_completed",
                    trace_id=str(trace_id),
                    route=route,
                    method=request.method,
                    status_code=response.status_code,
                    state="completed",
                    duration_ms=_duration_ms(started_at),
                )
            else:
                status_state = "client_error" if response.status_code < 500 else "server_error"
                log_event(
                    logger,
                    logging.WARNING if response.status_code < 500 else logging.ERROR,
                    "request_failed",
                    trace_id=str(trace_id),
                    route=route,
                    method=request.method,
                    status_code=response.status_code,
                    state=status_state,
                    duration_ms=_duration_ms(started_at),
                )
            return response


def _incoming_correlation_id(request: Request) -> UUID | None:
    value = request.headers.get(_CORRELATION_ID_HEADER)
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _duration_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))
