from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

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


class AttendanceEventPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[AttendanceEvent, ...]
    limit: int
    offset: int
    next_offset: int | None


class McpToolFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
