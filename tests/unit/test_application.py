from datetime import date
from uuid import UUID

import pytest
from pydantic import SecretStr

from attendance_teams_bot.agent.contracts import ListMyAttendanceIntent
from attendance_teams_bot.application import create_application
from attendance_teams_bot.mcp.contracts import AttendanceEvent, AttendanceEventPage


class FakeIntentSelector:
    def select_intent(self, message: str) -> ListMyAttendanceIntent:
        assert message == "Show my attendance from 2026-08-10 to 2026-08-10"
        return ListMyAttendanceIntent(date(2026, 8, 10), date(2026, 8, 10))


class FakeMcpClient:
    async def list_my_attendance_events(
        self,
        *,
        access_token: SecretStr,
        correlation_id: UUID,
        start_date: date,
        end_date: date,
    ) -> AttendanceEventPage:
        assert access_token == SecretStr("mcp-token")
        assert correlation_id == UUID("11111111-1111-1111-1111-111111111111")
        assert start_date == end_date == date(2026, 8, 10)
        return AttendanceEventPage(
            items=(
                AttendanceEvent(
                    attendance_event_id=100,
                    employee_id=42,
                    punch_type="Remote work",
                    location="Home",
                    checked_in_at=None,
                    checked_out_at=None,
                    note=None,
                ),
            ),
            limit=50,
            offset=0,
            next_offset=None,
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_application_calls_the_requester_scoped_client_without_rendering_internal_ids() -> (
    None
):
    application = create_application(
        intent_selector=FakeIntentSelector(),
        mcp_client=FakeMcpClient(),
        correlation_id_factory=lambda: UUID("11111111-1111-1111-1111-111111111111"),
    )

    response = await application.handle(
        message="Show my attendance from 2026-08-10 to 2026-08-10",
        mcp_access_token=SecretStr("mcp-token"),
    )

    assert "Remote work" in response.text
    assert "Home" in response.text
    assert "42" not in response.text
    assert "100" not in response.text
