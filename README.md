# Attendance Teams Bot

A Python service that will receive Microsoft Teams messages and act as an
LLM-powered, authenticated MCP client for Attendance CRMT.

The bot is intentionally separate from the Attendance CRMT service. It must not
access the attendance database or implement attendance authorization rules.

## Current local slice

The service has a dependency-injected application composition boundary and
protocols for Teams, identity, MCP, and LLM adapters. The local command confirms
that no external adapter is configured; it makes no network calls.

The real Microsoft Entra token flow, MCP endpoint, and LLM provider remain
intentionally unconfigured until the cross-repository integration contract is
agreed.

## Quick start

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
env -u PYTHONPATH uv run attendance-teams-bot
```

## Verification

```bash
env -u PYTHONPATH uv run pytest
env -u PYTHONPATH uv run ruff check .
env -u PYTHONPATH uv run ruff format --check .
env -u PYTHONPATH uv run mypy
```

## Configuration

Copy `.env.example` to an untracked `.env` only when a real MCP endpoint is
available. Deployed configuration must come from the platform secret manager.
Never put credentials or tokens in `.env.example`, source control, logs, or chat.

## Layout

```text
src/attendance_teams_bot/
├── agent/      # LLM boundary and response contracts
├── auth/       # authenticated Teams-user identity boundary
├── mcp/        # authenticated Attendance CRMT MCP client boundary
├── teams/      # Microsoft Teams transport boundary
├── cli.py      # local executable entry point
├── main.py     # dependency-injected application composition
└── settings.py # typed environment configuration

tests/unit/     # local composition, settings, and CLI behavior
```
