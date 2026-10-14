# ATTENDANCE-LOG-DURATION-TOTALS-001 — Teams owner plan

Planning completed locally on 13 October 2026. Implementation has not started.
This document is the Teams-specific implementation plan and readiness report;
the adjacent issue packet is a verbatim copy of the approved shared packet.
All three owner plans must be reconciled before any implementation, followed by
verified REST → MCP → Teams delivery.

## Ownership, inputs, and base

- Owned checkout: `/home/joselu/orca/workspaces/attendance-teams-bot/log-duration-totals`,
  branch `joselu103/log-duration-totals`.
- Initial worktree was clean. HEAD, local `origin/main`, and their merge base
  were all `620fd4c6ed79e094e69962ec69ee9af894117766`.
- Read shared packet and root `docs/codex-handoff.md`, local `AGENTS.md`,
  `AGENT_STATE.json`, active `AGENT_INBOX.md`, all four task guidance files,
  response guide, issue/domain conventions, `CONTEXT.md`, relevant ADRs,
  README verification instructions, and relevant source/tests.
- Root handoff records a later remotely verified main `60228c140f358763e8a09bc109ccc2fdc749eb3d`.
  Local tracking refs were not refreshed. Reconcile the intended source base
  with the coordinator before implementation; no fetch, rebase, or merge was
  performed during planning. Historical state/inbox/ADRs mention old windowing
  and catalog behavior; current code, tests, and newer root handoff establish
  full-period single-page reads and database-backed continuations. Preserve
  that behavior, including unrestricted explicit history spans.
- Planning changes are only the copied packet and this plan. No source/test,
  state/inbox, upstream-owner, or root edits are included. The existing external
  `blocked` readiness state is unchanged.

## Existing flow and narrow edit surface

| File | Finding / planned change |
| --- | --- |
| `src/attendance_teams_bot/mcp/contracts.py` | `AttendanceEvent` is the bot's history item; add required nullable duration and a required typed summary to `AttendanceEventPage`. Other result models stay unchanged. |
| `src/attendance_teams_bot/mcp/session.py` | Both enabled history tools already parse `AttendanceEventPage`; verify the new fields through this path without changing tool dispatch or transport. |
| `src/attendance_teams_bot/agent/orchestrator.py` | `_attendance_page` serializes items and discards all other result data except next offset; explicitly retain summary. Pass the current summary to both initial own/admin rendering and model-free continuation rendering. |
| `src/attendance_teams_bot/agent/rendering.py` | Extend required history presentation data and daily display entries; replace daily aggregate/exclusion text with row totals and add a history-specific page footer. Keep current-status rendering unchanged. |
| `tests/unit/test_mcp_contracts.py`, `tests/unit/test_mcp_session.py` | Validate required fields and transport parsing, including null, zero, malformed and old-shape payloads. |
| `tests/unit/test_attendance_rendering.py` | EN/SL row totals, clipping/splitting/truncation/DST, empty pages, singular footer and atomic day groups. |
| `tests/unit/test_agent_orchestrator.py`, `tests/unit/test_visibility_orchestration.py` | Own/admin initial pages, both continuation scopes, fresh totals, pagination, malformed results and model minimization. |
| `tests/integration/test_mcp_client.py`, `tests/integration/test_authenticated_attendance_flow.py` | Synthetic official SDK MCP transport and authenticated delivery prove the fields survive to Teams. |

Existing history constructors occur in the six test files for contracts,
rendering, orchestrator, visibility, MCP client, and authenticated attendance
flow. Migrate their synthetic fixtures explicitly when the required contract
lands. Add targeted authenticated-handler assertions only if needed to prove
delivery/continuation behavior; no production handler, continuation, memory,
settings, model-contract, or catalog changes are expected.

## Implementation decisions

### Typed incoming history contract

1. Add `duration_minutes` to `AttendanceEvent` as a required `int | None`,
   strict integer when non-null and nonnegative. A missing key must fail;
   do not provide a default or calculate a missing authoritative value.
   Keep current timestamp-offset validation and item extra-field behavior.
2. Introduce a frozen summary model with `extra="forbid"` and required strict,
   nonnegative integer `page_duration_minutes` and `period_duration_minutes`.
   Require `summary` on the already strict `AttendanceEventPage` envelope.
   Reject booleans, strings, floats, negatives, null summary/totals, missing
   fields, and extra summary fields. Preserve all pagination validation.
