from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Protocol, TypeVar, cast
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
import structlog

from attendance_teams_bot.memory.models import ChatMessage, ChatRole

MAX_CONTENT_LENGTH = 4_000
MEMORY_SUFFIX = "... [truncated for memory]"
DEFAULT_HISTORY_LIMIT = 10
MAX_HISTORY_LIMIT = 20
RETENTION_DAYS = 7
OPERATION_TIMEOUT_SECONDS = 2.0
_SCHEMA_LOCK = 8_441_270_091
_CLEANUP_LOCK = 8_441_270_092
_LOGGER = structlog.get_logger(__name__)
_Result = TypeVar("_Result")


class ConversationMemory(Protocol):
    async def get_chat_history(
        self, session_id: str, limit: int = DEFAULT_HISTORY_LIMIT
    ) -> tuple[ChatMessage, ...]: ...

    async def save_message(self, session_id: str, role: ChatRole, content: str) -> None: ...

    async def save_exchange(
        self, session_id: str, user_content: str, assistant_content: str
    ) -> None: ...


class StatelessConversationMemory:
    """No-op memory used for absent configuration and temporary database outages."""

    async def get_chat_history(
        self, session_id: str, limit: int = DEFAULT_HISTORY_LIMIT
    ) -> tuple[ChatMessage, ...]:
        del session_id, limit
        return ()

    async def save_message(self, session_id: str, role: ChatRole, content: str) -> None:
        del session_id, role, content

    async def save_exchange(
        self, session_id: str, user_content: str, assistant_content: str
    ) -> None:
        del session_id, user_content, assistant_content


def memory_session_id(tenant_id: str | None, aad_object_id: str | None) -> str | None:
    """Return an opaque, versioned key only for canonical authenticated identifiers."""
    if not isinstance(tenant_id, str) or not isinstance(aad_object_id, str):
        return None
    try:
        tenant = str(UUID(tenant_id.strip()))
        user = str(UUID(aad_object_id.strip()))
    except ValueError:
        return None
    # Stable opaque identifiers are deliberately never logged or returned to callers.
    payload = f"teams-memory:v1:{tenant}:{user}".encode()
    return hashlib.sha256(payload).hexdigest()


def truncate_for_memory(content: str) -> str:
    """Bound stored content without altering the live turn or Teams response."""
    if len(content) <= MAX_CONTENT_LENGTH:
        return content
    return content[:MAX_CONTENT_LENGTH] + MEMORY_SUFFIX


