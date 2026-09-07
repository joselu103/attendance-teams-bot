# Module Map

- `agent/`: LLM orchestration and tool-selection policy.
- `auth/`: Entra/Teams identity and token handling.
- `mcp/`: authenticated remote MCP client.
- `teams/`: Teams activity adapter and response rendering.
- `contracts/`: immutable internal request/response models.
- `application.py`: transport-independent bot behavior.
- `local.py`: connectivity-only local handler.
- `composition.py`: local or authenticated Teams wiring.
- `teams/authenticated.py`: SDK-independent authenticated-message handler.
- `teams/microsoft_agents.py`: Microsoft Agents SDK callback adapter.
- `asgi.py`: runtime-selected FastAPI application.
- `server.py`: Uvicorn entry point.
- `settings.py`: validated runtime and Bot Service settings.
