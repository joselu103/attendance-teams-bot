from attendance_teams_bot.server import ServerConfiguration


def test_server_configuration_defaults_to_local_development_port() -> None:
    configuration = ServerConfiguration()

    assert configuration.host == "127.0.0.1"
    assert configuration.port == 3978
    assert configuration.app_import == "attendance_teams_bot.asgi:app"
