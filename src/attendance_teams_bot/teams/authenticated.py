from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Protocol, cast
from uuid import UUID

import structlog
from pydantic import SecretStr

from attendance_teams_bot.agent.continuation import (
    HistoryContinuation,
    continuation_card,
    is_typed_continuation,
)
from attendance_teams_bot.agent.contracts import BotResponse, SelectedAttendanceAction
from attendance_teams_bot.auth.obo import DelegatedAuthenticationUnavailable
from attendance_teams_bot.memory.database import (
    ConversationMemory,
    StatelessConversationMemory,
    memory_session_id,
)
from attendance_teams_bot.memory.models import ChatMessage
from attendance_teams_bot.observability import (
    OperationLifecycle,
    current_correlation_id,
    message_input_metadata,
)

_SAFE_BOT_SERVICE_ONLY_REPLY = (
    "Bot Service connectivity is verified, but Teams SSO, MCP, "
    "and LLM adapters are not configured yet."
)
_SAFE_AUTHENTICATION_REPLY = "Authentication is temporarily unavailable. Please try again later."
_LOGGER = structlog.get_logger(__name__)


class BotServiceOnlyMessageHandler(Protocol):
    async def handle(self, *, message: str) -> BotResponse: ...


class AttendanceApplication(Protocol):
    async def pre_auth_decision(
        self,
        *,
        message: str,
        display_name: str | None = None,
        history: tuple[ChatMessage, ...] = (),
    ) -> BotResponse | SelectedAttendanceAction: ...

    async def handle_selected(
        self,
        *,
        message: str,
        mcp_access_token: SecretStr,
        selection: SelectedAttendanceAction,
        display_name: str | None = None,
        history: tuple[ChatMessage, ...] = (),
    ) -> BotResponse: ...

    async def handle_continuation(
        self,
        *,
        query: HistoryContinuation,
        mcp_access_token: SecretStr,
        display_name: str | None = None,
    ) -> BotResponse: ...


class TurnConversation(Protocol):
    conversation_type: str | None
    tenant_id: str | None
    id: str | None


class TurnSender(Protocol):
    name: str | None
    aad_object_id: str | None


class TurnActivity(Protocol):
    type: str
    text: str | None
    conversation: TurnConversation | None
    from_property: TurnSender | None


class BotServiceAuthenticatedTurnContext(Protocol):
    @property
    def activity(self) -> TurnActivity: ...

    async def send_activity(self, text: str) -> object: ...


class AttachmentTurnContext(BotServiceAuthenticatedTurnContext, Protocol):
    async def send_attachment(self, attachment: object) -> object: ...


class SsoTokenProvider(Protocol):
    async def get_token(self, context: BotServiceAuthenticatedTurnContext) -> SecretStr: ...


class OboTokenExchange(Protocol):
    async def exchange(self, user_assertion: SecretStr) -> SecretStr: ...


@dataclass(frozen=True, slots=True)
class BotServiceOnlyHandler:
    """Safe handler used after Bot Service authentication without attendance integrations."""

    async def handle(self, *, message: str) -> BotResponse:
        del message
        return BotResponse(text=_SAFE_BOT_SERVICE_ONLY_REPLY)


