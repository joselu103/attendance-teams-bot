from typing import Protocol


class IdentityProvider(Protocol):
    def authenticate(self, user_id: str) -> str: ...