3. Do not add duration fields to current-status, detailed current-attendance,
   directory, or other event-shaped responses. No version/header changes:
   Teams remains `0.1.0`, REST remains v1/`1.0.0`, MCP remains pre-release
   `1.3.0` with the existing major-version admission.
4. REST owns full-event UTC minutes, overlap eligibility, page totals and
   full-period clipped totals. Teams neither recalculates nor reconciles those
   values. In particular, page duration can exceed period duration for a
   clipped event; displayed daily minutes can sum to less than either.

### Initial and continuation plumbing

1. `_attendance_page` retains a serialized, validated summary beside items,
   dates and next offset. Rehydrate the typed summary where the initial and
   continuation branches construct `SafeHistoryPresentation`; give that
   presentation a required summary field before its optional fields. Prefer
   keyword construction at these touched call sites to avoid positional drift.
2. Initial self history still makes one MCP history call. Initial administrator
   history still resolves one exact current-message selector and then makes
   one history call with the internal resolved ID. Both use the same summary
   and rendering path. Upstream remains the authorization authority.
3. Self/admin continuations still reauthenticate, read the live catalog, fetch
   exactly one page with `limit=50`, and make no model calls. Pass that page's
   summary directly, including when it has no items at a beyond-last offset.
   Changed data may produce a changed total on a later page; show the new value.
4. Preserve dates, offsets, page numbers, opaque cards, query-only cursor,
   binding, expiry, leases, delivery ordering and failed-send behavior. Never
   store durations/summaries or accumulate totals in cursor or dialogue memory.
5. History results remain code-rendered. New duration/summary facts never enter
   model context or assistant memory.
   Malformed pages follow existing safe unavailable handling; do not render
   partial rows, fabricated totals, or legacy fallback results.

### Row durations and daily display segments

1. Carry an explicit nullable minute value on `_HistoryDisplayEntry`.
   For an unchanged whole-event row use `event.duration_minutes` exactly,
   including a zero-duration event. Determine whether endpoints changed using
   UTC instant comparisons, not local wall time or formatted HH:MM values.
2. Preserve `_split_history_event`'s period clipping and local-midnight loop.
   For each split/clipped daily segment whose authoritative duration is
   non-null, calculate only its display duration from its UTC endpoints and
   truncate that segment independently to whole minutes. A single clipped
   segment is still derived; a single unchanged segment ending at midnight
   still uses the API duration despite rendering its endpoint as `24:00`.
3. Preserve authoritative null as unavailable, even if timestamp values exist.
   Keep incomplete/reversed/zero intervals visible under current handling.
   An active event starting before the period stays an unsegmented active row
   with its original date/time and unavailable duration; do not invent a finish
   time, clip its start, or hide it because its start is outside the header dates.
4. Remove only the separate daily aggregate and old daily exclusion note.
   Append italic parenthetical copy to each existing row after its type:
   EN `*(Total: 8h 00m)*`, SL `*(Seštevek: 8h 00m)*`; null uses
   EN `*(Total: unavailable)*`, SL `*(Seštevek: ni na voljo)*`.
   Preserve bullet indentation, backticks, icons, safe escaping, controlled
   labels, chronological date grouping, time text, `24:00`, and DST offsets.
   Format integer minutes with unbounded hours and two-digit remainder minutes.
5. Fractional minutes must never pool between rows: an event from local
   `23:59:30` to `00:00:30` has API duration 1 but two rows of `0h 00m`.
   The full-period footer remains the API's `0h 01m` when both days are included.

### Every-page history footer and Teams batches

1. Add a history-specific footer constructor using the required summary.
   Existing `_history_footer` is also the default suffix for current-status
   replies; keep its non-history behavior and text unchanged.
2. History suffix order is the existing divider, localized period-total line,
   then the existing completion/continuation copy, separated with Teams `<br>`:
   EN `Period Total: 8h 00m`, SL `Seštevek obdobja: 8h 00m`.
   Use `summary.period_duration_minutes` only; do not display page totals or
   sum displayed rows as the footer. Keep all existing header/date/timezone copy.
