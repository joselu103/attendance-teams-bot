from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import structlog
from pydantic import SecretStr

from attendance_teams_bot.observability import authentication_event

_LOGGER = structlog.get_logger(__name__)


class AccessTokenProvider(Protocol):
    async def acquire_token_on_behalf_of(self, scopes: list[str], user_assertion: str) -> str: ...


class OboTokenExchange(Protocol):
    async def exchange(self, user_assertion: SecretStr) -> SecretStr: ...


class DelegatedAuthenticationUnavailable(Exception):
    """The bot could not obtain a downstream delegated access token."""


@dataclass(frozen=True, slots=True)
class MsalOboTokenExchange:
    """Exchange a Teams SSO assertion for the configured downstream delegated scope."""

    provider: AccessTokenProvider
    delegated_scope: str

    async def exchange(self, user_assertion: SecretStr) -> SecretStr:
        """Return a non-empty delegated token or raise the neutral authentication failure."""
        try:
            token = await self.provider.acquire_token_on_behalf_of(
                [self.delegated_scope],
                user_assertion.get_secret_value(),
            )
        except Exception as error:
            authentication_event(
                _LOGGER,
                event="auth_failed",
                scheme="delegated_obo",
                failure_reason=type(error).__name__,
            )
            raise DelegatedAuthenticationUnavailable from error
        if not token:
            authentication_event(
                _LOGGER,
                event="auth_failed",
                scheme="delegated_obo",
                failure_reason="empty_token",
            )
            raise DelegatedAuthenticationUnavailable
        authentication_event(_LOGGER, event="auth_validated", scheme="delegated_obo")
        return SecretStr(token)
