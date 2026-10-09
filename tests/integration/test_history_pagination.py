"""Public factory and actual pinned SDK dispatch with synthetic external boundaries."""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from microsoft_agents.activity import Activity, ResourceResponse
from microsoft_agents.hosting.core import AgentApplication, TurnContext
from pydantic import SecretStr
from structlog.testing import capture_logs

from attendance_teams_bot.agent.continuation import (
    HISTORY_VERB,
    ContinuationSigner,
    HistoryContinuation,
    TeamsBinding,
)
from attendance_teams_bot.agent.language_model import FinalResponse, PresentationPlan, ToolCall
from attendance_teams_bot.agent.mcp_catalog import DiscoveredMcpTool
from attendance_teams_bot.agent.orchestrator import AttendanceAgent
from attendance_teams_bot.mcp.client import AttendanceToolFailure
from attendance_teams_bot.mcp.contracts import (
    AttendanceEvent,
    AttendanceEventPage,
    McpToolFailure,
    ResolvedEmployee,
)
from attendance_teams_bot.settings import TeamsConnectionSettings
from attendance_teams_bot.teams import microsoft_agents

KEY = SecretStr("synthetic-test-signing-key-32-bytes")
BINDING = TeamsBinding("tenant", "aad-user", "chat")


def event(identifier=1):
    return AttendanceEvent(
        attendance_event_id=identifier,
        employee_id=7,
        punch_type="Office",
        location="private-location",
        checked_in_at=datetime(2026, 8, 1, 8, tzinfo=UTC),
        checked_out_at=None,
        note="private-note",
    )


def page(offset=0, next_offset=None, items=None):
    return AttendanceEventPage(
        items=(event(),) if items is None else items,
        limit=50,
        offset=offset,
        next_offset=next_offset,
    )


@dataclass
class Model:
    scope: str = "self"
    language: str = "en"
    calls: int = 0

    async def complete(self, request):
        self.calls += 1
        if request.pre_auth_guidance:
            arguments = {
                "start_date": "2020-01-01",
                "end_date": "2030-12-31",
                "reply_language": self.language,
            }
            if self.scope == "admin":
                arguments["email"] = "selected@example.test"
            return ToolCall(
                "selection",
                "list_my_attendance_events" if self.scope == "self" else "get_other_attendance",
                arguments,
            )
        return FinalResponse(
            "ignored",
            self.language,
            presentation=PresentationPlan(
                "Attendance" if self.language == "en" else "Prisotnost", None
            ),
        )


@dataclass
class Session:
    pages: list = field(default_factory=lambda: [page(next_offset=50)])
    calls: list = field(default_factory=list)
    catalogs: int = 0
    admitted: bool = True

    async def list_tools(self):
        self.catalogs += 1
        if not self.admitted:
            return ()

        def tool(name, keys):
            return DiscoveredMcpTool(
                name,
                "synthetic",
                {"type": "object", "properties": {key: {} for key in keys}},
                {"readOnlyHint": True},
            )

        return (
            tool("list_my_attendance_events", ("start_date", "end_date", "limit", "offset")),
            tool(
                "list_attendance_events",
                ("employee_id", "start_date", "end_date", "limit", "offset"),
            ),
            tool("resolve_employee", ("employee_id", "username", "email")),
        )

    async def call_tool(self, *, name, arguments):
        self.calls.append((name, arguments))
        if name == "resolve_employee":
            return ResolvedEmployee(employee_id=7)
        value = self.pages.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


@dataclass
class Factory:
    session: Session
    tokens: list = field(default_factory=list)

    @asynccontextmanager
    async def open(self, *, access_token, correlation_id):
        self.tokens.append(access_token)
        yield self.session


@dataclass
class Transport:
    sent: list = field(default_factory=list)
    fail_at: int | None = None
    attempts: int = 0

    async def send_activities(self, context, activities):
        self.attempts += 1
        if self.attempts == self.fail_at:
            raise RuntimeError("private-transport-detail")
        self.sent.extend(activities)
        return [ResourceResponse(id="sent") for _ in activities]


def activity(
    *,
    value=None,
    text=None,
    tenant="tenant",
    user="aad-user",
    chat="chat",
    conversation_type="personal",
):
    return Activity(
        type="message",
        id="incoming",
        channel_id="msteams",
        service_url="https://synthetic.example.test",
        text=text or "",
        value=value,
        conversation={"id": chat, "conversationType": conversation_type, "tenantId": tenant},
        from_property={"id": "opaque-channel-user", **({"aadObjectId": user} if user else {})},
        recipient={"id": "synthetic-bot"},
    )


