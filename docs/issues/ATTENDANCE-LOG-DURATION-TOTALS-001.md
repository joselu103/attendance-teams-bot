# ATTENDANCE-LOG-DURATION-TOTALS-001

## Approved outcome and scope

Add duration metrics to personal and administrator attendance-history pages,
event/segment totals to Teams history rows, and a full-period footer to every
page. Only the listed fields, selection behavior, and formatting may change.
Preserve authorization, audit, other endpoint shapes, ordering, page sizes,
continuations, unrelated formats, and existing development version labels.

## Shared contract

- History items alone gain `duration_minutes`: full-event UTC elapsed time
  truncated to whole minutes. Missing endpoints or reversed intervals yield
  null; zero-length intervals yield 0. Other shared event representations retain
  their existing shape.
- Inclusive Europe/Ljubljana dates define a half-open interval from start-date
  midnight through midnight after end-date. Pages include completed overlapping
  events and active events starting before the interval ends, including active
  punches that started before the period. Retain invalid/incomplete in-range
  records under existing handling; do not silently discard anomalies previously
  eligible for display. Preserve timestamp/event-ID order and limit-plus-one.
- Root `summary.page_duration_minutes` directly sums non-null returned item
  durations. `summary.period_duration_minutes` ignores pagination and sums valid
  completed overlapping intervals after clipping each to the period and truncating
  each contribution to whole minutes. Reuse `duration_minutes` for wholly
  contained events. Overlapping events count independently. Empty totals are 0.
- MCP forwards the new fields unchanged without calculating attendance metrics.
- Bot accepts the new fields without legacy compatibility work. Replace the
  separate Daily Total line with localized `(Total: Xh XXm)` on each row.
  Unchanged whole-event rows use the API duration; split/clipped daily rows use
  their own UTC elapsed duration, independently truncated. Preserve existing
  splitting, clipping, `24:00`, DST disambiguation, icons, time text, and Markdown.
  Null durations render localized `Total: unavailable` instead of the old daily
  exclusion note. Independently truncated segments can sum to less than an event.
- Every fetched page, including empty/intermediate pages, displays localized
  `Period Total: Xh XXm` immediately after its footer separator and before the
  existing end/continuation message. A page split into Teams messages has that
  footer once in the last message. Use the current API total, never accumulation
  over fetched pages; live pages retain their existing lack of snapshot guarantee.

## Owners and delivery gates

| Owner | Responsibility | Dependency |
| --- | --- | --- |
| REST API (`attendance-crmt`) | History-specific models, overlap selection, authoritative durations and summaries, REST contract/tests | None |
| `attendance-mcp` | Transparent forwarding, narrow tool guidance/contract and transport tests | Verified REST contract and evidence |
| `attendance-teams-bot` | Typed validation, presentation plumbing, row totals and footer, rendering/integration tests | Verified MCP contract and evidence |

Public notices are unaffected; no public publication is requested.
Planning runs in parallel in separate Orca-created owner worktrees. Each planner
reads this packet, root `docs/codex-handoff.md`, local AGENTS/state/active inbox,
guidance, and working tree. Write a repository-specific plan and readiness report;
do not implement during planning. Reconcile plans before implementation.
Implementation and verification proceed REST API, MCP, then bot. Each owner
publishes actual tests, contract/version status, dependencies and blockers in its
state and a handoff report. Local source verification clears no external gate.

## Verification

Use synthetic data only. Test self/admin isolation, all overlap boundaries,
active pre-period punches, incomplete/reversed/zero intervals, independent
overlaps, fractional minutes, midnight, spring/fallback DST, pagination and
beyond-last-page totals, and unchanged non-history endpoint shapes. Verify
MCP preservation through its transport and bot EN/SL rows, summaries, safe
invalid-result handling, empty/continuation/final pages and message splitting.
Use behavior-level RED then minimal GREEN where production behavior changes.
Run each repository's documented full checks after final edits.

## Execution authorization and model choices

User approved the specification and requested owner planning with GPT-6.1 Sol,
then implementation with GPT-6 Luna or GPT-5.6 Terra selected by complexity.
Use GPT-5.6 Terra (`gpt-5.6-terra`) for REST interval arithmetic; GPT-6 Luna
(`gpt-6-luna`) for MCP and Teams unless planning establishes complexity requiring
GPT-5.6 Terra. Planning uses `gpt-6.1-sol`. Verify effective launch model.
User follow-up prefers medium reasoning for further Terra work to save tokens;
Teams implementation also uses Luna medium. Completed high-effort work remains
valid and is not restarted.
No commits, pushes, PRs, merges, deployments, secret reads, real attendance-data
access, or external-resource changes are authorized.

## Current coordination state

All three GPT-6.1 Sol plans reconciled in Run `run_dfed2a086146` on 13 October.
Owner worktrees are `/home/joselu/orca/workspaces/<owner>/log-duration-totals`;
each contains this packet and `docs/issues/ATTENDANCE-LOG-DURATION-TOTALS-001-plan.md`.
REST base `70a4fb5`: 168 baseline tests; MCP base `8774f14`: 58 baseline tests;
Teams base `620fd4c`: 251 baseline tests, five synthetic PostgreSQL skips. Required
baseline lint/format checks passed; Teams mypy passed. These are existing-source
checks, not implementation evidence.

Reconciliation: preserve existing timestamp serializer/default-fold interpretation;
naive database timestamps cannot recover unknown DST fold identity. No DDL/schema
change is needed; missing production DDL/runtime evidence remains external. Keep
in-range anomalies in overlap paging. Empty pages have `next_offset=null`; renderer
may independently handle an empty display with a continuation message. All summaries
remain current-request values. Use exact history-only models and preserve non-history
footer behavior. Ensure owner README and contract changes accompany verified source.

Teams remote `60228c1` differs from planned base `620fd4c` only in tunnel README;
read-only remote/source comparison confirmed no application change. Keep the planned
base and unrelated tunnel documentation untouched; no merge/rebase is authorized.

REST implementation may start with GPT-5.6 Terra. MCP and Teams use GPT-6 Luna,
and wait for verified upstream handoffs. Root contract and human handover still
describe existing behavior until implementation verification.
