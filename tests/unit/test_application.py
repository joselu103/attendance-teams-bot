from __future__ import annotations

from datetime import date

from attendance_teams_bot.main import create_application


class FakeIdentityProvider:
    def authenticate(self, user_id: str) -> str:
        assert user_id == "teams-user-123"
        return "verified-user-token"


class FakeMcpClient:
    def list_my_attendance_events(
        self,
        *,
        access_token: str,
        start_date: date,
        end_date: date,
    ) -> tuple[str, ...]:
        raise AssertionError("The MCP client must not be called for a greeting")


class FakeLanguageModel:
    def select_intent(self, message: str) -> str:
        raise AssertionError("The LLM must not be called for a greeting")


def test_application_returns_a_safe_greeting_without_calling_external_adapters() -> None:
    application = create_application(
        identity_provider=FakeIdentityProvider(),
        mcp_client=FakeMcpClient(),
        language_model=FakeLanguageModel(),
    )

    response = application.handle(
        user_id="teams-user-123",
        message="Hello",
    )

    assert response.text == "Hello. Ask me about your attendance for a specific date range."
    assert response.request is None
