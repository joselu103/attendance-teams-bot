# Attendance Teams Bot

A Python service that will receive Microsoft Teams messages and act as an
LLM-powered, authenticated MCP client for Attendance CRMT.

The bot is intentionally separate from the Attendance CRMT service. It must not
access the attendance database or implement attendance authorization rules.

## Current runtime slices

The service has two deliberately separate runtime modes. The local developer
entry point listens on `127.0.0.1:3978`; the container entry point listens on
`0.0.0.0:8080` within its container network:

| Mode | Purpose | Request authentication |
| --- | --- | --- |
| `local` (default) | Offline connectivity and activity-adapter development | None — never configure this route as a real Bot Service callback. |
| `teams` | Bot Service callback foundation | Microsoft Agents SDK validates the Bot Service bearer credential before application handling. |

The authenticated `teams` mode currently returns only a fixed connectivity
reply. It does not perform Teams user SSO, OBO, MCP calls, LLM invocation, or
attendance-data access. A valid Bot Service request proves the channel path; it
does **not** yet prove the sender is an authorized Attendance CRMT employee.

The Teams/Entra user-token flow, MCP endpoint, and LLM provider remain
intentionally unconfigured until the cross-repository integration contract is
implemented.

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
devtunnel host attendance-teams-bot-dev.eun1
```

Stopping the command with `Ctrl+C` stops public forwarding. The tunnel port uses
the local `http` protocol because Uvicorn serves HTTP on `127.0.0.1:3978`; Dev
Tunnel terminates TLS and exposes the public `https` URL. Do not host the
`teams` mode publicly until its real single-tenant Bot Service settings have
been configured and the local fail-closed check has passed.

## Verification

```bash
env -u PYTHONPATH uv run pytest
env -u PYTHONPATH uv run ruff check .
env -u PYTHONPATH uv run ruff format --check .
env -u PYTHONPATH uv run mypy
```

## Configuration

`BOT_RUNTIME_MODE` defaults to `local`. Set it to `teams` only for the
single-tenant Bot Service callback runtime.

`Settings` is the single configuration boundary: it reads the environment and
optional local `.env` file once, validates every supported value, and passes a
typed Teams connection to the Microsoft SDK adapter. Unknown dotenv variables
are rejected rather than silently ignored.

Teams mode requires these environment-variable names:

```bash
BOT_RUNTIME_MODE=teams
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID=<bot-client-id>
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID=<tenant-id>
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET=<development-secret>
```

Use an untracked local `.env` file for development and a platform secret manager
for deployment. Never put real values, bearer tokens, or client secrets in
`.env.example`, source control, logs, test fixtures, or chat.

`CLIENTID` and `TENANTID` identify the Bot Service application and tenant;
`CLIENTSECRET` is sensitive. The authenticated runtime currently verifies the
Bot Service request only. It does not obtain a Teams user token or contact
Attendance CRMT.

## Container

Build the production image locally:

```bash
docker build --tag attendance-teams-bot:dev .
```

For a local authenticated probe, load values from the ignored `.env` file and
expose the container only on the local loopback interface:

```bash
docker run --rm \
  --env-file .env \
  --publish 127.0.0.1:8080:8080 \
  attendance-teams-bot:dev
```

The Docker build context excludes `.env` and `.env.*`. In AWS App Runner, set
the runtime mode and non-secret identifiers as environment variables and inject
only `CLIENTSECRET` from AWS Secrets Manager; never bake it into the image.

## Layout

- `src/attendance_teams_bot/application.py`: transport-independent bot behavior.
- `src/attendance_teams_bot/local.py`: safe handler used before external adapters exist.
- `src/attendance_teams_bot/composition.py`: local runtime dependency wiring.
- `src/attendance_teams_bot/asgi.py`: exported FastAPI application.
- `src/attendance_teams_bot/server.py`: Uvicorn process entry point.
- `src/attendance_teams_bot/teams/`: Teams activity parsing and HTTP transport.
- `tests/unit/`: application, handler, ASGI, and server behavior.
- `tests/integration/`: complete ASGI request-path behavior.
