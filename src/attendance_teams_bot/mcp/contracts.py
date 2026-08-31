from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

ATTENDANCE_MCP_CONTRACT_MAJOR = "1"
ATTENDANCE_MCP_CONTRACT_HEADER = "X-Attendance-MCP-Contract-Version"
CORRELATION_ID_HEADER = "X-Correlation-ID"
SELF_ATTENDANCE_TOOL = "list_my_attendance_events"


class AttendanceEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    attendance_event_id: int
    employee_id: int
    punch_type: str | None
    location: str | None
    checked_in_at: datetime | None
    checked_out_at: datetime | None
    note: str | None

    @field_validator("checked_in_at", "checked_out_at")
    @classmethod
    def require_utc_offset(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("attendance timestamps require a UTC offset")
        return value


class AttendanceEventPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[AttendanceEvent, ...]
    limit: int
    offset: int
    next_offset: int | None


McpToolErrorCode = Literal[
    "AUTHENTICATION_REQUIRED",
    "TOKEN_INVALID",
    "CORRELATION_ID_INVALID",
    "INVALID_ARGUMENT",
    "IDENTITY_UNMAPPED",
    "IDENTITY_AMBIGUOUS",
    "FORBIDDEN",
    "BACKEND_UNAVAILABLE",
    "INTERNAL_ERROR",
]

MCP_TOOL_ERROR_MESSAGES: dict[McpToolErrorCode, str] = {
    "AUTHENTICATION_REQUIRED": "Please sign in to use Attendance.",
    "TOKEN_INVALID": "Your sign-in could not be verified. Please try again.",
    "CORRELATION_ID_INVALID": "The request correlation ID is missing or invalid.",
    "INVALID_ARGUMENT": "Check the attendance date range and pagination values and try again.",
    "IDENTITY_UNMAPPED": (
        "Your Teams account is not linked to an active attendance employee. "
        "Contact an administrator."
    ),
    "IDENTITY_AMBIGUOUS": "Your Teams account cannot be linked safely. Contact an administrator.",
    "FORBIDDEN": "You do not have permission to do that.",
    "BACKEND_UNAVAILABLE": "Attendance is temporarily unavailable. Please try again shortly.",
    "INTERNAL_ERROR": "Attendance could not complete that request.",
}


class McpToolFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: McpToolErrorCode
    message: str

    @model_validator(mode="after")
    def require_safe_message_for_code(self) -> Self:
        if self.message != MCP_TOOL_ERROR_MESSAGES[self.code]:
            raise ValueError("tool error code requires its configured safe message")
        return self
