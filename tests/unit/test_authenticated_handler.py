import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

import attendance_teams_bot.observability as observability
from attendance_teams_bot.agent.contracts import BotResponse, SelectedAttendanceAction
from attendance_teams_bot.auth.obo import DelegatedAuthenticationUnavailable
from attendance_teams_bot.memory.models import ChatMessage
from attendance_teams_bot.teams.authenticated import (
    BotServiceOnlyHandler,
    SsoOboAttendanceTurnHandler,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class Conversation:
    conversation_type: str | None = "personal"
    tenant_id: str | None = None


@dataclass
class Sender:
    name: str | None = None
    aad_object_id: str | None = None


@dataclass
class Activity:
    type: str = "message"
    text: str | None = "Show my attendance"
    conversation: Conversation | None = field(default_factory=Conversation)
    from_property: Sender | None = None


@dataclass
class Context:
    activity: Activity
    sent: list[str] = field(default_factory=list)

    async def send_activity(self, text: str) -> None:
        self.sent.append(text)


@dataclass
class Application:
    tokens: list[SecretStr] = field(default_factory=list)
    display_names: list[str | None] = field(default_factory=list)

    async def pre_auth_decision(
        self,
        *,
        message: str,
        display_name: str | None = None,
        history: tuple[ChatMessage, ...] = (),
    ) -> SelectedAttendanceAction:
        del message, history
        self.display_names.append(display_name)
        return SelectedAttendanceAction("list_my_attendance_events", {}, "en")

    async def handle_selected(
        self,
        *,
        message: str,
        mcp_access_token: SecretStr,
        selection: SelectedAttendanceAction,
        display_name: str | None = None,
        history: tuple[ChatMessage, ...] = (),
    ) -> BotResponse:
        del message, history
        assert selection.name == "list_my_attendance_events"
        self.tokens.append(mcp_access_token)
        assert display_name == self.display_names[-1]
        return BotResponse(text="attendance reply")


@dataclass
class Sso:
    calls: int = 0

    async def get_token(self, context: Context) -> SecretStr:
        del context
        self.calls += 1
        return SecretStr("token-a")


@dataclass
class Obo:
    assertions: list[SecretStr] = field(default_factory=list)

    async def exchange(self, user_assertion: SecretStr) -> SecretStr:
        self.assertions.append(user_assertion)
        return SecretStr("token-b")


@dataclass
class Memory:
    history: tuple[ChatMessage, ...] = ()
    reads: list[str] = field(default_factory=list)
    saved: list[tuple[str, str, str]] = field(default_factory=list)

    async def get_chat_history(self, session_id: str, limit: int = 10) -> tuple[ChatMessage, ...]:
        assert limit == 10
        self.reads.append(session_id)
        return self.history

    async def save_message(self, session_id: str, role: str, content: str) -> None:
        del session_id, role, content

    async def save_exchange(
        self, session_id: str, user_content: str, assistant_content: str
    ) -> None:
        self.saved.append((session_id, user_content, assistant_content))


def handler(
    application: Application,
    sso: Sso | None = None,
    obo: Obo | None = None,
    memory: Memory | None = None,
) -> SsoOboAttendanceTurnHandler:
    return SsoOboAttendanceTurnHandler(
        application=application,
        sso_token_provider=sso or Sso(),
        obo_token_exchange=obo or Obo(),
        memory=memory or Memory(),
    )


@pytest.mark.anyio
async def test_bot_service_only_handler_returns_a_safe_reply() -> None:
    response = await BotServiceOnlyHandler().handle(message="Show my attendance")
    assert "Teams SSO, MCP" in response.text


@pytest.mark.anyio
async def test_personal_turn_exchanges_token_a_and_forwards_only_token_b() -> None:
    application, sso, obo = Application(), Sso(), Obo()
    context = Context(Activity(from_property=Sender("Unverified display name")))

    await handler(application, sso, obo).handle(context)

    assert sso.calls == 1
    assert obo.assertions == [SecretStr("token-a")]
    assert application.tokens == [SecretStr("token-b")]
    assert application.display_names == ["Unverified display name"]
    assert context.sent == ["attendance reply"]


@pytest.mark.anyio
async def test_memory_saves_delivered_authenticated_exchange() -> None:
    @dataclass
    class MemoryApplication(Application):
        observed_history: tuple[ChatMessage, ...] = ()

        async def pre_auth_decision(self, **kwargs: object) -> SelectedAttendanceAction:
            self.observed_history = kwargs["history"]  # type: ignore[assignment,index]
            return await super().pre_auth_decision(**kwargs)  # type: ignore[arg-type]

        async def handle_selected(self, **kwargs: object) -> BotResponse:
            del kwargs
            return BotResponse("safe guidance", assistant_memory="non-factual framing")

    memory = Memory((ChatMessage("user", "earlier", datetime.now(UTC)),))
    application = MemoryApplication()
    context = Context(
        Activity(
            conversation=Conversation("personal", "4a9c3c7b-eb0a-4e92-8b4a-0a8f1c65c786"),
            from_property=Sender("Unverified", "63b649e6-17d4-4913-8b24-1a0b3c2da274"),
        )
    )

    await handler(application, memory=memory).handle(context)

    assert application.observed_history == memory.history
    assert len(memory.reads) == len(memory.saved) == 1
    assert memory.saved[0][1:] == ("Show my attendance", "non-factual framing")


@pytest.mark.anyio
async def test_missing_authenticated_identifiers_keeps_the_turn_stateless() -> None:
    memory = Memory()
    await handler(Application(), memory=memory).handle(Context(Activity()))

    assert memory.reads == []
    assert memory.saved == []


@pytest.mark.anyio
@pytest.mark.parametrize("conversation_type", ["channel", "groupChat", None])
async def test_nonpersonal_turn_stops_before_sso(
    conversation_type: str | None,
) -> None:
    application, sso, obo = Application(), Sso(), Obo()
    context = Context(Activity(conversation=Conversation(conversation_type)))

    await handler(application, sso, obo).handle(context)

    assert sso.calls == 0
    assert obo.assertions == []
    assert application.tokens == []
    assert context.sent == ["Attendance is available only in a personal chat."]


@pytest.mark.anyio
async def test_blank_turn_does_not_start_authentication() -> None:
    application, sso, obo = Application(), Sso(), Obo()
    context = Context(Activity(text="  "))

    await handler(application, sso, obo).handle(context)

    assert sso.calls == 0
    assert obo.assertions == []
    assert application.tokens == []
    assert context.sent == ["Please send a message so I can help."]


@pytest.mark.anyio
async def test_safe_pre_auth_scope_guidance_skips_sso_and_obo() -> None:
    class GuidanceApplication(Application):
        async def pre_auth_decision(self, **kwargs: object) -> BotResponse:
            del kwargs
            return BotResponse("I can help with attendance, not weather forecasts.")

    application, sso, obo = GuidanceApplication(), Sso(), Obo()
    context = Context(Activity(text="What is the weather?"))

    await handler(application, sso, obo).handle(context)

    assert sso.calls == 0
    assert obo.assertions == []
    assert application.tokens == []
    assert context.sent == ["I can help with attendance, not weather forecasts."]


@pytest.mark.anyio
async def test_reply_batches_are_delivered_in_order_without_retry() -> None:
    class BatchApplication(Application):
        async def handle_selected(self, **kwargs: object) -> BotResponse:
            del kwargs
            return BotResponse.batch(("first date group", "second date group"))

    context = Context(Activity())
    await handler(BatchApplication()).handle(context)

    assert context.sent == ["first date group", "second date group"]


@pytest.mark.anyio
async def test_sso_and_obo_failures_send_safe_replies_without_calling_the_application() -> None:
    class FailingSso(Sso):
        async def get_token(self, context: Context) -> SecretStr:
            del context
            raise RuntimeError("token-a secret")

    class FailingObo(Obo):
        async def exchange(self, user_assertion: SecretStr) -> SecretStr:
            del user_assertion
            raise DelegatedAuthenticationUnavailable("token-b secret")

    for sso, obo in ((FailingSso(), Obo()), (Sso(), FailingObo())):
        application = Application()
        context = Context(Activity())
        await handler(application, sso, obo).handle(context)
        assert application.tokens == []
        assert context.sent == [
            "Authentication is temporarily unavailable. Please try again later."
        ]
        assert "secret" not in repr(context.sent)


@pytest.mark.anyio
async def test_cancellation_emits_one_terminal_event_and_does_not_reply(monkeypatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    def record_event(_logger: object, *, event: str, **fields: object) -> None:
        events.append((event, fields))

    monkeypatch.setattr(observability, "operation_event", record_event)

    class CancellingApplication(Application):
        async def handle_selected(self, **kwargs: object) -> BotResponse:
            del kwargs
            raise asyncio.CancelledError

    secret_message = "token=must-not-leak"
    context = Context(Activity(text=secret_message))
    with pytest.raises(asyncio.CancelledError):
        await handler(CancellingApplication()).handle(context)

    terminal = [event for event in events if event[0] == "operation_cancelled"]
    assert len(terminal) == 1
    assert terminal[0][1]["step"] == "attendance_application"
    assert context.sent == []
    assert secret_message not in repr(events)


@pytest.mark.anyio
async def test_application_and_reply_failures_emit_the_active_step_lifecycle_event(
    monkeypatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    def record_event(_logger: object, *, event: str, **fields: object) -> None:
        events.append((event, fields))

    monkeypatch.setattr(observability, "operation_event", record_event)

    class FailingApplication(Application):
        async def handle_selected(self, **kwargs: object) -> BotResponse:
            del kwargs
            raise ValueError("application failure")

    with pytest.raises(ValueError, match="application failure"):
        await handler(FailingApplication()).handle(Context(Activity()))

    assert events[-1] == (
        "operation_failed",
        {
            "handler": "SsoOboAttendanceTurnHandler",
            "operation": "authenticated_attendance_turn",
            "step": "attendance_application",
            "duration_ms": events[-1][1]["duration_ms"],
            "error_type": "ValueError",
            "input_metadata": {"message_present": True, "message_length": 18},
        },
    )

    @dataclass
    class FailingReplyContext(Context):
        async def send_activity(self, text: str) -> None:
            del text
            raise RuntimeError("delivery failure")

    with pytest.raises(RuntimeError, match="delivery failure"):
        await handler(Application()).handle(FailingReplyContext(Activity()))

    assert events[-1][0] == "operation_failed"
    assert events[-1][1]["step"] == "teams_reply_delivery"


@pytest.mark.anyio
async def test_failed_batch_send_stops_without_retry_and_logs_only_safe_progress(
    monkeypatch,
) -> None:
    import attendance_teams_bot.teams.authenticated as authenticated

    logged: list[dict[str, object]] = []

    class Logger:
        def error(self, event: str, **fields: object) -> None:
            logged.append({"event": event, **fields})

    monkeypatch.setattr(authenticated, "_LOGGER", Logger())

    @dataclass
    class SecondSendFails(Context):
        async def send_activity(self, text: str) -> None:
            if text == "second":
                raise RuntimeError("transport detail")
            self.sent.append(text)

    class BatchApplication(Application):
        async def handle_selected(self, **kwargs: object) -> BotResponse:
            del kwargs
            return BotResponse.batch(("first", "second", "third"))

    context = SecondSendFails(Activity())
    with pytest.raises(RuntimeError, match="transport detail"):
        await SsoOboAttendanceTurnHandler._send_response(
            context, BotResponse.batch(("first", "second", "third"))
        )

    assert context.sent == ["first"]
    assert len(logged) == 1
    assert logged[0]["event"] == "teams_reply_send_failed"
    assert logged[0]["completed_messages"] == 1
    assert logged[0]["total_messages"] == 3
    assert logged[0]["error_type"] == "RuntimeError"
    assert "transport detail" not in repr(logged)


@pytest.mark.anyio
async def test_failed_later_batch_message_does_not_save_an_exchange() -> None:
    @dataclass
    class SecondSendFails(Context):
        async def send_activity(self, text: str) -> None:
            if text == "second":
                raise RuntimeError("synthetic delivery failure")
            self.sent.append(text)

    class BatchApplication(Application):
        async def handle_selected(self, **kwargs: object) -> BotResponse:
            del kwargs
            return BotResponse("first", ("first", "second"), "safe framing")

    memory = Memory()
    context = SecondSendFails(
        Activity(
            conversation=Conversation("personal", "4a9c3c7b-eb0a-4e92-8b4a-0a8f1c65c786"),
            from_property=Sender("Unverified", "63b649e6-17d4-4913-8b24-1a0b3c2da274"),
        )
    )

    with pytest.raises(RuntimeError, match="synthetic delivery failure"):
        await handler(BatchApplication(), memory=memory).handle(context)

    assert context.sent == ["first"]
    assert memory.saved == []
