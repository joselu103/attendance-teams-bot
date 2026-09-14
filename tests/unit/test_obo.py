import pytest
from pydantic import SecretStr

from attendance_teams_bot.auth import obo
from attendance_teams_bot.auth.obo import (
    DelegatedAuthenticationUnavailable,
    MsalOboTokenExchange,
)


class FakeAccessTokenProvider:
    def __init__(self) -> None:
        self.scopes: list[str] | None = None
        self.user_assertion: str | None = None

    async def acquire_token_on_behalf_of(self, scopes: list[str], user_assertion: str) -> str:
        self.scopes = scopes
        self.user_assertion = user_assertion
        return "attendance-mcp-token"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_obo_exchange_returns_only_the_downstream_access_token() -> None:
    provider = FakeAccessTokenProvider()
    exchanger = MsalOboTokenExchange(
        provider=provider,
        delegated_scope="api://attendance-crmt/attendance.access",
    )

    token = await exchanger.exchange(SecretStr("teams-sso-token"))

    assert token.get_secret_value() == "attendance-mcp-token"
    assert provider.scopes == ["api://attendance-crmt/attendance.access"]
    assert provider.user_assertion == "teams-sso-token"


@pytest.mark.anyio
async def test_obo_exchange_hides_provider_failures() -> None:
    class FailingAccessTokenProvider:
        async def acquire_token_on_behalf_of(self, scopes: list[str], user_assertion: str) -> str:
            del scopes, user_assertion
            raise RuntimeError("token details must not reach the caller")

    exchanger = MsalOboTokenExchange(
        provider=FailingAccessTokenProvider(),
        delegated_scope="api://attendance-crmt/attendance.access",
    )

    with pytest.raises(DelegatedAuthenticationUnavailable) as error:
        await exchanger.exchange(SecretStr("teams-sso-token"))

    assert "token details" not in str(error.value)


@pytest.mark.anyio
async def test_obo_exchange_emits_safe_authentication_events(monkeypatch) -> None:
    events: list[dict[str, object]] = []

    def record_event(_logger, **fields: object) -> None:
        events.append(fields)

    monkeypatch.setattr(obo, "authentication_event", record_event)
    exchanger = MsalOboTokenExchange(
        provider=FakeAccessTokenProvider(),
        delegated_scope="api://attendance-crmt/attendance.access",
    )

    await exchanger.exchange(SecretStr("teams-sso-token"))

    assert events == [{"event": "auth_validated", "scheme": "delegated_obo"}]
    assert "teams-sso-token" not in repr(events)
