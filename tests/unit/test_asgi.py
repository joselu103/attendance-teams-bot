from fastapi import FastAPI

from attendance_teams_bot.asgi import app


def test_asgi_module_exports_fastapi_application() -> None:
    assert isinstance(app, FastAPI)
