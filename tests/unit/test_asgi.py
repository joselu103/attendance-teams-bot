import importlib
from types import SimpleNamespace

import structlog
from fastapi import FastAPI

import attendance_teams_bot.asgi as asgi
import attendance_teams_bot.composition as composition
import attendance_teams_bot.observability as observability
import attendance_teams_bot.settings as settings
from attendance_teams_bot.asgi import app
from attendance_teams_bot.settings import RuntimeMode


def test_asgi_module_configures_logging_before_runtime_composition(monkeypatch) -> None:
    runtime_settings = SimpleNamespace(
        log_environment="staging",
        app_version="test-sha",
        mode=RuntimeMode.LOCAL,
        attendance_integration_enabled=False,
    )
    calls: list[object] = []
    events: list[tuple[object, ...]] = []

    class RecordingLogger:
        def log(self, _level: int, event: str, **fields: object) -> None:
            events.append((event, fields))

    def record_create_http_app(received_settings: object) -> FastAPI:
        calls.append(received_settings)
        return FastAPI()

    with monkeypatch.context() as scoped_monkeypatch:
        scoped_monkeypatch.setattr(settings, "Settings", lambda: runtime_settings)
        scoped_monkeypatch.setattr(composition, "create_http_app", record_create_http_app)
        scoped_monkeypatch.setattr(
            observability,
            "configure_logging",
            lambda *, environment: events.append(("configure", environment)),
        )
        scoped_monkeypatch.setattr(structlog, "get_logger", lambda _name: RecordingLogger())
        importlib.reload(asgi)

        assert calls == [runtime_settings]
        assert events == [
            ("configure", "staging"),
            (
                "application_started",
                {
                    "runtime_mode": "local",
                    "attendance_integration_enabled": False,
                    "log_environment": "staging",
                    "app_version": "test-sha",
                },
            ),
        ]

    importlib.reload(asgi)


def test_asgi_module_exports_fastapi_application() -> None:
    assert isinstance(app, FastAPI)
