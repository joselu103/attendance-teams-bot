# Attendance Teams Bot

This Python 3.14 `uv` project is the Teams-facing, LLM-powered client of the
Attendance MCP adapter; it never owns attendance authority or SQL Server access.

Before editing, read `AGENT_STATE.json`, the active `AGENT_INBOX.md`, and the
applicable root guidance. Do not directly access attendance SQL Server, recreate
attendance rules, or use a user- or model-supplied identity as authority.

**Output Rule:** Wait for operations to finish. On success, output ONLY 3-5 bullet points summarizing results. No diffs, code dumps, or long explanations. (Details: `docs/agents/response-guide.md`)

Load task-specific guidance:

- [Boundary and authentication](docs/agent-guidance/boundary-and-authentication.md)
- [LLM and reply safety](docs/agent-guidance/llm-and-reply-safety.md)
- [Engineering and verification](docs/agent-guidance/engineering-and-verification.md)
- [Module map](docs/agent-guidance/module-map.md)

## Agent skills

### Issue tracker

Issues and specs are tracked in this repository's GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Uses the default five-label triage vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

Uses the single-context domain-doc layout. See `docs/agents/domain.md`.
