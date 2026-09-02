import logging

from attendance_teams_bot.composition import create_http_app
from attendance_teams_bot.observability import configure_logging, get_logger, log_event
from attendance_teams_bot.settings import Settings

settings = Settings()
configure_logging(level=settings.log_level)
log_event(
    get_logger("startup"),
    logging.INFO,
    "application_started",
    runtime_mode=settings.mode.value,
    attendance_integration_enabled=settings.attendance_integration_enabled,
    log_level=settings.log_level,
    app_version=settings.app_version,
)
app = create_http_app(settings)
