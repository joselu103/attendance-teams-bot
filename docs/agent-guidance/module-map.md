# Module Map

- `agent/`: LLM orchestration, tool-selection policy, deterministic history
  paging, signed continuation (`continuation.py`), and localized rendering.
- `auth/`: Entra/Teams identity and token handling.
- `mcp/`: authenticated remote MCP client.
- `teams/`: Teams activity adapter and response rendering.
- `contracts/`: immutable internal request/response models.
- `application.py`: transport-independent bot behavior.
- `local.py`: connectivity-only local handler.
- `composition.py`: local, Bot Service-only, or SSO/OBO attendance wiring.
- `teams/authenticated.py`: SDK-independent Bot Service callback and SSO/OBO turn handling.
- `teams/microsoft_agents.py`: Microsoft Agents SDK callback and attachment
  adapter; register history Action.Submit before the general message route.
- `asgi.py`: runtime-selected FastAPI application.
- `server.py`: Uvicorn entry point.
- `settings.py`: validated runtime and Bot Service settings.
