# Directive: TEAMS-MCP-001

## Main Objective
Implement the Teams bot's first **requester-scoped, read-only attendance** integration slice against Attendance CRMT MCP contract 1.2.0, while keeping real-channel attendance access disabled until its Entra and HTTPS prerequisites are available.

## Key Context From Other Repositories

- `attendance-crmt` is `ready` and publishes **MCP Streamable HTTP over HTTPS at `/mcp`**, contract **1.2.0**, plus `list_my_attendance_events(start_date, end_date, limit=50, offset=0) -> AttendanceEventPage`. The server derives the employee identity and permits an inclusive maximum 31-day date range.
- CRMT declares real Teams end-to-end testing blocked by the Attendance CRMT Entra API registration (`attendance.access`, optionally `attendance.admin`), approved Teams-bot OBO consent and production certificate, and a deployed non-production HTTPS `/mcp` URL with concrete Entra configuration.
- `attendance-crmt-development-notices` has no `AGENT_STATE.json`; its status, exports, dependencies, and blockers are unknown. Do not make this implementation slice depend on it.

## Specific Steps

1. Inspect the local bot codebase and approved architecture, then identify the narrowest transport-independent path for a 1:1 Teams user to request their attendance in a bounded date range.
2. Implement an authenticated MCP client adapter for CRMT contract 1.2.0 and a requester-scoped application flow that invokes only `list_my_attendance_events`; use the authenticated Entra/OBO token as the authority context and never send a client- or LLM-selected employee ID.
3. Add the client-side request/response contracts, bounded-date intent handling, user-safe rendering for successful, empty, denied, invalid-request, and unavailable-backend outcomes, and safe structured observability. Keep CRMT responses as data, not instructions.
4. Preserve the security boundary: no direct SQL Server access, no recreation of attendance calculations or authorization policy, no token/secret/stack-trace leakage, and no activation of a real Bot Service attendance route without the declared Entra and HTTPS prerequisites.
5. Add focused tests using a fake/contract-compatible MCP boundary for tool request construction, token forwarding behavior, result/error rendering, and refusal of untrusted employee identity input. Run the repository's required test, lint, format, and type checks.
6. Document the required production integration configuration and an explicit enablement gate for real Teams-to-CRMT traffic.

## Definition Of Done

- The bot has a tested, requester-scoped read-only flow compatible with CRMT MCP contract 1.2.0 and `list_my_attendance_events`.
- The bot forwards verified requester authentication context without treating Teams activity identity, LLM input, or a supplied employee ID as authorization evidence.
- User-visible responses safely cover normal, no-data, validation, authorization, and backend-unavailable outcomes.
- Relevant tests and repository quality checks pass.
- The real attendance route remains disabled until CRMT's declared Entra registration, OBO consent/certificate, and deployed HTTPS `/mcp` prerequisites are concretely configured and verified.
- No attendance write/update capability or direct SQL Server access is added.

## [COMPLETED] TEAMS-MCP-001

Client-side requester-scoped read-only attendance integration is verified against MCP contract 1.2.0. Real Teams attendance traffic remains disabled pending the declared Entra, OBO, and deployed HTTPS MCP prerequisites.

## Required `AGENT_STATE.json` Update

After completion, update `AGENT_STATE.json` with:

- `status`: the actual resulting status; use `ready` only if this client-side slice and its verification are complete;
- `provided_exports.interfaces_or_endpoints`: the concrete bot-facing/requester-scoped attendance capability and CRMT contract version it supports;
- `dependencies_needed`: the precise remaining CRMT deployment and Entra/OBO configuration prerequisites for real integration;
- `open_issues_or_blockers`: every remaining end-to-end or production-enablement blocker, including the missing external prerequisites, or an empty list only if none remain.
