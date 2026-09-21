from datetime import date
from typing import Protocol
from uuid import UUID

from pydantic import SecretStr

from attendance_teams_bot.mcp.contracts import AttendanceEventPage, McpToolFailure


class McpContractIncompatible(Exception):
    """The remote server does not implement the supported MCP contract major."""


class AttendanceToolFailure(Exception):
    """Preserve a validated MCP tool failure for safe presentation by the caller."""

    def __init__(self, failure: McpToolFailure) -> None:
        self.failure = failure
        super().__init__(failure.code)


class AttendanceMcpUnavailable(Exception):
    """The remote MCP server could not provide a valid attendance response."""


class AttendanceMcpClient(Protocol):
    """Legacy requester-attendance client port retained for transport-neutral callers."""

    async def list_my_attendance_events(
        self,
        *,
        access_token: SecretStr,
        correlation_id: UUID,
        start_date: date,
        end_date: date,
    ) -> AttendanceEventPage: ...
