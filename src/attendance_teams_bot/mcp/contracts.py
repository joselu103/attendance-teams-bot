from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ATTENDANCE_MCP_CONTRACT_MAJOR = "1"
ATTENDANCE_MCP_CONTRACT_HEADER = "X-Attendance-MCP-Contract-Version"
CORRELATION_ID_HEADER = "X-Correlation-ID"
SELF_ATTENDANCE_TOOL = "list_my_attendance_events"
RESOLVE_EMPLOYEE_TOOL = "resolve_employee"
OTHER_ATTENDANCE_TOOL = "list_attendance_events"
CURRENT_ATTENDANCE_TOOL = "get_current_attendance"
CURRENT_WORK_STATUS_TOOL = "get_current_work_status"
SEARCH_EMPLOYEES_TOOL = "search_employees"


class ListMyAttendanceArguments(BaseModel):
    """The only model-controlled values in the requester attendance contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.start_date > self.end_date:
            raise ValueError("end date precedes start date")
        return self


class AttendanceEvent(BaseModel):
    """Represent one validated attendance event returned by the MCP authority."""

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
    """Represent one offset-based page returned by the requester attendance tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[AttendanceEvent, ...]
    limit: int = Field(strict=True, ge=1, le=100)
    offset: int = Field(strict=True, ge=0)
    next_offset: int | None = Field(strict=True, ge=0)


class ResolvedEmployee(BaseModel):
    """Directory-safe employee record used only to make the next MCP call."""

    model_config = ConfigDict(frozen=True)

    employee_id: int
    username: str | None = None
    email: str | None = None


class CurrentAttendancePage(BaseModel):
    """An intentionally permissive, typed envelope for current-status results."""

    model_config = ConfigDict(frozen=True, extra="allow")

    items: tuple[dict[str, object], ...]
    limit: int
    offset: int
    next_offset: int | None


class CurrentWorkStatusItem(BaseModel):
    """The pilot category-only REST projection for one workforce member."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    first_name: str
    last_name: str
    status: Literal["office", "remote", "customer_site", "break", "absence", "no_status"]


class CurrentWorkStatusPage(BaseModel):
    """A validated page from the category-only MCP tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[CurrentWorkStatusItem, ...]
    limit: int
    offset: int
    next_offset: int | None


class EmployeeSuggestion(BaseModel):
    """A directory-safe candidate that must be explicitly selected in a later message."""

    model_config = ConfigDict(frozen=True)

    display_name: str
    username: str | None = None
    email: str | None = None


class EmployeeSuggestionPage(BaseModel):
    """Bounded directory-safe suggestions returned for a name query."""

    model_config = ConfigDict(frozen=True)

    items: tuple[EmployeeSuggestion, ...]


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
    "NOT_FOUND",
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
    "NOT_FOUND": "No matching employee was found.",
}


class McpToolFailure(BaseModel):
    """Represent an MCP failure only when its code and safe user message agree."""

    model_config = ConfigDict(frozen=True)

    code: McpToolErrorCode
    message: str

    @model_validator(mode="after")
    def require_safe_message_for_code(self) -> Self:
        if self.message != MCP_TOOL_ERROR_MESSAGES[self.code]:
            raise ValueError("tool error code requires its configured safe message")
        return self
