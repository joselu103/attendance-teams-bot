from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ServerConfiguration:
    host: str = "127.0.0.1"
    port: int = 3978
    app_import: str = "attendance_teams_bot.asgi:app"
