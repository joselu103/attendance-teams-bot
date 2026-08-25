# Attendance Teams Bot

A Python service that will receive Microsoft Teams messages and act as an
LLM-powered, authenticated MCP client for Attendance CRMT.

The bot is intentionally separate from the Attendance CRMT service. It must not
access the attendance database or implement attendance authorization rules.

## Current local slice

The service has a dependency-injected application boundary and protocols for
Teams, identity, MCP, and LLM adapters. The local runtime composes a safe,
connectivity-only handler through the Teams activity adapter and FastAPI route
factory. It validates message-shaped payloads, ignores non-message activities,
and returns an explicit response that external adapters are not configured.

The local runtime listens only on `127.0.0.1:3978` and exposes
`POST /api/messages`. It does not authenticate Teams requests and it never
creates tokens, calls MCP, invokes an LLM, or returns attendance data.

The real Microsoft Entra token flow, MCP endpoint, and LLM provider remain
intentionally unconfigured until the cross-repository integration contract is
agreed.

## Quick start

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
env -u PYTHONPATH uv run attendance-teams-bot
```

## Run the local HTTP service

Start the local service in one terminal:

```bash
env -u PYTHONPATH uv run attendance-teams-bot-http
```

In a second terminal, send a harmless local activity:

```bash
curl --fail --silent --show-error \
  -X POST http://127.0.0.1:3978/api/messages \
  -H 'Content-Type: application/json' \
  -d '{"type":"message","id":"local-1","from":{"id":"local-user"},"text":"Hello"}'
```

The response must say that authentication, MCP, and LLM adapters are not
configured. Stop the local service with `Ctrl+C`.

### Dev Tunnel, after local verification

Dev Tunnel forwards traffic to the already-running local service; it does not
start the bot. Only after the local probe succeeds, run this in a separate
terminal and keep it open:

```bash
devtunnel host attendance-teams-bot-dev -p 3978
```

Stopping the command with `Ctrl+C` stops public forwarding. The current route
is connectivity-only and must not be configured for real attendance access until
Bot Service request authentication exists.

## Verification

```bash
env -u PYTHONPATH uv run pytest
env -u PYTHONPATH uv run ruff check .
env -u PYTHONPATH uv run ruff format --check .
env -u PYTHONPATH uv run mypy
```

## Configuration

Copy `.env.example` to an untracked `.env` only when a real MCP endpoint is
available. Deployed configuration must come from the platform secret manager.
Never put credentials or tokens in `.env.example`, source control, logs, or chat.

## Layout

- `src/attendance_teams_bot/application.py`: transport-independent bot behavior.
- `src/attendance_teams_bot/local.py`: safe handler used before external adapters exist.
- `src/attendance_teams_bot/composition.py`: local runtime dependency wiring.
- `src/attendance_teams_bot/asgi.py`: exported FastAPI application.
- `src/attendance_teams_bot/server.py`: Uvicorn process entry point.
- `src/attendance_teams_bot/teams/`: Teams activity parsing and HTTP transport.
- `tests/unit/`: application, handler, ASGI, and server behavior.
- `tests/integration/`: complete ASGI request-path behavior.
