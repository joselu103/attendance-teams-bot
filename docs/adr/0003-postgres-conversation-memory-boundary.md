# ADR 0003: Optional PostgreSQL conversation-memory boundary

## Decision

The Teams bot may retain a bounded, seven-day dialogue history in its own
PostgreSQL database. A SHA-256 key derived only from Bot Service-authenticated
tenant and AAD object identifiers scopes each session; absent identifiers keep a
turn stateless.

Only accepted user text and validated non-factual guidance or presentation
framing are saved after the entire Teams reply batch is delivered. Rendered
attendance facts, result names, identifiers, tokens, raw tool results, and
provider envelopes never enter memory.

## Consequences

History is untrusted context supplied to the existing provider-neutral model
request. It may inform conversational intent or date references, but cannot
authorize tools, supply identity or cached attendance facts, retain a resolved
employee, or bypass fresh MCP catalog admission and authorization. User text
may contain names; the minimized exclusions apply to assistant factual bodies,
display names, and source-result fields. PostgreSQL outages fall back to
stateless operation, preserving the existing reply and retry behavior.

The shared Compose dotenv may also contain `POSTGRES_DB`, `POSTGRES_USER`, and
`POSTGRES_PASSWORD` for the sidecar. A dotenv-source hook filters only these
three keys case-insensitively before strict validation; every other unknown key
is rejected, and sidecar credentials are not modeled as bot configuration.

## Pagination continuation

The same optional PostgreSQL database owns an active continuation for a
delivered attendance-history page. It stores only validated scope, dates, fixed
page size, resolved administrator target where applicable, next offset,
language, expiry, and a short lease. Attendance events, rendered replies, model
output, and credentials are excluded.

Buttons carry an opaque continuation identifier. Buttons and typed continuation
phrases claim the same record, are bound to the authenticated user and Teams
conversation, and perform fresh SSO, OBO, live MCP admission, and one page
read. A successful page advances or clears the record; failure releases its
lease. Database unavailability disables continuation while preserving replies.
