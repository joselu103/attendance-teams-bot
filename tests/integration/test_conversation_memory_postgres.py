import asyncio
import os
from datetime import date
from uuid import uuid4

import asyncpg
import pytest

import attendance_teams_bot.memory.database as database
from attendance_teams_bot.agent.continuation import HistoryContinuation
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


@pytest.mark.anyio
async def test_exchange_insert_is_atomic_when_assistant_insert_fails() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is required for synthetic PostgreSQL verification")
    memory = PostgresConversationMemory(database_url)
    assert await memory.start()
    session = f"synthetic-atomic-{uuid4()}"
    pool = memory._pool
    assert pool is not None
    try:
        async with pool.acquire() as connection:
            await connection.execute(
                """
                CREATE OR REPLACE FUNCTION reject_synthetic_assistant() RETURNS trigger AS $$
                BEGIN
                    IF NEW.role = 'assistant' THEN
                        RAISE EXCEPTION 'synthetic assistant failure';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
                """
            )
            await connection.execute(
                """
                CREATE TRIGGER reject_synthetic_assistant_trigger
                BEFORE INSERT ON chat_messages
                FOR EACH ROW EXECUTE FUNCTION reject_synthetic_assistant()
                """
            )

        await memory.save_exchange(session, "user turn", "assistant framing")

        assert await memory.get_chat_history(session) == ()
    finally:
        async with pool.acquire() as connection:
            await connection.execute(
                "DROP TRIGGER IF EXISTS reject_synthetic_assistant_trigger ON chat_messages"
            )
            await connection.execute("DROP FUNCTION IF EXISTS reject_synthetic_assistant()")
        await memory.close()


@pytest.mark.anyio
async def test_maintenance_drains_more_than_one_cleanup_batch(monkeypatch) -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is required for synthetic PostgreSQL verification")
    memory = PostgresConversationMemory(database_url)
    assert await memory.start()
    pool = memory._pool
    assert pool is not None
    session = f"synthetic-expired-{uuid4()}"
    slept_after_cleanup = asyncio.Event()
    maintenance: asyncio.Task[None] | None = None

    async def wait_after_cleanup(_seconds: float) -> None:
        slept_after_cleanup.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(database.asyncio, "sleep", wait_after_cleanup)
    try:
        async with pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO chat_messages (session_id, role, content, created_at)
                VALUES ($1, 'user', 'expired', NOW() - INTERVAL '8 days')
                """,
                [(session,) for _ in range(1_001)],
            )

        maintenance = asyncio.create_task(database._maintenance(memory))
        await asyncio.wait_for(slept_after_cleanup.wait(), timeout=2)
        async with pool.acquire() as connection:
            remaining = await connection.fetchval(
                "SELECT COUNT(*) FROM chat_messages WHERE session_id = $1", session
            )
        assert remaining == 0
        maintenance.cancel()
        with pytest.raises(asyncio.CancelledError):
            await maintenance
    finally:
        if maintenance is not None and not maintenance.done():
            maintenance.cancel()
            with pytest.raises(asyncio.CancelledError):
                await maintenance
        await memory.close()


@pytest.mark.anyio
async def test_active_continuation_is_leased_then_advanced_or_cleared() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is required for synthetic PostgreSQL verification")
    memory = PostgresConversationMemory(database_url)
    assert await memory.start()
    session, conversation = f"synthetic-{uuid4()}", f"conversation-{uuid4()}"
    first = HistoryContinuation("self", date(2026, 1, 1), date(2026, 1, 31), 50, "en")
    second = HistoryContinuation("self", date(2026, 1, 1), date(2026, 1, 31), 100, "en")
    try:
        identifier = await memory.replace_continuation(session, conversation, first)
        assert identifier is not None
        claimed = await memory.claim_continuation(session, conversation, identifier)
        assert claimed is not None
        assert claimed.query == first
        assert await memory.claim_continuation(session, conversation) is None

        next_identifier = await memory.finish_continuation(claimed, session, conversation, second)
        assert next_identifier is not None and next_identifier != identifier
        assert await memory.claim_continuation(session, conversation, identifier) is None
        final = await memory.claim_continuation(session, conversation, next_identifier)
        assert final is not None and final.query == second
        assert await memory.finish_continuation(final, session, conversation, None) is None
        assert await memory.claim_continuation(session, conversation) is None
    finally:
        await memory.close()
