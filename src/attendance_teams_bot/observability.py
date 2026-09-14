from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from typing import Any, Final, cast
from uuid import UUID, uuid4

import structlog
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
