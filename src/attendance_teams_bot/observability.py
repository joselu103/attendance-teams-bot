from __future__ import annotations

import logging
import re
import sys
from collections.abc import Awaitable, Callable, Generator, Mapping, MutableMapping
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Final
from uuid import UUID, uuid4

import structlog
from fastapi import FastAPI, Request
from starlette.responses import Response
from structlog.contextvars import bind_contextvars, reset_contextvars
from structlog.processors import CallsiteParameter, CallsiteParameterAdder

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
_LOGGER = structlog.get_logger(__name__)


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
    logger.log(logging.ERROR if event == "operation_failed" else logging.INFO, event, **fields)


_LIFECYCLE_METADATA_KEYS: Final = frozenset(
    {
        "message_present",
        "message_length",
        "tool_count",
        "tool_name",
        "argument_count",
        "outcome",
        "error_code",
    }
)


class OperationLifecycle:
    """Own one operation's safe telemetry from start through one terminal event."""

    def __init__(
        self,
        logger: structlog.BoundLogger,
        *,
        handler: str,
        operation: str,
        input_metadata: Mapping[str, object] | None = None,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self._logger = logger
        self._handler = handler
        self._operation = operation
        self._input_metadata = _lifecycle_metadata(input_metadata)
        self._clock = clock
        self._started_at: float | None = None
        self._step: str | None = None
        self._terminal = False

    def start(self, *, step: str) -> None:
        """Start the operation. An operation may be started exactly once."""
        if self._started_at is not None:
            raise RuntimeError("operation lifecycle has already started")
        self._started_at = self._clock()
        self._step = step
        self._emit("operation_started")

    def step_completed(
        self, *, step: str | None = None, input_metadata: Mapping[str, object] | None = None
    ) -> None:
        """Record completion of the active step, optionally advancing to ``step``."""
        self._require_active()
        if step is not None:
            self._step = step
        self._emit("operation_step_completed", input_metadata=input_metadata)

    def succeed(
        self, *, step: str | None = None, input_metadata: Mapping[str, object] | None = None
    ) -> None:
        self._terminal_event("operation_succeeded", step=step, input_metadata=input_metadata)

    def fail(
        self,
        error: BaseException | None = None,
        *,
        step: str | None = None,
        input_metadata: Mapping[str, object] | None = None,
    ) -> None:
        self._terminal_event(
            "operation_failed",
            step=step,
            error_type=type(error).__name__ if error is not None else None,
            input_metadata=input_metadata,
        )

    def cancel(
        self, *, step: str | None = None, input_metadata: Mapping[str, object] | None = None
    ) -> None:
        self._terminal_event("operation_cancelled", step=step, input_metadata=input_metadata)

    @property
    def terminal(self) -> bool:
        """Whether this lifecycle has emitted its sole terminal event."""
        return self._terminal

    def _terminal_event(
        self,
        event: str,
        *,
        step: str | None,
        error_type: str | None = None,
        input_metadata: Mapping[str, object] | None = None,
    ) -> None:
        self._require_active()
        if self._terminal:
            raise RuntimeError("operation lifecycle has already emitted a terminal event")
        if step is not None:
            self._step = step
        self._terminal = True
        self._emit(event, error_type=error_type, input_metadata=input_metadata)

    def _require_active(self) -> None:
        if self._started_at is None or self._step is None:
            raise RuntimeError("operation lifecycle has not started")

    def _emit(
        self,
        event: str,
        *,
        error_type: str | None = None,
        input_metadata: Mapping[str, object] | None = None,
    ) -> None:
        assert self._started_at is not None
        assert self._step is not None
        self._input_metadata.update(_lifecycle_metadata(input_metadata))
        operation_event(
            self._logger,
            event=event,
            handler=self._handler,
            operation=self._operation,
            step=self._step,
            duration_ms=None if event == "operation_started" else self._duration_ms(),
            error_type=error_type,
            input_metadata=self._input_metadata,
        )

    def _duration_ms(self) -> int:
        assert self._started_at is not None
        return max(0, round((self._clock() - self._started_at) * 1000))


def _lifecycle_metadata(metadata: Mapping[str, object] | None) -> dict[str, object]:
    if metadata is None:
        return {}
    return {key: value for key, value in metadata.items() if key in _LIFECYCLE_METADATA_KEYS}


def authentication_event(
    logger: structlog.BoundLogger,
    *,
    event: str,
    scheme: str,
    failure_reason: str | None = None,
    user_or_client_id: str = _UNAVAILABLE,
) -> None:
    """Emit authentication state without accepting unverified channel identity."""
    logger.log(
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
def correlation_scope(correlation_id: UUID | None = None) -> Generator[UUID]:
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
) -> Generator[None]:
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


def install_http_request_observability(app: FastAPI) -> None:
    """Emit scrubbed lifecycle events for every request handled by an HTTP app."""

    @app.middleware("http")
    async def observe_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = _incoming_correlation_id(request)
        route = request.url.path
        started_at = perf_counter()
        with correlation_scope(correlation_id) as trace_id:
            _LOGGER.log(
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
                _LOGGER.log(
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
                _LOGGER.log(
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
                _LOGGER.log(
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
