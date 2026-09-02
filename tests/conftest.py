import os

# ASGI modules create their application during import. Keep test collection independent
# from a developer's local `.env` while individual tests opt into Teams mode explicitly.
os.environ["BOT_RUNTIME_MODE"] = "local"
os.environ["ATTENDANCE_INTEGRATION_ENABLED"] = "false"
