"""Stateless history context; signatures bind queries, never grant attendance authority."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import SecretStr

from attendance_teams_bot.agent.contracts import BotAttachment, ReplyLanguage

HISTORY_VERB = "attendance.history.next"
MAX_TOKEN_SIZE = 4096


@dataclass(frozen=True, slots=True)
class TeamsBinding:
    tenant: str
    user: str
    conversation: str

    def __post_init__(self) -> None:
        for value in (self.tenant, self.user, self.conversation):
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 256
                or any(ord(char) < 32 for char in value)
            ):
                raise ValueError("invalid Teams binding")


@dataclass(frozen=True, slots=True)
class HistoryContinuation:
    scope: Literal["self", "admin"]
    start_date: date
    end_date: date
    offset: int
    language: ReplyLanguage
    binding: TeamsBinding
    target: int | None = None

    def __post_init__(self) -> None:
        if (
            self.scope not in {"self", "admin"}
            or self.language not in {"en", "sl"}
            or type(self.start_date) is not date
            or type(self.end_date) is not date
            or self.end_date < self.start_date
            or type(self.offset) is not int
            or self.offset < 1
            or not isinstance(self.binding, TeamsBinding)
        ):
            raise ValueError("invalid history context")
        if self.scope == "self" and self.target is not None:
            raise ValueError("self history cannot carry a target")
        if self.scope == "admin" and (type(self.target) is not int or self.target < 1):
            raise ValueError("admin history requires a resolved target")


@dataclass(frozen=True, slots=True)
class ContinuationSigner:
    key: SecretStr = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.key.get_secret_value().encode()) < 32:
            raise ValueError("history signing key requires at least 32 bytes")

    def sign(self, query: HistoryContinuation) -> str:
        payload: dict[str, object] = {
            "v": 1,
            "scope": query.scope,
            "start": query.start_date.isoformat(),
            "end": query.end_date.isoformat(),
            "limit": 50,
            "offset": query.offset,
            "language": query.language,
            "tenant": query.binding.tenant,
            "user": query.binding.user,
            "conversation": query.binding.conversation,
        }
        if query.scope == "admin":
            payload["target"] = query.target
        body = _encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        signature = _encode(
            hmac.digest(self.key.get_secret_value().encode(), body.encode(), hashlib.sha256)
        )
        token = body + "." + signature
        if len(token) > MAX_TOKEN_SIZE:
            raise ValueError("history context too large")
        return token

    def verify(self, token: object, binding: TeamsBinding) -> HistoryContinuation:
        try:
            if not isinstance(token, str) or len(token) > MAX_TOKEN_SIZE:
                raise ValueError
            body, signature = token.split(".")
            raw_signature = _decode(signature)
            expected = hmac.digest(
                self.key.get_secret_value().encode(), body.encode(), hashlib.sha256
            )
            if not hmac.compare_digest(raw_signature, expected):
                raise ValueError
            payload = json.loads(_decode(body), object_pairs_hook=_unique_object)
            if not isinstance(payload, dict):
                raise ValueError
            scope = payload.get("scope")
            if scope not in ("self", "admin"):
                raise ValueError
            fields = {
                "v",
                "scope",
                "start",
                "end",
                "limit",
                "offset",
                "language",
                "tenant",
                "user",
                "conversation",
            }
            if scope == "admin":
                fields.add("target")
            if set(payload) != fields or type(payload["v"]) is not int or payload["v"] != 1:
                raise ValueError
            if type(payload["limit"]) is not int or payload["limit"] != 50:
                raise ValueError
            bound = TeamsBinding(payload["tenant"], payload["user"], payload["conversation"])
            if bound != binding:
                raise ValueError
            start, end = _date(payload["start"]), _date(payload["end"])
            return HistoryContinuation(
                scope,
                start,
                end,
                payload["offset"],
                payload["language"],
                bound,
                payload.get("target"),
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            UnicodeError,
            OverflowError,
            RecursionError,
        ) as error:
            raise ValueError("invalid history continuation") from error

    def card(self, query: HistoryContinuation) -> BotAttachment:
        return BotAttachment(
            "application/vnd.microsoft.card.adaptive",
            {
                "type": "AdaptiveCard",
                "version": "1.4",
                "actions": [
                    {
                        "type": "Action.Submit",
                        "title": "Naslednja stran" if query.language == "sl" else "Next page",
                        "data": {"verb": HISTORY_VERB, "continuation": self.sign(query)},
                    }
                ],
            },
        )


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _encode(decoded) != value:
        raise ValueError
    return decoded


def _date(value: object) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError
    return date.fromisoformat(value)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError
        result[name] = value
    return result
