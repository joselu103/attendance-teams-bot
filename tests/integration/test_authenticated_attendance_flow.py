from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from attendance_teams_bot.agent.rule_based import RuleBasedIntentSelector
from attendance_teams_bot.application import create_application
from attendance_teams_bot.auth.obo import DelegatedAuthenticationUnavailable
from attendance_teams_bot.mcp.contracts import AttendanceEvent, AttendanceEventPage
from attendance_teams_bot.teams.authenticated import AttendanceApplicationHandler
from attendance_teams_bot.teams.microsoft_agents import route_attendance_turn


@dataclass
class FakeActivity:
    type: str
    text: str | None


@dataclass
class FakeTurnContext:
    activity: FakeActivity
    sent_texts: list[str] = field(default_factory=list)

    async def send_activity(self, text: str) -> None:
        self.sent_texts.append(text)


@dataclass
class FakeSsoTokenProvider:
    token: SecretStr = field(default_factory=lambda: SecretStr("token-a"))

    async def get_token(self, context: object) -> SecretStr:
        del context
        return self.token


@dataclass
class RecordingOboTokenExchange:
    assertions: list[SecretStr] = field(default_factory=list)

    async def exchange(self, user_assertion: SecretStr) -> SecretStr:
        self.assertions.append(user_assertion)
        return SecretStr("token-b")


@dataclass
class RecordingMcpClient:
    tokens: list[SecretStr] = field(default_factory=list)
    correlation_ids: list[UUID] = field(default_factory=list)
    dates: list[tuple[date, date]] = field(default_factory=list)

    async def list_my_attendance_events(
        self,
        *,
        access_token: SecretStr,
        correlation_id: UUID,
        start_date: date,
        end_date: date,
    ) -> AttendanceEventPage:
        self.tokens.append(access_token)
        self.correlation_ids.append(correlation_id)
        self.dates.append((start_date, end_date))
        return AttendanceEventPage(
            items=(
                AttendanceEvent(
                    attendance_event_id=13,
                    employee_id=7,
                    punch_type="Office",
                    location="Company",
                    checked_in_at=datetime(2026, 8, 10, 8),
                    checked_out_at=datetime(2026, 8, 10, 16),
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
async def test_authenticated_attendance_flow_exchanges_teams_token_before_mcp() -> None:
    mcp_client = RecordingMcpClient()
    obo = RecordingOboTokenExchange()
    handler = AttendanceApplicationHandler(
        create_application(
            intent_selector=RuleBasedIntentSelector(),
            mcp_client=mcp_client,
            correlation_id_factory=uuid4,
        )
    )
    context = FakeTurnContext(
        activity=FakeActivity(
            type="message",
            text="Show my attendance from 2026-08-10 to 2026-08-12",
        )
    )

    await route_attendance_turn(
        context=context,
        handler=handler,
        sso_token_provider=FakeSsoTokenProvider(),
        obo_token_exchange=obo,
    )

    assert obo.assertions == [SecretStr("token-a")]
    assert mcp_client.tokens == [SecretStr("token-b")]
    assert mcp_client.dates == [(date(2026, 8, 10), date(2026, 8, 12))]
    assert len(mcp_client.correlation_ids) == 1
    assert "token-a" not in context.sent_texts[0]
    assert "token-b" not in context.sent_texts[0]
    assert "13" not in context.sent_texts[0]
    assert "7" not in context.sent_texts[0]


@dataclass
class FailingOboTokenExchange:
    async def exchange(self, user_assertion: SecretStr) -> SecretStr:
        del user_assertion
        raise DelegatedAuthenticationUnavailable


@pytest.mark.anyio
async def test_obo_failure_returns_a_safe_authentication_reply() -> None:
    context = FakeTurnContext(activity=FakeActivity(type="message", text="Show my attendance"))

    await route_attendance_turn(
        context=context,
        handler=AttendanceApplicationHandler(
            create_application(
                intent_selector=RuleBasedIntentSelector(),
                mcp_client=RecordingMcpClient(),
            )
        ),
        sso_token_provider=FakeSsoTokenProvider(),
        obo_token_exchange=FailingOboTokenExchange(),
    )

    assert context.sent_texts == [
        "Authentication is temporarily unavailable. Please try again later."
    ]