3. Use this footer for both empty and non-empty render paths and pass `has_more`
   in both. The empty path currently hardcodes end-of-results; fix its
   presentation-level choice while retaining no-data copy and a singular footer.
4. Existing strict page validation rejects empty items with non-null next
   offset. With preserved limit-plus-one selection such a wire page should not
   occur. Keep this guard; test empty API pages with `next_offset=None`,
   non-empty intermediate pages, and independently test the renderer's empty
   `has_more=True` presentation. Reconcile any contrary upstream expectation
   before implementation rather than silently changing cursor semantics.
5. Pass the entire history suffix to `_ordered_batch` so its capacity reserve
   includes the longer footer. Complete day groups remain indivisible. The
   total/footer occurs once, on the final Teams message of the fetched page;
   the header stays once on the first message. Retain safe failure when a
   complete group cannot fit; do not split rows within that date group.

## Behavior-level verification sequence

Write failing observable contract/rendering/flow tests first, then implement the
smallest corresponding source changes and migrate synthetic fixtures. Capture
RED failures for new behavior, not only constructor/import failures. Run each
focused group after GREEN and the full documented checks after final edits.

| Scenario | Observable acceptance and owner/test location |
| --- | --- |
| Required new wire fields | Contract/session tests accept positive, zero and null item minutes; require both summary totals even on empty pages; reject malformed fields and the old payload shape. |
| Whole-event authority | Renderer uses a supplied non-null API minute value exactly rather than recalculating timestamps; preserves zero and never silently substitutes a missing value. |
| Every anomaly | EN/SL active, missing start, missing both endpoints and reversed rows retain existing time/date rendering with localized unavailable totals; zero shows `0h 00m`; no daily aggregate/exclusion text remains. |
| Overlap presentation | Synthetic upstream-selected left/right/both-side crossing events display only completed segments inside the requested local days; active pre-period events remain visible; valid completed segments do not gain phantom boundary rows. REST verifies query eligibility and exact boundary exclusions. |
| Midnight/fractions | A 60-second event straddling midnight displays independently truncated 0-minute segments and a 1-minute API footer; clipping a longer event to one day uses the segment minutes, not the full-event minutes. Exactly-midnight end uses `24:00` without a new empty day. |
| DST | Preserve current spring/fallback examples; repeated `02:30` offsets disambiguate fallback. Full Ljubljana spring/fallback days produce 23h/25h derived rows from UTC elapsed time. |
| Independent overlap | Two overlapping rows retain separate API totals. A synthetic footer unequal to their visible sum proves Teams trusts upstream and does not merge intervals. REST proves authoritative independent counting. |
| Pagination/live totals | Own and exact-selector admin initial/intermediate/final pages keep fixed requests and page numbers. Continuations fetch once without model calls; a changed API total replaces the previous value. Empty first and beyond-last pages still show current period total, even if nonzero. |
| Empty display/intermediate | Empty renderer presentation with `has_more=True` retains next-page text after the total; transport still rejects invalid empty advancing cursors. |
| Message splitting | EN/SL complete-day groups span multiple messages under a controlled feasible character limit; all rows survive, header once, footer/period total once on last message, no earlier completion text, all lengths bounded. Oversized indivisible groups retain safe unavailable behavior. |
| Unsafe result handling | Missing/malformed duration/summary through SDK/fake session produces localized safe unavailable reply, no partial factual report and no cursor advancement. |
| Boundary invariants | Existing own/admin selector isolation, fresh SSO/OBO/catalog, no model continuation, safe projections/memory, failure-stop delivery and current-status rendering tests continue to pass. |
| Real MCP transport | Extend local FastMCP SDK transport test to both history tools and summaries/null/zero, including empty pages; keep bearer/correlation/version header assertions. No deployed endpoint is needed. |

Focused command after test/source changes:

```sh
env -u PYTHONPATH -u TEST_DATABASE_URL uv run --frozen pytest tests/unit/test_mcp_contracts.py tests/unit/test_mcp_session.py tests/unit/test_attendance_rendering.py tests/unit/test_agent_orchestrator.py tests/unit/test_visibility_orchestration.py tests/unit/test_history_continuation.py tests/unit/test_authenticated_handler.py tests/integration/test_mcp_client.py tests/integration/test_authenticated_attendance_flow.py -q
```

