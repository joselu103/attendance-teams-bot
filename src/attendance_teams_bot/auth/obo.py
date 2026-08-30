from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr


class AccessTokenProvider(Protocol):
    async def acquire_token_on_behalf_of(self, scopes: list[str], user_assertion: str) -> str: ...


class OboTokenExchange(Protocol):
    async def exchange(self, user_assertion: SecretStr) -> SecretStr: ...


class DelegatedAuthenticationUnavailable(Exception):
    """The bot could not obtain a downstream delegated access token."""


@dataclass(frozen=True, slots=True)
class MsalOboTokenExchange:
    provider: AccessTokenProvider
    delegated_scope: str

    async def exchange(self, user_assertion: SecretStr) -> SecretStr:
        try:
            token = await self.provider.acquire_token_on_behalf_of(
                [self.delegated_scope],
                user_assertion.get_secret_value(),
            )
        except Exception as error:
            raise DelegatedAuthenticationUnavailable from error
        if not token:
            raise DelegatedAuthenticationUnavailable
        return SecretStr(token)
