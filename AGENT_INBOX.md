# Directive: ATTENDANCE-HISTORY-PAGINATION-001

> **Status:** Completed locally by coordinator after worker usage-limit interruption.
>
> **Scope:** Personal and administrator attendance-event listings only. Required
> inclusive Ljubljana dates have no maximum span; future end dates are allowed.
> One 50-event page per request and signed Next page buttons replace window
> splitting and all-history aggregation. Current-status/report policies are unchanged.
>
> **Dependencies:** Verified REST history-pagination worktree (168 tests), then
> MCP history-pagination worktree (57 tests). REST 1.0.0 and pre-release MCP 1.3.0
> routes, arguments, fields, auth/audit ownership and header flow remain compatible.
>
> **Continuation:** Persistent externally supplied ATTENDANCE_HISTORY_SIGNING_KEY
> (at least 32 UTF-8 bytes, shared across instances), authenticated SDK binding,
> fresh SSO/one OBO/live catalog per click, zero model calls, original-position
> replay/restart, live data, safe invalid submissions and stop-on-send-failure.
> No real key was read or provisioned; no external activation is claimed.
>
> **Evidence:** Python 3.14.2 full pytest 247 passed; coordinator independently
> reran signer/actual SDK integration tests (71 passed). Ruff check and format
> check passed (55 files), strict mypy passed (29 files), Compose config with
> --env-file /dev/null passed. Final state JSON validation and git diff --check passed.
>
> **Delivery:** Local edits in Orca history-pagination worktree on
> joselu103/history-pagination, base f3fb26c. Worker ctx_353f9884d211 hit its
> usage limit after implementation and cleanup and sent no worker_done; the
> coordinator abandoned that dispatch without deleting its resources, reviewed
> the final source, completed records and reran required gates. Deployment,
> Entra/OBO, SQL, audit policy, privacy/provider, signing-key provisioning and
> real-account evidence remain blocked separately. No commit/push/PR/merge.

## Historical completed directive

# Directive: TEAMS-LLM-MCP-001

> **Status:** Completed historical directive. Do not re-execute it. Read
> `AGENT_STATE.json` and wait for a newly assigned directive before changing
> this repository.

## Main Objective
Replace the requester attendance flow's regex-only intent selection with a provider-neutral LLM orchestration boundary, using OpenAI API as the first adapter, while allowing the model to invoke only explicitly approved tools that are advertised by the authenticated Attendance CRMT MCP session.

## Key Context From Other Repositories
- `attendance-crmt` is `blocked` but declares the source contract `Attendance CRMT MCP contract 1.2.0`, including `list_my_attendance_events(start_date, end_date, limit=50, offset=0) -> AttendanceEventPage`, with server-derived employee identity and an inclusive maximum range of 31 days.
- `attendance-crmt` declares that no verified non-production HTTPS `/mcp` deployment or Entra API registration currently exists; real Teams attendance traffic must remain disabled pending those and the other declared security/readiness prerequisites.
- `attendance-crmt-development-notices` is `blocked`; it declares public development notice URLs, while publication readiness and organizational approvals remain unresolved.
- `attendance-teams-bot` is `ready` and declares an authenticated, personal-chat, requester-scoped, read-only flow against MCP contract 1.2.0; it still depends on the deployed MCP endpoint, Entra registration, delegated OBO consent, and production certificate configuration for real traffic.

## Specific Steps
1. Inspect the repository's current working tree and reconcile any pre-existing uncommitted work before implementation; do not assume it is correct, complete, or approved.
2. Define a provider-neutral asynchronous language-model contract and implement OpenAI API as the first adapter, keeping provider credentials and provider-specific response types behind that boundary.
3. Build a bounded orchestration flow that discovers tools from the authenticated MCP session, intersects them with an explicit bot-side allowlist/typed policy, and rejects unknown tools, malformed arguments, parallel calls, excessive call loops, and unusable model responses.
4. Initially allow only `list_my_attendance_events` from MCP contract 1.2.0. Preserve MCP as the identity, authorization, validation, execution, and audit boundary; never expose Teams/OBO bearer tokens, SQL access, arbitrary HTTP/filesystem/code tools, or executable callbacks to the model.
5. Ensure natural-language date-range requests are interpreted through the model, validated against the typed requester-scoped contract, executed through the authenticated MCP session, and rendered safely. Decide explicitly whether attendance records are rendered deterministically or returned to the model, documenting and testing the data-exposure trade-off.
6. Preserve fail-closed behavior and the existing deployment gate: absence of OpenAI configuration, MCP/Entra prerequisites, tool-catalog agreement, or valid tool output must not enable real attendance traffic or invent attendance facts.
7. Add focused unit and integration tests for adapter translation, tool-catalog intersection, argument validation, tool-call limits, token/data non-disclosure, model/MCP failures, composition, and compatibility with the existing authenticated Teams flow. Run the repository's full test, lint, formatting, and type-check quality gates.
8. Document the OpenAI runtime configuration and the provider-replacement seam without claiming that Azure/Entra/MCP deployment prerequisites have been completed.

## Definition Of Done
- A natural-language requester attendance query is handled through the provider-neutral orchestration boundary with OpenAI as the first production adapter.
- The only callable action in this slice is the authenticated MCP-advertised and bot-approved `list_my_attendance_events` contract; unauthorized or unadvertised tool calls fail closed.
- Tokens, secrets, SQL connectivity, arbitrary local/remote capabilities, and unapproved employee-scoped operations are never exposed to the model.
- Focused and full tests, lint, formatting checks, and static type checks pass, with real command output recorded by the repository agent.
- Runtime configuration and provider replacement are documented, while deployment, Entra/OBO, certificate, production data-readiness, and notice-publication work remain explicitly outside this slice.

## Required `AGENT_STATE.json` Update
- `status`: record the actual outcome (`ready`, `blocked`, or another truthful state) after implementation and verification.
- `provided_exports`: declare the concrete provider-neutral LLM interface, OpenAI adapter/configuration contract, constrained MCP orchestration behavior, allowed MCP contract/tool version, and any verified tests or callable interfaces without claiming deployment.
- `dependencies_needed`: retain unresolved `attendance-crmt`, Entra/OBO, certificate, non-production HTTPS MCP deployment, and any newly discovered provider/runtime prerequisites precisely.
- `open_issues_or_blockers`: list all remaining integration, security, deployment, data-exposure, configuration, or end-to-end verification blockers; clear an item only when this repository agent has direct evidence that it is resolved.

## [COMPLETED] TEAMS-LLM-MCP-001

Provider-neutral OpenAI orchestration is verified against the fake authenticated MCP 1.2.0 boundary. Real attendance traffic remains disabled pending the declared external prerequisites.