Final documented checks (the frozen lock and cleared environment avoid drift):

```sh
env -u PYTHONPATH -u TEST_DATABASE_URL uv run --frozen pytest -q -rs
env -u PYTHONPATH uv run --frozen ruff check .
env -u PYTHONPATH uv run --frozen ruff format --check .
env -u PYTHONPATH uv run --frozen mypy
python3 -m json.tool AGENT_STATE.json > /dev/null
git diff --check
```

If the coordinator provides an explicitly isolated synthetic PostgreSQL test
database, also rerun the complete suite with that synthetic `TEST_DATABASE_URL`
and record no-skip results. Never inspect dotenv files or use an inherited
database URL as evidence. No memory schema change is planned; absence of that
synthetic database is a stated verification limit, not a deployed readiness
claim. Compose/image checks are unnecessary unless deployment source changes;
none are planned or authorized by this owner slice.

## Planning verification actually performed

- `env -u PYTHONPATH -u TEST_DATABASE_URL uv run --frozen pytest -q -rs`:
  **251 passed, 5 skipped** in 2.39s. All five skips are synthetic PostgreSQL
  memory/continuation tests because the test URL was deliberately unset.
- `env -u PYTHONPATH uv run --frozen ruff check .`: passed.
- `env -u PYTHONPATH uv run --frozen ruff format --check .`: **80 files** formatted.
- `env -u PYTHONPATH uv run --frozen mypy`: passed, **32 source files**.
- `cmp` confirms byte-for-byte packet equivalence; state JSON validation,
  `git diff --check`, and both documents' whitespace/final-newline checks pass.
  Final git status contains only the two new issue documents; tracked source,
  tests, state and inbox are unchanged.

These establish the existing base only. They do not verify new duration behavior,
an upstream implementation, PostgreSQL integration, or any external gate.

## Readiness, blockers, and handoff

Teams planning is complete; Teams implementation is **dependency-blocked**:

1. Coordinator reconciles all owner plans, intended source bases, new-field
   requiredness/type/null rules, overlap behavior, and unchanged version labels.
2. REST supplies verified history-only wire models, overlap/boundary/anomaly
   evidence, authoritative page/period totals, pagination/empty-page evidence,
   non-history shape regression evidence, state and source handoff.
3. MCP supplies verified unchanged forwarding of item/summary fields through
   real MCP SDK transport for self/admin/empty pages, narrow updated guidance,
   full checks, unchanged `1.3.0`, state and source handoff.
4. Only then implement Teams against the verified new contract. Reject old
   results safely; no compatibility fallback or extra endpoint probing.

No additional product-decision blocker was found. Base-ref drift and the empty
advancing-cursor interpretation above are explicit reconciliation checkpoints.
External Entra/OBO/certificate, HTTPS MCP deployment, real-account identity and
authorization, audit operations, company provider/privacy approval, and real
Teams delivery gates remain unchanged. No attendance SQL Server access,
authority/rule recreation, real data, secrets, publication, external mutation,
commits, pushes, PRs, merges, or deployment is part of this work.

On eventual implementation completion, update owner `AGENT_STATE.json`, active
inbox/task evidence and a durable handoff report with actual RED/GREEN/full
checks, skip limits, source revisions, required new-contract compatibility,
unchanged version labels, dependencies and external blockers. Coordinate those
records only through the live Orca Dispatch; the planner does not publish them
as completed implementation evidence.

## Complexity and implementation model

**Assessment: moderate, suitable for GPT-6 Luna.** The existing bot already owns
UTC-aware display splitting, clipping, DST formatting, ordered batches and both
history scopes. This slice adds strict typed fields, two explicit summary
plumbing sites, per-row display minutes and one localized page suffix; it adds
no database schema, authority, model policy, new tool, or interval selection.
The main risks are losing the summary in `_attendance_page`, recalculating an
unchanged row, shared-footer regressions, independent segment truncation and
empty-page paths; the behavior matrix above addresses each directly.

The coordinator should launch Teams implementation with the approved GPT-6
Luna and verify the effective model in its launch receipt before dispatch.
Use GPT-5.6 Terra only if reconciliation exposes additional interval or cursor
work beyond this plan; report that complexity change rather than expanding
scope silently. REST interval arithmetic remains the REST owner's GPT-5.6 work.