def setup(monkeypatch, *, scope="self", language="en", session=None):
    session = session or Session()
    model, factory = Model(scope, language), Factory(session)
    signer = ContinuationSigner(KEY)
    agent = AttendanceAgent(model, factory, continuation_signer=signer)
    captured = {}

    class TrackingApplication(AgentApplication):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captured["sdk"] = self

    class Authorization:
        def __init__(self, *args, connection_manager, **kwargs):
            self.connection_manager = connection_manager
            self.sso_calls = 0
            captured["auth"] = self

        async def _on_turn_auth_intercept(self, context, state):
            return SimpleNamespace(should_skip_turn=False)

        async def _start_or_continue_sign_in(self, context, state, handler_id):
            assert handler_id == "attendance-teams-sso"
            return SimpleNamespace(sign_in_complete=lambda: True)

        async def get_token(self, context, handler_id):
            assert isinstance(context, TurnContext)
            self.sso_calls += 1
            return SimpleNamespace(token=f"fresh-sso-{self.sso_calls}")

    class Obo:
        def __init__(self, **kwargs):
            self.calls = []
            captured["obo"] = self

        async def exchange(self, assertion):
            self.calls.append(assertion)
            return SecretStr(f"fresh-obo-{len(self.calls)}")

    monkeypatch.setattr(microsoft_agents, "AgentApplication", TrackingApplication)
    monkeypatch.setattr(microsoft_agents, "Authorization", Authorization)
    monkeypatch.setattr(microsoft_agents, "MsalOboTokenExchange", Obo)
    microsoft_agents.create_attendance_teams_http_app(
        connection=TeamsConnectionSettings(
            client_id=UUID(int=1),
            tenant_id=UUID(int=2),
            client_secret=SecretStr("synthetic-client-credential"),
        ),
        attendance_application=agent,
        oauth_connection_name="synthetic-sso",
        delegated_scope="api://synthetic/attendance.access",
        continuation_signer=signer,
    )
    return captured, agent, model, factory, signer


def button(sent):
    return sent[-1].attachments[0].content["actions"][0]["data"]