class PostgresConversationMemory:
    """Optional asyncpg-backed storage with short, bounded database operations."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None
        # Serialise lifecycle transitions without holding a database connection
        # while a caller is only reading or writing conversation history.
        self._lifecycle_lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self._pool is not None

    async def start(self) -> bool:
        async with self._lifecycle_lock:
            if self._pool is not None:
                return True
            pool: asyncpg.Pool | None = None
            try:
                pool = await asyncio.wait_for(
                    asyncpg.create_pool(self._database_url, min_size=1, max_size=5),
                    timeout=OPERATION_TIMEOUT_SECONDS,
                )
                await self._bounded(self._initialize_schema_for(pool))
                # Do not publish a pool until every required schema operation succeeds.
                self._pool = pool
                return True
            except asyncio.CancelledError:
                if pool is not None:
                    await self._dispose(pool)
                raise
            except Exception as error:
                _LOGGER.warning("conversation_memory_unavailable", error_type=type(error).__name__)
                if pool is not None:
                    await self._dispose(pool)
                return False

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if self._pool is not None:
                pool, self._pool = self._pool, None
                await self._dispose(pool)

    async def get_chat_history(
        self, session_id: str, limit: int = DEFAULT_HISTORY_LIMIT
    ) -> tuple[ChatMessage, ...]:
        if not 1 <= limit <= MAX_HISTORY_LIMIT:
            raise ValueError("history limit must be between 1 and 20")
        pool = self._pool
        if pool is None:
            return ()
        try:
            rows = await self._bounded(
                pool.fetch(
                    """
                    SELECT role, content, created_at FROM chat_messages
                    WHERE session_id = $1 AND created_at > NOW() - INTERVAL '7 days'
                    ORDER BY created_at DESC, id DESC LIMIT $2
                    """,
                    session_id,
                    limit,
                )
            )
            return tuple(
                ChatMessage(row["role"], row["content"], row["created_at"])
                for row in reversed(rows)
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _LOGGER.warning("conversation_memory_read_failed", error_type=type(error).__name__)
            return ()

    async def save_message(self, session_id: str, role: ChatRole, content: str) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("invalid chat role")
        await self._save(session_id, ((role, content),))

    async def save_exchange(
        self, session_id: str, user_content: str, assistant_content: str
    ) -> None:
        await self._save(session_id, (("user", user_content), ("assistant", assistant_content)))

    async def cleanup_expired(self) -> int:
        pool = self._pool
        if pool is None:
            return 0
        try:
            return await self._bounded(self._cleanup(pool))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _LOGGER.warning("conversation_memory_cleanup_failed", error_type=type(error).__name__)
            return 0

    async def _save(self, session_id: str, messages: tuple[tuple[ChatRole, str], ...]) -> None:
        pool = self._pool
        if pool is None:
            return
        try:
            await self._bounded(self._save_transaction(pool, session_id, messages))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _LOGGER.warning("conversation_memory_write_failed", error_type=type(error).__name__)

    async def _save_transaction(
        self, pool: asyncpg.Pool, session_id: str, messages: tuple[tuple[ChatRole, str], ...]
    ) -> None:
        async with pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", session_id
            )
            for role, content in messages:
                await connection.execute(
                    "INSERT INTO chat_messages (session_id, role, content) VALUES ($1, $2, $3)",
                    session_id,
                    role,
                    truncate_for_memory(content),
                )
            await connection.execute(
                """
                DELETE FROM chat_messages WHERE id IN (
                    SELECT id FROM chat_messages WHERE session_id = $1
                    ORDER BY created_at DESC, id DESC OFFSET $2
                )
                """,
                session_id,
                MAX_HISTORY_LIMIT,
            )

    async def _initialize_schema_for(self, pool: asyncpg.Pool) -> None:
        async with pool.acquire() as connection:
            # Transaction-scoped advisory locks are released even when startup is
            # cancelled or DDL fails, before this connection can return to the pool.
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock($1)", _SCHEMA_LOCK)
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_messages (
                        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                        content TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS chat_messages_session_created_idx "
                    "ON chat_messages (session_id, created_at DESC)"
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS chat_messages_created_idx "
                    "ON chat_messages (created_at)"
                )

    async def _cleanup(self, pool: asyncpg.Pool) -> int:
        async with pool.acquire() as connection:
            async with connection.transaction():
                locked = cast(
                    bool,
                    await connection.fetchval(
                        "SELECT pg_try_advisory_xact_lock($1)", _CLEANUP_LOCK
                    ),
                )
                if not locked:
                    return 0
                result = await connection.execute(
                    """
                    DELETE FROM chat_messages WHERE id IN (
                        SELECT id FROM chat_messages
                        WHERE created_at <= NOW() - INTERVAL '7 days'
                        ORDER BY id LIMIT 1000
                    )
                    """
                )
                return int(cast(str, result).rsplit(" ", maxsplit=1)[-1])

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("conversation memory is unavailable")
        return self._pool

    @staticmethod
    async def _bounded(awaitable: Awaitable[_Result]) -> _Result:
        return await asyncio.wait_for(awaitable, timeout=OPERATION_TIMEOUT_SECONDS)

    @staticmethod
    async def _dispose(pool: asyncpg.Pool) -> None:
        try:
            await asyncio.wait_for(pool.close(), timeout=OPERATION_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            pool.terminate()
            raise
        except Exception:
            pool.terminate()


@asynccontextmanager
async def conversation_memory_lifespan(
    memory: PostgresConversationMemory | None,
) -> AsyncIterator[None]:
    """Own startup, periodic retry/cleanup, and safe shutdown for optional memory."""
    task: asyncio.Task[None] | None = None
    if memory is not None:
        await memory.start()
        task = asyncio.create_task(_maintenance(memory))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if memory is not None:
            await memory.close()


async def _maintenance(memory: PostgresConversationMemory) -> None:
    while True:
        try:
            if not memory.available:
                await memory.start()
                await asyncio.sleep(60)
                continue
            if memory.available:
                while await memory.cleanup_expired() == 1000:
                    pass
            await asyncio.sleep(60 * 60)
        except asyncio.CancelledError:
            raise
