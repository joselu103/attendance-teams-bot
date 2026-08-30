import pytest

from attendance_teams_bot.auth.msal_provider import MsalConfidentialAccessTokenProvider


class FakeConfidentialClient:
    def __init__(self, result: dict[str, str]) -> None:
        self.result = result
        self.user_assertion: str | None = None
        self.scopes: list[str] | None = None

    def acquire_token_on_behalf_of(self, user_assertion: str, scopes: list[str]) -> dict[str, str]:
        self.user_assertion = user_assertion
        self.scopes = scopes
        return self.result


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_msal_provider_extracts_only_access_token() -> None:
    confidential_client = FakeConfidentialClient({"access_token": "mcp-token"})
    provider = MsalConfidentialAccessTokenProvider(confidential_client)

    token = await provider.acquire_token_on_behalf_of(
        scopes=["api://attendance-crmt/attendance.access"],
        user_assertion="teams-token",
    )

    assert token == "mcp-token"
    assert confidential_client.user_assertion == "teams-token"
    assert confidential_client.scopes == ["api://attendance-crmt/attendance.access"]


@pytest.mark.anyio
async def test_msal_provider_does_not_return_raw_failure_payloads() -> None:
    provider = MsalConfidentialAccessTokenProvider(
        FakeConfidentialClient({"error_description": "secret"})
    )

    with pytest.raises(RuntimeError) as error:
        await provider.acquire_token_on_behalf_of(scopes=["scope"], user_assertion="teams-token")

    assert "secret" not in str(error.value)
