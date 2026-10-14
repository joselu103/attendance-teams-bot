# Directive: MCP-TOOL-INSTRUCTION-OWNERSHIP-001

**Status:** Completed locally; upstream MCP metadata handoff verified at `b6372e6`.

The Teams bot now performs tool-free pre-auth routing. Only an internal
`attendance_needed` sentinel enters SSO/OBO, authenticated MCP discovery, and
then one bounded model selection from the admitted live catalog. Server tool
description, input schema, and `_meta` examples are forwarded unchanged to the
provider for direct tools; the other-employee action remains a bot-owned
resolve-then-history composite with its resolved ID internal.

Evidence: `uv run pytest -q` passed (223 passed, 5 skipped); Ruff, strict mypy,
JSON validation, and `git diff --check` passed. Deployment, Entra/OBO consent,
and production MCP endpoint prerequisites remain unchanged.


## ATTENDANCE-LOG-DURATION-TOTALS-001 Teams implementation (13 October 2026)

Implemented on accepted base `620fd4c` after verified REST and MCP handoffs. Required history duration and summary contract, localized row totals, and per-page period footer are in source; presentation summaries are required and source-null durations stay unavailable after segmentation. Final acceptance coverage verifies API-authoritative whole rows, independently truncated midnight segments, EN/SL nonzero empty/intermediate/split footers, fresh own/admin continuation totals without model/cursor cache, and strict metric rejection at the MCP-session and safe-flow boundaries; the old-shape fixture rejection was not feature-level RED. Verification: focused 192 passed; full 300 passed/5 expected synthetic DB skips; fresh isolated PostgreSQL 17 full run 305 passed/no skips with exact container cleanup; Ruff check/format, strict mypy, JSON, and diff checks pass. External activation gates remain blocked. Details: `docs/issues/ATTENDANCE-LOG-DURATION-TOTALS-001-handoff.md`.
