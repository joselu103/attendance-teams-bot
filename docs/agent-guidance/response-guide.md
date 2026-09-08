# Response Guide

## Core Directive
Before answering the user after a prompt, always wait for all tool executions and background operations to fully finish.

## Success Format
When operations succeed:
- Respond using **bullet points only** (maximum 3-5 items).
- Focus strictly on key outcomes, state changes, or test status.
- **DO NOT** include git diffs, code snippets, or lengthy explanations unless explicitly requested by the user.

## Exception (Error Handling)
When operations fail (e.g., failed tests, ruff linter errors):
- You are allowed to exceed the brief bullet point format.
- Provide the exact error trace, root cause analysis, and the proposed fix briefly.