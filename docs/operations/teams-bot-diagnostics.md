# Teams Bot Diagnostics

This runbook classifies a Teams callback failure without logging or retrieving
messages, identities, tokens, client secrets, OAuth exchange IDs, prompts, MCP
arguments/results, or attendance data.

## Verify The Active Container Revision

Authenticate interactively with `az login`. Do not print secret values.

```bash
az containerapp show \
  --name attendance-teams-bot-dev \
  --resource-group rg-attendance-teams-dev \
  --query '{runningStatus:properties.runningStatus,latestRevisionName:properties.latestRevisionName,latestReadyRevisionName:properties.latestReadyRevisionName,image:properties.template.containers[0].image,env:properties.template.containers[0].env[].{name:name,secretRef:secretRef,hasInlineValue:not_null(value)}}' \
  -o json

az containerapp revision list \
  --name attendance-teams-bot-dev \
  --resource-group rg-attendance-teams-dev \
  --query '[].{name:name,active:properties.active,runningState:properties.runningState,trafficWeight:properties.trafficWeight,image:properties.template.containers[0].image,createdTime:properties.createdTime}' \
  -o table
```

The active image must be an immutable commit-derived tag or digest. Its revision
must match the `app_version` field in the `application_started` JSON log event.

## Probe The Application Boundary

```bash
FQDN="$(az containerapp show \
  --name attendance-teams-bot-dev \
  --resource-group rg-attendance-teams-dev \
  --query properties.configuration.ingress.fqdn -o tsv)"

curl --fail-with-body --silent --show-error "https://$FQDN/health"
curl --silent --show-error -o /dev/null -w '%{http_code}\n' \
  -X POST "https://$FQDN/api/messages" \
  -H 'Content-Type: application/json' \
  -d '{"type":"message"}'
```

Expected results: `/health` returns `200`; an unsigned callback returns `401`.
A `404` from `/` is expected because it is not a defined route.

## Trace One Teams Interaction

Start a console log tail, send one personal-chat message, and follow only the
generated `correlation_id`.

```bash
az containerapp logs show \
  --name attendance-teams-bot-dev \
  --resource-group rg-attendance-teams-dev \
  --type console \
  --follow
```

| Last event | Meaning | Next check |
| --- | --- | --- |
| No `teams_callback_completed` | Channel, messaging endpoint, or ingress did not reach the app | Azure Bot messaging endpoint and Container App ingress |
| Callback `401` | Bot Service request authentication rejected the request | Bot registration and service connection |
| Fallback outcome `response_preserved` | The SDK returned `501`, but the activity did not match the narrow token-exchange fallback | Active image provenance and safe fallback fields |
| Fallback outcome `interactive_sign_in_requested` | The bot returned `412`; Teams should continue interactive sign-in | Azure Bot OAuth/Entra SSO configuration |
| `teams_sso_token_unavailable` or `teams_obo_exchange_failed` | Authentication stopped before OpenAI/MCP | OAuth connection, consent, scope, and Entra API configuration |
| `attendance_turn_completed` at `mcp_open`/`mcp_catalog` | MCP transport or contract is unavailable | attendance-mcp HTTPS endpoint and contract header |
| `attendance_turn_completed` at `model_completion` | Model adapter failed safely | Approved OpenAI model and platform secret reference |
| `teams_reply_send_failed` | Bot processing completed but outgoing delivery failed | Bot Service connector/outbound delivery |

The callback fallback changes only the empty token-exchange dead end. It does
not prove OAuth, Entra, OBO, or downstream MCP configuration is correct.

## Interpret A `signin/failure` Category

When a `signin/failure` invoke reaches the fallback, the
`teams_sso_token_exchange_fallback` event includes `sso_failure_code`. This is
an allowlisted category only: unrecognized or absent codes are recorded as
`unknown`; messages, tokens, exchange IDs, and the raw invoke payload are never
logged.

| `sso_failure_code` | Configuration checks |
| --- | --- |
| `resourcematchfailed` | Confirm the Azure Bot OAuth connection token-exchange URL is `api://botid-<BOT_APP_ID>` and that its app ID matches the installed bot. |
| `installedappnotfound`, `installappfailed` | Confirm the app is installed for the affected user and the Teams manifest bot ID matches the Azure Bot registration. |
| `authrequestfailed`, `invokeerror` | Confirm the Azure Bot messaging endpoint, OAuth setting name, and service connection are configured for the active revision. |
| `tokenmissing` | Confirm the OAuth connection uses the documented `access_as_user` scope and retry from a personal chat after the app is installed. |
| `oauthcardnotvalid` | Confirm `TEAMS_SSO_OAUTH_CONNECTION_NAME` exactly matches the Azure Bot OAuth setting name. |
| `userconsentrequired` | Confirm delegated consent and Teams client preauthorization for the bot app registration. |
| `interactionrequired` | Complete interactive sign-in, then check Conditional Access and consent requirements. |
| `unknown` | Verify the active immutable image and inspect only the safe callback fields; do not enable raw invoke-payload logging. |

## Verify Microsoft Configuration Without Secret Disclosure

Use the Azure Bot OAuth setting name from `TEAMS_SSO_OAUTH_CONNECTION_NAME`.
The runtime name and Azure Bot setting name must be identical. Verify the
connection provider is Azure Active Directory v2, its scope is
`api://botid-<BOT_APP_ID>/access_as_user`, and its token-exchange URL is
`api://botid-<BOT_APP_ID>`. The downstream Attendance CRMT scope belongs only to
OBO; do not place it in the Azure Bot OAuth connection.

```bash
az bot authsetting show \
  --name <AZURE_BOT_NAME> \
  --resource-group <RESOURCE_GROUP> \
  --setting-name <OAUTH_CONNECTION_NAME> \
  --query '{name:name,properties:{clientId:properties.clientId,parameters:properties.parameters[?key!=`clientSecret`],provisioningState:properties.provisioningState,scopes:properties.scopes,serviceProviderDisplayName:properties.serviceProviderDisplayName}}' \
  -o json

az ad app show --id <BOT_APP_ID> \
  --query '{identifierUris:identifierUris,requestedAccessTokenVersion:api.requestedAccessTokenVersion,scopes:api.oauth2PermissionScopes[].{value:value,isEnabled:isEnabled},preAuthorizedApplications:api.preAuthorizedApplications,redirectUris:web.redirectUris}' \
  -o json
```

Require token version `2`, enabled `access_as_user`, documented Teams client
preauthorization, and `https://token.botframework.com/.auth/web/redirect`.
