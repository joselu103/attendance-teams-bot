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
ATTENDANCE_HISTORY_SIGNING_KEY=<persistent-secret-from-approved-secret-store>
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
OpenAI or history signing-key configuration fails startup rather than falling back to an attendance route.

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
rejected locally. History requires both boundaries, with no maximum date span.
Clear relative periods such as “last month” resolve to explicit inclusive
Europe/Ljubljana dates, shown in every page. Missing or ambiguous boundaries
require a new message restating the whole period. Future end dates are allowed.
The initial history request reads one page with `limit=50`, `offset=0` over the
complete requested period. Identity, resolved employee IDs, roles, pagination,
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
type. History adds a separate Next page Adaptive Card after every text batch
when the server returns a valid advancing `next_offset`. Final and empty pages
have no button. Other rich outputs and durable language preferences remain
follow-up work in issues #22 and #21.

### History continuation and signing-key setup

`ATTENDANCE_HISTORY_SIGNING_KEY` is required when attendance is enabled. Supply
an externally provisioned high-entropy secret of at least 32 UTF-8 bytes through
the approved secret manager, using the same persistent value on every replica.
The bot stores it as `SecretStr`, never generates it at startup, and does not
write it to logs or model context. Compose passes the runtime value through;
this repository does not provision a real secret.

A Next page button holds a versioned HMAC-SHA256 signed query: original dates,
self/administrator scope, the once-resolved administrator target (only for that
scope), fixed limit 50, next offset, reply language, and authenticated SDK
Teams tenant/AAD-user/conversation bindings. Payloads have strict field/type,
size, version, date-order, scope and signature checks. A signature binds query
context; the REST API remains the authorization authority.

Each valid personal-chat click gets current Teams SSO credentials, performs one
OBO exchange, discovers and admits the live MCP catalog, and requests exactly
one history page directly. It bypasses both model selection and completion and
uses the localized deterministic renderer. Malformed submissions never fall
back to a model. The initial administrator selection resolves once; continuations
keep its signed resolved target without a new directory lookup.

Buttons have no time expiry and survive restarts with the same key. Replacing
the key invalidates existing buttons. Repeated clicks request their original
offset; results use live data, so additions/deletions can move records between
pages. There is no snapshot, stored query history, aggregate cap, or maximum
number of reachable history pages. REST 1.0.0 and pre-release MCP 1.3.0 tool
names, arguments, and response schemas remain compatible. Current-status paging
and upstream report limits are unchanged.

This verified client-side behavior does not establish a real integration. Real
attendance traffic remains disabled by default pending the attendance-mcp
non-production HTTPS `/mcp` deployment, Attendance REST API Entra `attendance.access` API
registration, delegated OBO consent, certificate configuration, end-to-end
identity/data verification, and the required organizational privacy/provider and
development-notice approvals.

## Container

### Docker Compose source setup

Copy `.env.example` to an untracked `.env`, configure non-secret identifiers
and service URLs, and provide credentials through the approved local or
deployment secret mechanism. Compose builds and runs the Teams bot as a separate
service; the attendance MCP adapter and REST API keep their own Compose
deployments. Keep `ATTENDANCE_INTEGRATION_ENABLED=false` until the external
activation gates are approved.

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
