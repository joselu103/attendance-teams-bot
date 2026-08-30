from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


class ConfidentialClientApplication(Protocol):
    def acquire_token_on_behalf_of(
        self,
        user_assertion: str,
        scopes: list[str],
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class MsalConfidentialAccessTokenProvider:
    """Async adapter over MSAL's synchronous OBO API."""

    confidential_client: ConfidentialClientApplication

    async def acquire_token_on_behalf_of(self, scopes: list[str], user_assertion: str) -> str:
        result = await asyncio.to_thread(
            self.confidential_client.acquire_token_on_behalf_of,
            user_assertion,
            scopes,
        )
        access_token = result.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise RuntimeError("MSAL did not return a delegated access token")
        return access_token
