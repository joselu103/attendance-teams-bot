import asyncio
from dataclasses import dataclass

import pytest

import attendance_teams_bot.memory.database as database
from attendance_teams_bot.memory.database import conversation_memory_lifespan


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class RecoveringMemory:
    available: bool = False
    start_calls: int = 0
    cleanup_calls: int = 0
    close_calls: int = 0

    async def start(self) -> bool:
        self.start_calls += 1
        return False

    async def cleanup_expired(self) -> int:
        self.cleanup_calls += 1
        return 0

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.anyio
async def test_unavailable_memory_retries_on_the_short_recovery_cadence(monkeypatch) -> None:
    memory = RecoveringMemory()
    delays: list[int] = []

    async def stop_after_two_retries(seconds: int) -> None:
        delays.append(seconds)
        if len(delays) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(database.asyncio, "sleep", stop_after_two_retries)

    with pytest.raises(asyncio.CancelledError):
        await database._maintenance(memory)  # type: ignore[arg-type]

    assert memory.start_calls == 2
    assert memory.cleanup_calls == 0
    assert delays == [60, 60]


@pytest.mark.anyio
async def test_lifespan_cancels_maintenance_and_closes_unavailable_memory(monkeypatch) -> None:
    memory = RecoveringMemory()
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def waiting_maintenance(_memory: RecoveringMemory) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(database, "_maintenance", waiting_maintenance)

    async with conversation_memory_lifespan(memory):  # type: ignore[arg-type]
        await entered.wait()

    assert memory.start_calls == 1
    assert cancelled.is_set()
    assert memory.close_calls == 1