@dataclass(frozen=True, slots=True)
class SsoOboAttendanceTurnHandler:
    """Run an admitted personal Teams turn through SSO, OBO, and attendance processing."""

    application: AttendanceApplication
    sso_token_provider: SsoTokenProvider
    obo_token_exchange: OboTokenExchange
    memory: ConversationMemory = field(default_factory=StatelessConversationMemory)

    async def handle(self, context: BotServiceAuthenticatedTurnContext) -> None:
        """Process personal text turns only, obtaining SSO and OBO tokens before delegation."""
        if context.activity.type != "message" or context.activity.text is None:
            return
        conversation = context.activity.conversation
        if conversation is None or conversation.conversation_type != "personal":
            await self._reject_nonpersonal(context)
            return
        message = context.activity.text.strip()
        if not message:
            await self._reject_blank(context)
            return
        await self._handle_attendance_message(context=context, message=message)

    async def _reject_nonpersonal(self, context: BotServiceAuthenticatedTurnContext) -> None:
        correlation_id = current_correlation_id()
        logger = _LOGGER.bind(correlation_id=str(correlation_id))
        logger.log(
            logging.INFO,
            "teams_turn_rejected",
            correlation_id=str(correlation_id),
            outcome="nonpersonal_conversation",
        )
        await context.send_activity("Attendance is available only in a personal chat.")

    async def _reject_blank(self, context: BotServiceAuthenticatedTurnContext) -> None:
        correlation_id = current_correlation_id()
        logger = _LOGGER.bind(correlation_id=str(correlation_id))
        logger.log(
            logging.INFO,
            "teams_turn_rejected",
            correlation_id=str(correlation_id),
            outcome="blank_message",
        )
        await context.send_activity("Please send a message so I can help.")

    async def _handle_attendance_message(
        self, *, context: BotServiceAuthenticatedTurnContext, message: str
    ) -> None:
        display_name = self._display_name(context)
        session_id = self._session_id(context)
        conversation_id = self._conversation_id(context)
        if session_id and conversation_id and is_typed_continuation(message):
            await self._handle_continuation(context, session_id, conversation_id, None)
            return
        history = await self.memory.get_chat_history(session_id) if session_id else ()
        decision = await self.application.pre_auth_decision(
            message=message, display_name=display_name, history=history
        )
        if isinstance(decision, BotResponse):
            await self._send_response(context, decision)
            await self._save_delivered_exchange(session_id, message, decision)
            await self._store_delivered_continuation(session_id, conversation_id, context, decision)
            return
        correlation_id = current_correlation_id()
        logger = _LOGGER.bind(correlation_id=str(correlation_id))
        input_metadata = message_input_metadata(message)
        step = "teams_sso"
        lifecycle = OperationLifecycle(
            logger,
            handler=type(self).__name__,
            operation="authenticated_attendance_turn",
            input_metadata=input_metadata,
        )
        lifecycle.start(step=step)
        try:
            try:
                token_a = await self.sso_token_provider.get_token(context)
            except RuntimeError as error:
                await self._reply_for_authentication_failure(
                    context=context,
                    logger=logger,
                    lifecycle=lifecycle,
                    error=error,
                    event="teams_sso_token_unavailable",
                )
                return
            lifecycle.step_completed()

            step = "delegated_obo"
            try:
                token_b = await self.obo_token_exchange.exchange(token_a)
            except DelegatedAuthenticationUnavailable as error:
                await self._reply_for_authentication_failure(
                    context=context,
                    logger=logger,
                    lifecycle=lifecycle,
                    error=error,
                    event="teams_obo_exchange_failed",
                )
                return
            lifecycle.step_completed(step=step)

            step = "attendance_application"
            response = await self.application.handle_selected(
                message=message,
                mcp_access_token=token_b,
                selection=decision,
                display_name=display_name,
                history=history,
            )
            lifecycle.step_completed(step=step)

            step = "teams_reply_delivery"
            await self._send_response(context, response)
            await self._save_delivered_exchange(session_id, message, response)
            await self._store_delivered_continuation(session_id, conversation_id, context, response)
            lifecycle.succeed(step=step)
        except asyncio.CancelledError:
            if not lifecycle.terminal:
                lifecycle.cancel(step=step)
            raise
        except Exception as error:
            lifecycle.fail(error, step=step)
            raise

    async def _reply_for_authentication_failure(
        self,
        *,
        context: BotServiceAuthenticatedTurnContext,
        logger: structlog.BoundLogger,
        lifecycle: OperationLifecycle,
        error: RuntimeError | DelegatedAuthenticationUnavailable,
        event: str,
    ) -> None:
        logger.log(
            logging.WARNING,
            event,
            correlation_id=str(current_correlation_id()),
            error_type=type(error).__name__,
        )
        lifecycle.fail(error)
        await context.send_activity(_SAFE_AUTHENTICATION_REPLY)

    @staticmethod
    async def _send_response(
        context: BotServiceAuthenticatedTurnContext, response: BotResponse
    ) -> None:
        """Deliver a validated batch in order; a failed send is deliberately not retried."""
        completed = 0
        for message in response.messages:
            try:
                await context.send_activity(message)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                _LOGGER.error(
                    "teams_reply_send_failed",
                    correlation_id=str(current_correlation_id()),
                    completed_messages=completed,
                    total_messages=len(response.messages),
                    error_type=type(error).__name__,
                )
                raise
            completed += 1

    @staticmethod
    def _display_name(context: BotServiceAuthenticatedTurnContext) -> str | None:
        sender = context.activity.from_property
        return sender.name if sender is not None else None

    @staticmethod
    def _session_id(context: BotServiceAuthenticatedTurnContext) -> str | None:
        conversation = context.activity.conversation
        sender = context.activity.from_property
        return memory_session_id(
            getattr(conversation, "tenant_id", None), getattr(sender, "aad_object_id", None)
        )

    @staticmethod
    def _conversation_id(context: BotServiceAuthenticatedTurnContext) -> str | None:
        conversation = context.activity.conversation
        value = getattr(conversation, "id", None)
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            return None
        return hashlib.sha256(f"teams-continuation:v1:{value}".encode()).hexdigest()

    async def _save_delivered_exchange(
        self, session_id: str | None, message: str, response: BotResponse
    ) -> None:
        if session_id is not None and response.assistant_memory is not None:
            await self.memory.save_exchange(session_id, message, response.assistant_memory)

    async def _store_delivered_continuation(
        self,
        session_id: str | None,
        conversation_id: str | None,
        context: BotServiceAuthenticatedTurnContext,
        response: BotResponse,
    ) -> None:
        if session_id is None or conversation_id is None or not response.continuation_complete:
            return
        identifier = await self.memory.replace_continuation(
            session_id, conversation_id, response.continuation
        )
        if identifier is not None and response.continuation is not None:
            await cast(AttachmentTurnContext, context).send_attachment(
                continuation_card(identifier, response.continuation.language)
            )

    async def handle_submission(
        self, context: BotServiceAuthenticatedTurnContext, data: object
    ) -> None:
        """Use the same active continuation record as typed next-page requests."""
        if not isinstance(data, dict) or set(data) != {"verb", "continuation_id"}:
            await context.send_activity(
                "This page button is invalid. Please restate the attendance period."
            )
            return
        try:
            identifier = UUID(str(data["continuation_id"]))
        except ValueError:
            await context.send_activity(
                "This page button is invalid. Please restate the attendance period."
            )
            return
        session_id, conversation_id = self._session_id(context), self._conversation_id(context)
        if session_id is None or conversation_id is None:
            await context.send_activity(
                "There is no active next page. Please restate the attendance period."
            )
            return
        await self._handle_continuation(context, session_id, conversation_id, identifier)

    async def _handle_continuation(
        self,
        context: BotServiceAuthenticatedTurnContext,
        session_id: str,
        conversation_id: str,
        identifier: UUID | None,
    ) -> None:
        claimed = await self.memory.claim_continuation(session_id, conversation_id, identifier)
        if claimed is None:
            await context.send_activity(
                "There is no available next page. Please restate the attendance period."
            )
            return
        try:
            token_a = await self.sso_token_provider.get_token(context)
            token_b = await self.obo_token_exchange.exchange(token_a)
            response = await self.application.handle_continuation(
                query=claimed.query,
                mcp_access_token=token_b,
                display_name=self._display_name(context),
            )
            await self._send_response(context, response)
            if not response.continuation_complete:
                await self.memory.release_continuation(claimed, session_id, conversation_id)
                return
            identifier = await self.memory.finish_continuation(
                claimed, session_id, conversation_id, response.continuation
            )
            if identifier is not None and response.continuation is not None:
                await cast(AttachmentTurnContext, context).send_attachment(
                    continuation_card(identifier, response.continuation.language)
                )
        except asyncio.CancelledError:
            await self.memory.release_continuation(claimed, session_id, conversation_id)
            raise
        except Exception:
            await self.memory.release_continuation(claimed, session_id, conversation_id)
            raise
