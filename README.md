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
| `teams` | Bot Service-only callback foundation | Microsoft Agents SDK validates the Bot Service callback credential before application handling. |

Teams mode has two explicit, fail-closed paths:

```text
Integration disabled:
Teams → Bot Service-only callback → connectivity reply

Integration enabled:
Teams → Azure Bot OAuth connection → token A → OBO → token B
→ attendance-mcp /mcp → requester-scoped response
```

The default is integration disabled. A valid Bot Service request proves the
channel path; it does **not** prove the sender is an authorized Attendance CRMT
employee. The enabled path must not be activated until the separately deployed
attendance-mcp endpoint and Attendance REST API Entra configuration are ready.

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

`LOG_ENV` defaults to `local`, which emits colorized DEBUG console logs. Set it
to `staging` or `production` for single-line JSON INFO logs on stdout. Logs carry
a context-bound `trace_id`; trusted callers may additionally bind validated
user/client and session context. Secrets are recursively redacted before output.

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
`CLIENTSECRET` is sensitive. The Bot Service-only runtime verifies the Bot Service
request only unless attendance integration is explicitly enabled.

The enabled integration requires all of these environment-variable names in
addition to the Teams connection settings:

```bash
ATTENDANCE_INTEGRATION_ENABLED=true
MCP_ENDPOINT=https://<attendance-mcp-host>/mcp
MCP_SCOPE=api://<attendance-rest-api-app-id>/attendance.access
MCP_TIMEOUT_SECONDS=10
TEAMS_SSO_OAUTH_CONNECTION_NAME=<azure-bot-oauth-connection-name>
OPENAI_API_KEY=<secret-openai-api-key>
OPENAI_MODEL=<explicit-approved-model-name>
```

`TEAMS_SSO_OAUTH_CONNECTION_NAME` obtains the Teams token (token A). The bot
exchanges it only through the configured Microsoft Agents SDK service connection
to obtain the downstream Attendance REST API token (token B); only token B reaches
the MCP client. Do not configure a downstream Attendance CRMT scope on the Azure
Bot OAuth connection.

`OPENAI_API_KEY` is a secret and `OPENAI_MODEL` is mandatory explicit runtime
configuration; no production model is selected by default. Use an untracked
local `.env` only for development and a deployment secret manager for the key.
`ATTENDANCE_INTEGRATION_ENABLED=false` remains authoritative even if every
Teams, MCP, and OpenAI value is present. When enabled, incomplete Teams, MCP, or
OpenAI configuration fails startup rather than falling back to an attendance route.

### Bounded language-model and MCP policy

The provider-neutral asynchronous `LanguageModel` boundary runs a turn-local,
bounded agent loop; OpenAI is the first adapter. Replacing OpenAI means
implementing `LanguageModel` and changing provider composition only—not Teams
handling, OBO, MCP transport, or attendance policy.

For each accepted personal-chat turn, the bot opens one requester-authenticated
MCP session with token B and discovers the catalog once. It admits only the
established version-1 read-only CRMT tool names; every advertised entry
must be unique, safely formed, and marked `readOnlyHint=true`. The selected
requester tool must also be present and schema-compatible. Unknown, duplicate,
missing, malformed, or writable entries fail closed.
The executable catalog contains `list_my_attendance_events`, a bot-owned
exact-selector composite that calls `resolve_employee` then
`list_attendance_events`, `get_current_work_status`, and `search_employees` when
their required MCP schemas are present. The detailed `get_current_attendance`
tool is not admitted for workforce status because REST now restricts it to
administrators. A name search renders directory-safe
candidates and requires the user to send a listed username or email in a new
message before an attendance read. The model never receives or chooses a
resolved employee ID; CRMT remains responsible for authorization. Current status
uses server time, optionally accepts a `statuses` array containing only `office`,
`remote`, `customer_site`, `break`, `absence`, and `no_status`, and is fetched
through every available page in one MCP request.
Tool-selection calls receive only bot-owned schemas, including
`reply_language` restricted to `en` or `sl`; remote discovery metadata,
administrator-only detailed current attendance, and disabled tools are never
offered. After one successful execution, the model receives no tools and only a
reply-safe result view. The model receives the Ljubljana reference date and resolves
English ordinal and Slovenian day-month forms. Ambiguous numeric dates are
rejected locally. Each requested range may span up to twelve calendar months;
the bot partitions it into inclusive 31-day MCP windows and reads every page
before the model can answer. Identity, resolved employee IDs, roles, pagination,
and authority are never model-controlled.

The initial pilot supports personal one-to-one chats and read-only actions.
These are pilot scope choices, not permanent product limits. Group, meeting,
channel, missing, and unknown conversation scope stop before SSO, OBO, OpenAI,
or MCP. The bot calls the public attendance-mcp MCP contract
`1.3.0`
through one validated model selection and one code-controlled execution. The
post-result model receives only a reply-safe view: history dates and record
presence, or current status categories and counts; names, timestamps, event
types, identifiers, notes, locations, tokens, and raw records stay in code.
The model may provide only a validated nonfactual title and context; code owns
attendance facts, translation, chronology, and final message chunks. Guidance
uses fixed English or Slovenian copy selected by a no-auth model intent, with no
attendance access. Reply batches mark their parts and finish; delivery stops
after a failed send and logs only the correlation ID, progress count, and error
type. Images, cards, files, and durable language preferences are intentionally
deferred to issues #22 and #21.

