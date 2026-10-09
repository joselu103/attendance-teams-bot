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
