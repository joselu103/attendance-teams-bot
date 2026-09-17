from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from pydantic import SecretStr

from attendance_teams_bot.agent.contracts import BotResponse
from attendance_teams_bot.auth.obo import DelegatedAuthenticationUnavailable
from attendance_teams_bot.observability import (
    current_correlation_id,
    get_logger,
    log_event,
    message_input_metadata,
    operation_event,
)

_SAFE_BOT_SERVICE_REPLY = (
    "Microsoft Bot Service connectivity is verified, but Teams SSO, MCP, "
    "and LLM adapters are not configured yet."
)
_SAFE_AUTHENTICATION_REPLY = "Authentication is temporarily unavailable. Please try again later."


class ChannelAuthenticatedMessageHandler(Protocol):
    async def handle(self, *, message: str) -> BotResponse: ...


class AttendanceApplication(Protocol):
    async def handle(
        self,
        *,
        message: str,
        mcp_access_token: SecretStr,
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


class AuthenticatedTurnContext(Protocol):
    @property
    def activity(self) -> TurnActivity: ...

    async def send_activity(self, text: str) -> object: ...


class SsoTokenProvider(Protocol):
    async def get_token(self, context: AuthenticatedTurnContext) -> SecretStr: ...


class OboTokenExchange(Protocol):
    async def exchange(self, user_assertion: SecretStr) -> SecretStr: ...


@dataclass(frozen=True, slots=True)
class BotServiceConnectivityHandler:
    """Safe handler used after Bot Service request authentication."""

    async def handle(self, *, message: str) -> BotResponse:
        del message
        return BotResponse(text=_SAFE_BOT_SERVICE_REPLY)


@dataclass(frozen=True, slots=True)
class AuthenticatedAttendanceTurnHandler:
    """Run an admitted personal Teams turn through SSO, OBO, and attendance processing."""

    application: AttendanceApplication
    sso_token_provider: SsoTokenProvider
    obo_token_exchange: OboTokenExchange

    async def handle(self, context: AuthenticatedTurnContext) -> None:
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

    async def _reject_nonpersonal(self, context: AuthenticatedTurnContext) -> None:
        correlation_id = current_correlation_id()
        logger = get_logger("teams.turn").bind(correlation_id=str(correlation_id))
        log_event(
            logger,
            logging.INFO,
            "teams_turn_rejected",
            correlation_id=str(correlation_id),
            outcome="nonpersonal_conversation",
        )
        await context.send_activity("Attendance is available only in a personal chat.")

    async def _reject_blank(self, context: AuthenticatedTurnContext) -> None:
        correlation_id = current_correlation_id()
        logger = get_logger("teams.turn").bind(correlation_id=str(correlation_id))
        log_event(
            logger,
            logging.INFO,
            "teams_turn_rejected",
            correlation_id=str(correlation_id),
            outcome="blank_message",
        )
        await context.send_activity("Please send a message so I can help.")

    async def _handle_attendance_message(
        self, *, context: AuthenticatedTurnContext, message: str
    ) -> None:
        correlation_id = current_correlation_id()
        logger = get_logger("teams.turn").bind(correlation_id=str(correlation_id))
        started_at = perf_counter()
        input_metadata = message_input_metadata(message)
        step = "teams_sso"
        operation_event(
            logger,
            event="operation_started",
            handler=type(self).__name__,
            operation="authenticated_attendance_turn",
            step=step,
            input_metadata=input_metadata,
        )
        try:
            try:
                token_a = await self.sso_token_provider.get_token(context)
            except RuntimeError as error:
                await self._reply_for_authentication_failure(
                    context=context,
                    logger=logger,
                    started_at=started_at,
                    step=step,
                    error=error,
                    input_metadata=input_metadata,
                    event="teams_sso_token_unavailable",
                )
                return
            self._step_completed(logger, started_at, step, input_metadata)

            step = "delegated_obo"
            try:
                token_b = await self.obo_token_exchange.exchange(token_a)
            except DelegatedAuthenticationUnavailable as error:
                await self._reply_for_authentication_failure(
                    context=context,
                    logger=logger,
                    started_at=started_at,
                    step=step,
                    error=error,
                    input_metadata=input_metadata,
                    event="teams_obo_exchange_failed",
                )
                return
            self._step_completed(logger, started_at, step, input_metadata)

            step = "attendance_application"
            response = await self.application.handle(
                message=message,
                mcp_access_token=token_b,
                display_name=self._display_name(context),
            )
            self._step_completed(logger, started_at, step, input_metadata)

            step = "teams_reply_delivery"
            try:
                await context.send_activity(response.text)
            except Exception as error:
                log_event(
                    logger,
                    logging.ERROR,
                    "teams_reply_send_failed",
                    correlation_id=str(correlation_id),
                    error_type=type(error).__name__,
                )
                raise
            operation_event(
                logger,
                event="operation_succeeded",
                handler=type(self).__name__,
                operation="authenticated_attendance_turn",
                step=step,
                duration_ms=_duration_ms(started_at),
                input_metadata=input_metadata,
            )
        except asyncio.CancelledError:
            operation_event(
                logger,
                event="operation_cancelled",
                handler=type(self).__name__,
                operation="authenticated_attendance_turn",
                step=step,
                duration_ms=_duration_ms(started_at),
                input_metadata=input_metadata,
            )
            raise
        except Exception as error:
            self._failed(logger, started_at, step, error, input_metadata)
            raise

    async def _reply_for_authentication_failure(
        self,
        *,
        context: AuthenticatedTurnContext,
        logger: object,
        started_at: float,
        step: str,
        error: RuntimeError | DelegatedAuthenticationUnavailable,
        input_metadata: dict[str, object],
        event: str,
    ) -> None:
        log_event(
            logger,  # type: ignore[arg-type]
            logging.WARNING,
            event,
            correlation_id=str(current_correlation_id()),
            error_type=type(error).__name__,
        )
        self._failed(logger, started_at, step, error, input_metadata)
        await context.send_activity(_SAFE_AUTHENTICATION_REPLY)

    def _step_completed(
        self, logger: object, started_at: float, step: str, input_metadata: dict[str, object]
    ) -> None:
        operation_event(
            logger,  # type: ignore[arg-type]
            event="operation_step_completed",
            handler=type(self).__name__,
            operation="authenticated_attendance_turn",
            step=step,
            duration_ms=_duration_ms(started_at),
            input_metadata=input_metadata,
        )

    def _failed(
        self,
        logger: object,
        started_at: float,
        step: str,
        error: Exception,
        input_metadata: dict[str, object],
    ) -> None:
        operation_event(
            logger,  # type: ignore[arg-type]
            event="operation_failed",
            handler=type(self).__name__,
            operation="authenticated_attendance_turn",
            step=step,
            duration_ms=_duration_ms(started_at),
            error_type=type(error).__name__,
            input_metadata=input_metadata,
        )

    @staticmethod
    def _display_name(context: AuthenticatedTurnContext) -> str | None:
        sender = context.activity.from_property
        return sender.name if sender is not None else None


def _duration_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))
