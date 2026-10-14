# ATTENDANCE-LOG-DURATION-TOTALS-001 — Teams handoff

## Readiness

The Teams source slice is implemented locally on accepted base `620fd4c6ed79e094e69962ec69ee9af894117766`. REST `1.0.0` and MCP `1.3.0` contracts and the Teams development version are unchanged. The implementation consumes the verified history-only fields; the presentation summary is required and has no fabricated default. Null duration remains unavailable even for split rows; it does not add an old-shape fallback, recalculate authoritative totals, or put duration facts into model projections or continuation state.

## Behavior

`AttendanceEvent` requires strict nonnegative `duration_minutes` or null; history pages require strict nonnegative page and period totals. Initial self/admin rendering and model-free continuations pass each fresh summary into the history renderer. Whole rows use the API minutes; clipped/split rows calculate independently truncated UTC segment minutes only when endpoints change. Each row carries localized `Total` / `Seštevek` text, and every page footer includes the current period total before the existing end/continuation text. Empty pages use the same footer and batching keeps the footer on the final message. Current-status footer behavior is unchanged.

## Verification

The earlier old-shape contract failure for missing duration and summary was fixture-migration evidence, not feature-level RED. Final focused contract/session/rendering/orchestration/visibility/continuation/handler/MCP SDK/authenticated-flow verification passed **192 tests**. The full suite without a database URL passed **300 tests with 5 expected synthetic-PostgreSQL skips**; a fresh local synthetic PostgreSQL 17 container then ran the full suite with **305 passed and no skips**, and was removed exactly afterward. Ruff check and format check passed (83 files), strict mypy passed (32 source files), and JSON validation plus `git diff --check` passed. No inherited URL, dotenv, real attendance data, or SQL Server was accessed.

## Limits

REST/MCP source handoffs establish upstream source contracts and transport preservation; these local synthetic checks do not establish deployed HTTPS, Entra/OBO, real-account authorization, audit operations, provider/privacy approval, or production integration. Those external activation gates remain blocked. No commit, push, PR, merge, deployment, or external change occurred.
