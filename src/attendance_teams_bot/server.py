from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ServerConfiguration:
    host: str = "127.0.0.1"
    port: int = 3978
    app_import: str = "attendance_teams_bot.asgi:app"


class ServerRunner(Protocol):
    def __call__(self, app_import: str, *, host: str, port: int) -> None: ...


def run_server(runner: ServerRunner) -> None:
    configuration = ServerConfiguration()
    runner(
        configuration.app_import,
        host=configuration.host,
        port=configuration.port,
    )