@pytest.mark.anyio
@pytest.mark.parametrize("scope", ["self", "admin"])
async def test_actual_sdk_routes_repeat_and_restart_button_with_live_authorization(
    monkeypatch, scope
):
    session = Session(
        pages=[
            page(next_offset=50),
            page(50, 100),
            page(50, None, (event(9).model_copy(update={"punch_type": "Remote work"}),)),
            page(50, None, ()),
        ]
    )
    captured, agent, model, factory, signer = setup(monkeypatch, scope=scope, session=session)
    transport = Transport()
    await captured["sdk"].on_turn(TurnContext(transport, activity(text="2020 through 2030")))
    data = button(transport.sent)
    assert len(transport.sent) == 2
    assert "2020-01-01" in transport.sent[0].text and "2030-12-31" in transport.sent[0].text
    assert "More results" in transport.sent[0].text
    assert data["verb"] == HISTORY_VERB
    assert model.calls == 2
    assert signer.verify(data["continuation"], BINDING).target == (7 if scope == "admin" else None)
    for _ in range(2):
        await captured["sdk"].on_turn(TurnContext(transport, activity(value=data)))
    assert "Remote work" in transport.sent[-1].text
    assert "Final page" in transport.sent[-1].text and not transport.sent[-1].attachments
    # Simulated restart with new application, signer and SDK storage; original button survives.
    restarted, _, new_model, new_factory, _ = setup(monkeypatch, scope=scope, session=session)
    await restarted["sdk"].on_turn(TurnContext(transport, activity(value=data)))
    history_calls = [call for call in session.calls if call[0] != "resolve_employee"]
    assert [args["offset"] for _, args in history_calls] == [0, 50, 50, 50]
    assert all(
        args["start_date"] == "2020-01-01"
        and args["end_date"] == "2030-12-31"
        and args["limit"] == 50
        for _, args in history_calls
    )
    assert all(("employee_id" in args) == (scope == "admin") for _, args in history_calls)
    assert sum(name == "resolve_employee" for name, _ in session.calls) == (
        1 if scope == "admin" else 0
    )
    assert captured["auth"].sso_calls == 3 and len(captured["obo"].calls) == 3
    assert restarted["auth"].sso_calls == 1 and len(restarted["obo"].calls) == 1
    assert model.calls == 2 and new_model.calls == 0
    assert factory.tokens == [
        SecretStr("fresh-obo-1"),
        SecretStr("fresh-obo-2"),
        SecretStr("fresh-obo-3"),
    ]
    assert new_factory.tokens == [SecretStr("fresh-obo-1")]
    assert session.catalogs == 4
    assert not transport.sent[-1].attachments and "No attendance events" in transport.sent[-1].text
    assert "private-note" not in repr(transport.sent) and "private-location" not in repr(
        transport.sent
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "variant",
    [
        "tampered",
        "wrong-tenant",
        "wrong-user",
        "wrong-chat",
        "channel",
        "unknown-verb",
        "with-text",
        "missing-token",
        "extra-field",
        "nonobject",
        "missing-binding",
    ],
)
async def test_sdk_rejects_malformed_submissions_without_model_or_attendance(monkeypatch, variant):
    captured, _, model, factory, signer = setup(monkeypatch)
    query = HistoryContinuation("self", date(2020, 1, 1), date(2030, 12, 31), 50, "en", BINDING)
    data = {"verb": HISTORY_VERB, "continuation": signer.sign(query)}
    kwargs = {}
    if variant == "tampered":
        data["continuation"] += "x"
    elif variant == "unknown-verb":
        data["verb"] = "unknown"
    elif variant == "with-text":
        kwargs["text"] = "show my history"
    elif variant == "missing-token":
        del data["continuation"]
    elif variant == "extra-field":
        data["token"] = "synthetic-disallowed-data"
    elif variant == "nonobject":
        data = ["invalid"]
    elif variant == "missing-binding":
        kwargs["user"] = ""
    elif variant == "channel":
        kwargs["conversation_type"] = "channel"
    else:
        kwargs[{"wrong-tenant": "tenant", "wrong-user": "user", "wrong-chat": "chat"}[variant]] = (
            "mismatch"
        )
    transport = Transport()
    with capture_logs() as logs:
        await captured["sdk"].on_turn(TurnContext(transport, activity(value=data, **kwargs)))
    assert len(transport.sent) == 1 and not transport.sent[0].attachments
    assert not model.calls and not factory.tokens and not factory.session.calls
    assert not captured["auth"].sso_calls and not captured["obo"].calls
    assert "continuation" not in repr(logs) and "synthetic-disallowed-data" not in repr(logs)


@pytest.mark.anyio
@pytest.mark.parametrize("scope", ["self", "admin"])
@pytest.mark.parametrize("failure", ["catalog", "forbidden", "sso", "obo"])
async def test_continuation_rechecks_live_authorization_and_safe_failures(
    monkeypatch, scope, failure
):
    captured, _, model, factory, signer = setup(monkeypatch, scope=scope)
    query = HistoryContinuation(
        scope,
        date(2020, 1, 1),
        date(2030, 12, 31),
        50,
        "sl",
        BINDING,
        7 if scope == "admin" else None,
    )
    if failure == "catalog":
        factory.session.admitted = False
    elif failure == "forbidden":
        factory.session.pages = [
            AttendanceToolFailure(
                McpToolFailure(code="FORBIDDEN", message="You do not have permission to do that.")
            )
        ]
    elif failure == "sso":

        async def unavailable(*args):
            raise RuntimeError("private-auth-detail")

        captured["auth"].get_token = unavailable
    else:
        from attendance_teams_bot.auth.obo import DelegatedAuthenticationUnavailable

        async def denied(*args):
            raise DelegatedAuthenticationUnavailable("private-obo-detail")

        captured["obo"].exchange = denied
    transport = Transport()
    await captured["sdk"].on_turn(
        TurnContext(
            transport, activity(value={"verb": HISTORY_VERB, "continuation": signer.sign(query)})
        )
    )
    assert not model.calls
    assert len(transport.sent) == 1 and not transport.sent[0].attachments
    assert "private-" not in transport.sent[0].text
    assert len(factory.session.calls) == (1 if failure == "forbidden" else 0)
    if failure == "forbidden":
        assert "Nimate dovoljenja" in transport.sent[0].text
        assert factory.session.calls[0][0] == (
            "list_my_attendance_events" if scope == "self" else "list_attendance_events"
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "updates",
    [
        {"limit": 49},
        {"limit": True},
        {"limit": "50"},
        {"offset": 0},
        {"offset": "50"},
        {"next_offset": 50},
        {"next_offset": 49},
        {"next_offset": True},
        {"items": (event(),) * 51},
        {"items": (), "next_offset": 100},
    ],
)
async def test_sdk_page_metadata_mismatch_returns_no_facts_or_button(monkeypatch, updates):
    malformed = page(50).model_copy(update=updates)
    captured, _, model, factory, signer = setup(monkeypatch, session=Session(pages=[malformed]))
    query = HistoryContinuation("self", date(2020, 1, 1), date(2030, 12, 31), 50, "en", BINDING)
    transport = Transport()
    await captured["sdk"].on_turn(
        TurnContext(
            transport, activity(value={"verb": HISTORY_VERB, "continuation": signer.sign(query)})
        )
    )
    assert len(factory.session.calls) == 1 and not model.calls
    assert len(transport.sent) == 1 and "temporarily unavailable" in transport.sent[0].text
    assert not transport.sent[0].attachments and "08:00" not in transport.sent[0].text


