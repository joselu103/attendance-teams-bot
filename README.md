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

Teams mode has two explicit, fail-closed paths:

```text
Integration disabled:
Teams → authenticated Bot Service callback → connectivity reply

Integration enabled:
Teams → Azure Bot OAuth connection → token A → OBO → token B
→ Attendance CRMT /mcp → requester-scoped response
```

The default is integration disabled. A valid Bot Service request proves the
channel path; it does **not** prove the sender is an authorized Attendance CRMT
employee. The enabled path must not be activated until the separately deployed
Attendance CRMT service exposes its Entra API, delegated scope, and HTTPS MCP
contract.

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
Bot Service request only unless attendance integration is explicitly enabled.

The enabled integration requires all of these environment-variable names in
addition to the Teams connection settings:

```bash
ATTENDANCE_INTEGRATION_ENABLED=true
MCP_ENDPOINT=https://<attendance-crmt-host>/mcp
MCP_SCOPE=api://<attendance-crmt-api-app-id>/attendance.access
MCP_TIMEOUT_SECONDS=10
TEAMS_SSO_OAUTH_CONNECTION_NAME=<azure-bot-oauth-connection-name>
OPENAI_API_KEY=<secret-openai-api-key>
OPENAI_MODEL=<explicit-approved-model-name>
```

`TEAMS_SSO_OAUTH_CONNECTION_NAME` obtains the Teams token (token A). The bot
exchanges it only through the configured Microsoft Agents SDK service connection
to obtain the downstream Attendance CRMT token (token B); only token B reaches
the MCP client. Do not configure a downstream Attendance CRMT scope on the Azure
Bot OAuth connection.

`OPENAI_API_KEY` is a secret and `OPENAI_MODEL` is mandatory explicit runtime
configuration; no production model is selected by default. Use an untracked
local `.env` only for development and a deployment secret manager for the key.
`ATTENDANCE_INTEGRATION_ENABLED=false` remains authoritative even if every
Teams, MCP, and OpenAI value is present. When enabled, incomplete Teams, MCP, or
OpenAI configuration fails startup rather than falling back to an attendance route.

### Constrained language-model and MCP policy

The provider-neutral asynchronous `LanguageModel` boundary selects an action;
OpenAI is the first adapter. Replacing OpenAI means implementing `LanguageModel`
and changing provider composition only—not Teams handling, OBO, MCP transport,
or the attendance application policy.

For each accepted personal-chat turn, the bot opens one requester-authenticated
MCP session with token B and discovers the catalog once. It intersects that
advertisement with its frozen first-party catalog before calling the model:
every advertised tool must be configured, unique, schema-compatible, and marked
`readOnlyHint=true`; unknown, duplicate, missing, or writable tools fail closed.
The current frozen catalog contains only `list_my_attendance_events`. Its
bot-owned definition—not remote descriptions or metadata—is the only tool prompt
given to the model, and permits at most one call. The model may supply only
`start_date` and `end_date`; the bot enforces inclusive Europe/Ljubljana calendar
dates, a maximum 31-day range, bounded arguments, and fixed `limit=50` /
`offset=0` pagination. Identity, employee targets, roles, and pagination are
never model-controlled.

The first enabled slice supports only personal one-to-one chats. Group, meeting,
channel, missing, and unknown conversation scope stop before SSO, OBO, OpenAI,
or MCP. The bot calls Attendance CRMT MCP contract
[`1.2.0`](../attendance-crmt/docs/integrations/teams-bot-mcp-auth-contract.md)
and renders validated attendance locally and deterministically. Attendance
records are deliberately **not** sent to OpenAI, avoiding a second model/tool
loop and reducing employee-data exposure. Tokens, employee authority, arbitrary
tools, provider or MCP diagnostics, internal identifiers, and notes do not enter
model prompts or Teams replies. Stable tool failures map to fixed safe replies.

This verified client-side behavior does not establish a real integration. Real
attendance traffic remains disabled by default pending the Attendance CRMT
non-production HTTPS `/mcp` deployment, Entra `attendance.access` API
registration, delegated OBO consent, certificate configuration, end-to-end
identity/data verification, and the required organizational privacy/provider and
development-notice approvals.

## Container

Build the production image with immutable source provenance locally:

```bash
SHA="$(git rev-parse HEAD)"
SHORT_SHA="$(git rev-parse --short=12 HEAD)"
docker build --build-arg VCS_REF="$SHA" \
  --tag "attendance-teams-bot:$SHORT_SHA" .
```

The image publishes the full revision through the OCI
`org.opencontainers.image.revision` label and `APP_VERSION` startup-log field.
Deploy a commit-derived image tag or registry digest, never a floating development
tag. See [Teams bot diagnostics](docs/operations/teams-bot-diagnostics.md) for
secret-safe Container Apps and Teams troubleshooting.

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

- `src/attendance_teams_bot/agent/`: provider-neutral model contract, OpenAI adapter, and one-shot policy.
- `src/attendance_teams_bot/mcp/`: authenticated Streamable HTTP MCP session and contracts.
- `src/attendance_teams_bot/local.py`: safe handler used before external adapters exist.
- `src/attendance_teams_bot/composition.py`: local runtime dependency wiring.
- `src/attendance_teams_bot/asgi.py`: exported FastAPI application.
- `src/attendance_teams_bot/server.py`: Uvicorn process entry point.
- `src/attendance_teams_bot/teams/`: Teams activity parsing and HTTP transport.
- `tests/unit/`: application, handler, ASGI, and server behavior.
- `tests/integration/`: complete ASGI request-path behavior.
