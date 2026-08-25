from attendance_teams_bot.composition import create_http_app
from attendance_teams_bot.settings import Settings

app = create_http_app(Settings())
