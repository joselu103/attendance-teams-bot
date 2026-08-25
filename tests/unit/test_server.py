import sys
from types import SimpleNamespace

from attendance_teams_bot.server import ServerConfiguration, main, run_server


def test_server_configuration_defaults_to_local_development_port() -> None:
    configuration = ServerConfiguration()

    assert configuration.host == "127.0.0.1"
    assert configuration.port == 3978
    assert configuration.app_import == "attendance_teams_bot.asgi:app"


def test_run_server_uses_the_local_configuration() -> None:
    calls: list[tuple[str, str, int]] = []

    def record_run(app_import: str, *, host: str, port: int) -> None:
        calls.append((app_import, host, port))

    run_server(record_run)

    assert calls == [("attendance_teams_bot.asgi:app", "127.0.0.1", 3978)]


def test_main_starts_uvicorn_with_the_local_configuration(monkeypatch) -> None:
    calls: list[tuple[str, str, int]] = []

    def record_run(app_import: str, *, host: str, port: int) -> None:
        calls.append((app_import, host, port))

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=record_run))

    main()

    assert calls == [("attendance_teams_bot.asgi:app", "127.0.0.1", 3978)]
