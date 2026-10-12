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
