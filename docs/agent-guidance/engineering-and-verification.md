# Engineering and Verification

Use `uv`, `pyproject.toml`, pytest, Ruff, strict mypy, Pydantic configuration,
and structured logging. Run the relevant tests, lint, formatting, and type checks
before claiming completion.

Keep Teams SDK/framework code behind adapters. Keep LLM, Teams, MCP-client, and
authentication code modular and independently testable; inject the LLM client,
MCP client, clock, and external identity/Teams adapters. Test authentication
boundaries, MCP requests, tool-result handling, and security-sensitive behavior
before implementation.

Until the cross-repository contract is deployed and verified, use a local fake MCP
server in bot tests. Do not commit, push, or change deployment resources without
explicit user approval.