This verified client-side behavior does not establish a real integration. Real
attendance traffic remains disabled by default pending the attendance-mcp
non-production HTTPS `/mcp` deployment, Attendance REST API Entra `attendance.access` API
registration, delegated OBO consent, certificate configuration, end-to-end
identity/data verification, and the required organizational privacy/provider and
development-notice approvals.

## Container

### Optional conversation memory

Set `DATABASE_URL` only through the approved secret mechanism to enable the
bot-owned PostgreSQL conversation store. It retains a seven-day, twenty-message
maximum per authenticated tenant/AAD-user session for follow-up context; it does
not cache attendance facts, authorization data, assistant factual bodies, source-result
fields, or display names. User text may contain names. History may inform intent
or date references but never authorize tools or supply identity or attendance facts. When absent or
temporarily unavailable, the bot stays stateless and delivery continues normally.

The Compose file includes an internal-only PostgreSQL 17 service and named
`postgres_data` volume. It is intentionally a single bot replica; scaling the
Teams SDK process state is outside this slice.

### Docker Compose source setup

Copy `.env.example` to an untracked `.env`, configure non-secret identifiers
and service URLs, and provide credentials through the approved local or
deployment secret mechanism. Compose builds and runs the Teams bot as a separate
service; the attendance MCP adapter and REST API keep their own Compose
deployments. Keep `ATTENDANCE_INTEGRATION_ENABLED=false` until the external
activation gates are approved.

The PostgreSQL sidecar intentionally requires an externally supplied
`POSTGRES_PASSWORD`; no credential default is committed. If memory is enabled,
set `DATABASE_URL` to the Compose service hostname `postgres` and matching
`POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` values (for example,
`postgresql://attendance_memory:<password>@postgres:5432/attendance_memory`).
The bot filters only these three case-insensitive PostgreSQL-sidecar dotenv
keys before strict settings validation. Every other unknown dotenv key remains
an error, while all bot-owned settings stay validated; this makes a copied
Compose dotenv usable without turning sidecar configuration into application
settings.

### Manual named-volume restart verification

This is a reproducible local verification procedure, not an automated test. It
uses only synthetic credentials, an isolated Compose project, and the existing
named volume; `down` deliberately omits `--volumes` so the marker must survive.

```bash
export POSTGRES_PASSWORD=synthetic-postgres-password
export BOT_IMAGE=attendance-teams-bot:memory-restart-check
docker compose --env-file /dev/null -p attendance-memory-restart build attendance-teams-bot
docker compose --env-file /dev/null -p attendance-memory-restart up -d postgres
docker compose --env-file /dev/null -p attendance-memory-restart exec -T postgres \
  psql -U attendance_memory -d attendance_memory -c \
  "CREATE TABLE restart_verification (marker TEXT NOT NULL); INSERT INTO restart_verification VALUES ('synthetic-marker');"
docker compose --env-file /dev/null -p attendance-memory-restart down
docker compose --env-file /dev/null -p attendance-memory-restart up -d postgres
docker compose --env-file /dev/null -p attendance-memory-restart exec -T postgres \
  psql -U attendance_memory -d attendance_memory -c \
  "SELECT marker FROM restart_verification;"
docker compose --env-file /dev/null -p attendance-memory-restart down --volumes
unset POSTGRES_PASSWORD BOT_IMAGE
```

Set `MCP_ENDPOINT` to the MCP service's reachable HTTPS endpoint, such as
`https://attendance-mcp.<your-routable-domain>/mcp`; set the Bot Service
messaging endpoint to the bot's own published URL, such as
`https://attendance-bot.<your-routable-domain>/api/messages`. Replace the
domain placeholders with DNS names routable from the relevant service and
Microsoft Bot Service. `localhost` inside a container refers to that
container. The sample bind address publishes locally only; an approved host or
ingress must provide routable HTTPS before Teams traffic is enabled.

```bash
docker compose --env-file .env.example config
docker compose up --build
```

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

### Azure Container Registry automation

The `Container image` GitHub Actions workflow runs only when manually dispatched
from `main`; it then pushes
`attendancecrmtbotdev-gdc8cwfndyatcqfq.azurecr.io/attendance-teams-bot` tagged
with the first seven characters of the selected commit SHA. Pull requests and
feature branches cannot publish an image.

Before the first push, create an Azure workload identity for this repository,
grant it the `Reader` and `Container Registry Repository Writer` roles scoped to
the `attendancecrmtbotdev` registry, and add a federated credential restricted to
`repo:joselu103/attendance-teams-bot:ref:refs/heads/main`. In the repository's
GitHub Actions variables, set its `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and
`AZURE_SUBSCRIPTION_ID`. This uses GitHub OIDC, so no Azure password or service
principal secret is stored in GitHub.

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
