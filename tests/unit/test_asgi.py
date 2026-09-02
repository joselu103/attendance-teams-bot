import importlib
from types import SimpleNamespace

from fastapi import FastAPI

import attendance_teams_bot.asgi as asgi
import attendance_teams_bot.composition as composition
import attendance_teams_bot.observability as observability
import attendance_teams_bot.settings as settings
from attendance_teams_bot.asgi import app
from attendance_teams_bot.settings import RuntimeMode


def test_asgi_module_configures_logging_before_runtime_composition(monkeypatch) -> None:
    runtime_settings = SimpleNamespace(
        log_level="WARNING",
        app_version="test-sha",
        mode=RuntimeMode.LOCAL,
        attendance_integration_enabled=False,
    )
    calls: list[object] = []
    events: list[tuple[object, ...]] = []

    def record_create_http_app(received_settings: object) -> FastAPI:
        calls.append(received_settings)
        return FastAPI()

    with monkeypatch.context() as scoped_monkeypatch:
        scoped_monkeypatch.setattr(settings, "Settings", lambda: runtime_settings)
        scoped_monkeypatch.setattr(composition, "create_http_app", record_create_http_app)
        scoped_monkeypatch.setattr(
            observability,
            "configure_logging",
            lambda *, level: events.append(("configure", level)),
        )
        scoped_monkeypatch.setattr(
            observability,
            "log_event",
            lambda _logger, _level, event, **fields: events.append((event, fields)),
        )
        importlib.reload(asgi)

        assert calls == [runtime_settings]
        assert events == [
            ("configure", "WARNING"),
            (
                "application_started",
                {
                    "runtime_mode": "local",
                    "attendance_integration_enabled": False,
                    "log_level": "WARNING",
                    "app_version": "test-sha",
                },
            ),
        ]

    importlib.reload(asgi)


def test_asgi_module_exports_fastapi_application() -> None:
    assert isinstance(app, FastAPI)
