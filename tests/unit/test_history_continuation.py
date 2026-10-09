from datetime import date

import pytest
from pydantic import SecretStr

from attendance_teams_bot.agent.continuation import (
    ContinuationSigner,
    HistoryContinuation,
    TeamsBinding,
)


def test_signed_context_survives_restart_and_binds_to_authenticated_caller() -> None:
    binding = TeamsBinding("tenant", "aad-user", "conversation")
    query = HistoryContinuation(
        scope="self",
        start_date=date(2020, 1, 1),
        end_date=date(2030, 12, 31),
        offset=50,
        language="sl",
        binding=binding,
    )
    key = SecretStr("synthetic-test-signing-key-32-bytes")
    token = ContinuationSigner(key).sign(query)
    assert ContinuationSigner(key).verify(token, binding) == query
    with pytest.raises(ValueError):
        ContinuationSigner(key).verify(token, TeamsBinding("tenant", "other-user", "conversation"))
    with pytest.raises(ValueError):
        ContinuationSigner(SecretStr("replacement-synthetic-key-32-bytes")).verify(token, binding)


@pytest.mark.parametrize(
    "change",
    [
        {"v": 2},
        {"v": True},
        {"v": "1"},
        {"unexpected": "field"},
        {"scope": "admin"},
        {"scope": "other"},
        {"scope": []},
        {"target": 7},
        {"start": "20260101"},
        {"end": "2019-12-31"},
        {"start": None},
        {"limit": 100},
        {"limit": "50"},
        {"limit": True},
        {"offset": 0},
        {"offset": -1},
        {"offset": True},
        {"offset": "50"},
        {"offset": 50.0},
        {"language": "de"},
        {"language": []},
        {"tenant": "another-tenant"},
        {"user": "other-user"},
        {"conversation": "other-chat"},
        {"conversation": ""},
    ],
)
def test_even_correctly_signed_invalid_payloads_fail_closed(change) -> None:
    import base64
    import hmac
    import json

    payload = {
        "v": 1,
        "scope": "self",
        "start": "2020-01-01",
        "end": "2030-12-31",
        "limit": 50,
        "offset": 50,
        "language": "en",
        "tenant": "tenant",
        "user": "user",
        "conversation": "chat",
        **change,
    }
    key = "synthetic-test-signing-key-32-bytes"
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signature = (
        base64.urlsafe_b64encode(hmac.digest(key.encode(), body.encode(), "sha256"))
        .decode()
        .rstrip("=")
    )
    with pytest.raises(ValueError, match="invalid history continuation"):
        ContinuationSigner(SecretStr(key)).verify(
            body + "." + signature, TeamsBinding("tenant", "user", "chat")
        )


@pytest.mark.parametrize(
    "token", [None, {}, "", "x" * 4097, "abc", "abc.def.ghi", "abc.%%%%", "=.abc", "abc.A"]
)
def test_malformed_tokens_are_safe(token) -> None:
    with pytest.raises(ValueError):
        ContinuationSigner(SecretStr("synthetic-test-signing-key-32-bytes")).verify(
            token, TeamsBinding("tenant", "user", "chat")
        )


def test_admin_target_is_internal_signed_and_tampering_is_rejected() -> None:
    from dataclasses import replace

    signer = ContinuationSigner(SecretStr("synthetic-test-signing-key-32-bytes"))
    binding = TeamsBinding("tenant", "user", "chat")
    query = HistoryContinuation("admin", date(2020, 1, 1), date(2030, 12, 31), 50, "en", binding, 7)
    token = signer.sign(query)
    assert signer.verify(token, binding).target == 7
    with pytest.raises(ValueError):
        signer.verify(token[:-1] + ("A" if token[-1] != "A" else "B"), binding)
    for target in (None, True, "7", -1):
        with pytest.raises(ValueError):
            replace(query, target=target)