@pytest.mark.anyio
async def test_every_text_batch_precedes_card_and_failed_text_sends_stop_without_retry(monkeypatch):
    import attendance_teams_bot.agent.rendering as rendering

    monkeypatch.setattr(rendering, "MAX_REPLY_CHARACTERS", 250)
    records = (
        event(),
        event(2).model_copy(update={"checked_in_at": datetime(2026, 8, 2, 8, tzinfo=UTC)}),
    )
    for fail_at in (None, 2):
        captured, _, model, _, _ = setup(
            monkeypatch, language="sl", session=Session(pages=[page(next_offset=50, items=records)])
        )
        transport = Transport(fail_at=fail_at)
        with capture_logs() as logs:
            if fail_at:
                with pytest.raises(RuntimeError, match="private-transport-detail"):
                    await captured["sdk"].on_turn(
                        TurnContext(transport, activity(text="2020 through 2030"))
                    )
            else:
                await captured["sdk"].on_turn(
                    TurnContext(transport, activity(text="2020 through 2030"))
                )
        assert model.calls == 2
        assert "private-transport-detail" not in repr(logs)
        if fail_at:
            assert transport.attempts == 2 and len(transport.sent) == 1
            assert not any(item.attachments for item in transport.sent)
        else:
            assert len(transport.sent) == 3
            assert all(not item.attachments for item in transport.sent[:-1])
            assert button(transport.sent)["verb"] == HISTORY_VERB
            assert "vključno" in transport.sent[0].text
            assert "Več rezultatov" in transport.sent[-2].text
            assert "Konec rezultatov" not in transport.sent[-2].text
            assert (
                transport.sent[-1].attachments[0].content["actions"][0]["title"]
                == "Naslednja stran"
            )


@pytest.mark.anyio
async def test_failed_card_send_is_not_retried_and_logs_no_signed_data(monkeypatch):
    captured, _, _, _, _ = setup(monkeypatch)
    transport = Transport(fail_at=2)
    with capture_logs() as logs:
        with pytest.raises(RuntimeError, match="private-transport-detail"):
            await captured["sdk"].on_turn(
                TurnContext(transport, activity(text="2020 through 2030"))
            )
    assert transport.attempts == 2 and len(transport.sent) == 1
    failures = [entry for entry in logs if entry["event"] == "teams_reply_send_failed"]
    assert len(failures) == 1 and failures[0]["completed_messages"] == 1
    assert failures[0]["total_messages"] == 2 and failures[0]["error_type"] == "RuntimeError"
    assert "continuation" not in repr(logs) and "private-transport-detail" not in repr(logs)


@pytest.mark.anyio
@pytest.mark.parametrize("language", ["en", "sl"])
async def test_empty_page_displays_original_period_and_final_page_without_card(
    monkeypatch, language
):
    captured, _, model, _, signer = setup(monkeypatch, session=Session(pages=[page(50, items=())]))
    query = HistoryContinuation("self", date(2020, 1, 1), date(2030, 12, 31), 50, language, BINDING)
    transport = Transport()
    await captured["sdk"].on_turn(
        TurnContext(
            transport, activity(value={"verb": HISTORY_VERB, "continuation": signer.sign(query)})
        )
    )
    assert len(transport.sent) == 1 and not transport.sent[0].attachments and not model.calls
    text = transport.sent[0].text
    assert "2020-01-01" in text and "2030-12-31" in text
    assert ("Final page" if language == "en" else "Zadnja stran") in text
