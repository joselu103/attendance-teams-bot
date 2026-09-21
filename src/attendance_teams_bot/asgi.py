import logging

import structlog

from attendance_teams_bot.composition import create_http_app
from attendance_teams_bot.observability import configure_logging
from attendance_teams_bot.settings import Settings

_LOGGER = structlog.get_logger(__name__)

settings = Settings()
configure_logging(environment=settings.log_environment)
_LOGGER.log(
    logging.INFO,
    "application_started",
    runtime_mode=settings.mode.value,
    attendance_integration_enabled=settings.attendance_integration_enabled,
    log_environment=settings.log_environment,
    app_version=settings.app_version,
)
app = create_http_app(settings)
