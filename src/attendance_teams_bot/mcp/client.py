from datetime import date
from typing import Protocol


class AttendanceMcpClient(Protocol):
    def list_my_attendance_events(
        self,
        *,
        access_token: str,
        start_date: date,
        end_date: date,
    ) -> tuple[str, ...]: ...
