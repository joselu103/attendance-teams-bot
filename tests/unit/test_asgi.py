import importlib

from fastapi import FastAPI

import attendance_teams_bot.asgi as asgi
import attendance_teams_bot.composition as composition
import attendance_teams_bot.settings as settings
from attendance_teams_bot.asgi import app


def test_asgi_module_uses_runtime_composition(monkeypatch) -> None:
    runtime_settings = object()
    calls: list[object] = []

    def record_create_http_app(received_settings: object) -> FastAPI:
        calls.append(received_settings)
        return FastAPI()

    with monkeypatch.context() as scoped_monkeypatch:
        scoped_monkeypatch.setattr(settings, "RuntimeSettings", lambda: runtime_settings)
        scoped_monkeypatch.setattr(composition, "create_http_app", record_create_http_app)
        importlib.reload(asgi)

        assert calls == [runtime_settings]

    importlib.reload(asgi)


def test_asgi_module_exports_fastapi_application() -> None:
    assert isinstance(app, FastAPI)
