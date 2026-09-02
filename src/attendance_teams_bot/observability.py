from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Final
from uuid import UUID, uuid4

_LOGGER_NAME: Final = "attendance_teams_bot"
_correlation_id: ContextVar[UUID | None] = ContextVar("correlation_id", default=None)


class JsonLogFormatter(logging.Formatter):
    """Serialize only an event name and explicitly supplied safe fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        fields = getattr(record, "event_fields", None)
        if isinstance(fields, Mapping):
            payload.update(fields)
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def configure_logging(*, level: str) -> None:
    """Configure the application logger once for stdout JSON ingestion."""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    logger.addHandler(handler)
    logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")


def log_event(logger: logging.Logger, level: int, event: str, **fields: object) -> None:
    logger.log(level, event, extra={"event_fields": fields})


@contextmanager
def correlation_scope(correlation_id: UUID | None = None) -> Iterator[UUID]:
    active_id = correlation_id or uuid4()
    token = _correlation_id.set(active_id)
    try:
        yield active_id
    finally:
        _correlation_id.reset(token)


def current_correlation_id() -> UUID:
    return _correlation_id.get() or uuid4()
