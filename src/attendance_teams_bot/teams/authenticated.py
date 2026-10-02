from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

import structlog
from pydantic import SecretStr

from attendance_teams_bot.agent.contracts import BotResponse, SelectedAttendanceAction
from attendance_teams_bot.auth.obo import DelegatedAuthenticationUnavailable
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
        self, *, message: str, display_name: str | None = None
    ) -> BotResponse | SelectedAttendanceAction: ...

    async def handle_selected(
        self,
        *,
        message: str,
        mcp_access_token: SecretStr,
        selection: SelectedAttendanceAction,
        display_name: str | None = None,
    ) -> BotResponse: ...


class TurnConversation(Protocol):
    conversation_type: str | None


class TurnSender(Protocol):
    name: str | None


class TurnActivity(Protocol):
    type: str
    text: str | None
    conversation: TurnConversation | None
    from_property: TurnSender | None


class BotServiceAuthenticatedTurnContext(Protocol):
    @property
    def activity(self) -> TurnActivity: ...

    async def send_activity(self, text: str) -> object: ...


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
        decision = await self.application.pre_auth_decision(
            message=message, display_name=display_name
        )
        if isinstance(decision, BotResponse):
            await self._send_response(context, decision)
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
            )
            lifecycle.step_completed(step=step)

            step = "teams_reply_delivery"
            try:
                await self._send_response(context, response)
            except Exception as error:
                logger.log(
                    logging.ERROR,
                    "teams_reply_send_failed",
                    correlation_id=str(correlation_id),
                    error_type=type(error).__name__,
                )
                raise
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
        for message in response.messages:
            await context.send_activity(message)

    @staticmethod
    def _display_name(context: BotServiceAuthenticatedTurnContext) -> str | None:
        sender = context.activity.from_property
        return sender.name if sender is not None else None
