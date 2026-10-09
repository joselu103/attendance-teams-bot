# Boundary and Authentication

The bot owns Teams messaging, adaptive-card/UI behavior, Teams/Entra adapters,
LLM orchestration, prompts, conversation state, an authenticated MCP client, safe
error rendering, bot observability, configuration, and tests. Attendance CRMT
remains the authority for token validation, requester mapping, authorization,
audit, attendance rules, and SQL Server access.

Use the approved request flow: the bot performs one OBO exchange, then calls the
MCP adapter with the resulting Attendance token; the adapter forwards that token
and correlation ID to Attendance CRMT REST. Do not treat `activity.from.id` as
verified Entra identity, email, employee ID, or authorization evidence.

Keep Microsoft Agents SDK imports in `teams/microsoft_agents.py`; stable
application and authorization logic must not depend on SDK types. Keep `local`
mode explicitly unauthenticated and never expose it as a Bot Service endpoint.

The real attendance route remains disabled until the approved Entra, deployment,
certificate, identity, authorization, audit, privacy, and end-to-end gates have
direct evidence.

History buttons bind only authenticated SDK tenant, AAD user and conversation
metadata, never activity.from.id or a model/user identity. Validate signed data
before retrieval; every valid click obtains current SSO credentials, performs
one OBO exchange and live MCP admission, and leaves authorization to REST.
Enabled attendance requires an externally supplied persistent history signing
key shared across replicas; never generate, read or provision a real secret.
