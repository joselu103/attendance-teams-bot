import asyncio
import os
from uuid import uuid4

import asyncpg
import pytest

from attendance_teams_bot.memory.database import _SCHEMA_LOCK, PostgresConversationMemory


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_postgres_memory_persists_orders_and_trims_synthetic_messages() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is required for synthetic PostgreSQL verification")
    first = PostgresConversationMemory(database_url)
    second = PostgresConversationMemory(database_url)
    # Independent owners may start together; schema creation is coordinated by
    # the database, not a process-local lock.
    assert all(await asyncio.gather(first.start(), second.start()))
    try:
        session = f"synthetic-session-{uuid4()}"
        other_session = f"synthetic-other-{uuid4()}"
        for index in range(12):
            await first.save_exchange(session, f"user {index}", f"guide {index}")
        await first.save_message(other_session, "user", "isolated")

        history = await second.get_chat_history(session, limit=20)

        assert len(history) == 20
        assert history[0].content == "user 2"
        assert history[-1].content == "guide 11"
        assert [message.role for message in history[:2]] == ["user", "assistant"]
        assert [message.content for message in await first.get_chat_history(other_session)] == [
            "isolated"
        ]

        long_content = "x" * 4_001
        await first.save_message(session, "assistant", long_content)
        assert (await first.get_chat_history(session, 1))[0].content.endswith(
            "... [truncated for memory]"
        )

        pool = first._pool
        assert pool is not None
        async with pool.acquire() as connection:
            indexes = await connection.fetch(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'chat_messages'"
            )
            assert {row["indexname"] for row in indexes} >= {
                "chat_messages_session_created_idx",
                "chat_messages_created_idx",
            }
            await connection.execute(
                "UPDATE chat_messages SET created_at = "
                "NOW() - INTERVAL '7 days' - INTERVAL '1 second' "
                "WHERE session_id = $1",
                other_session,
            )
        assert await first.get_chat_history(other_session) == ()
        assert await first.cleanup_expired() >= 1

        concurrent_session = f"synthetic-concurrent-{uuid4()}"
        await asyncio.gather(
            *(
                first.save_exchange(concurrent_session, f"user {index}", f"guide {index}")
                for index in range(16)
            )
        )
        concurrent_history = await first.get_chat_history(concurrent_session, 20)
        assert len(concurrent_history) == 20
        assert all(message.role in {"user", "assistant"} for message in concurrent_history)
        with pytest.raises(ValueError):
            await first.get_chat_history(session, 0)
        with pytest.raises(ValueError):
            await first.save_message(session, "system", "not permitted")  # type: ignore[arg-type]
    finally:
        await first.close()
        await second.close()


@pytest.mark.anyio
async def test_cancelled_schema_start_releases_its_pool_and_allows_retry() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is required for synthetic PostgreSQL verification")
    blocker = await asyncpg.connect(database_url)
    memory = PostgresConversationMemory(database_url)
    await blocker.execute("SELECT pg_advisory_lock($1)", _SCHEMA_LOCK)
    try:
        starting = asyncio.create_task(memory.start())
        await asyncio.sleep(0.05)
        starting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await starting
        assert memory.available is False
    finally:
        await blocker.execute("SELECT pg_advisory_unlock($1)", _SCHEMA_LOCK)
        await blocker.close()

    assert await memory.start() is True
    await memory.close()
