# Attendance Teams Bot

This context covers a Teams client that presents requester-scoped attendance data
from Attendance CRMT without becoming an attendance authority.

## Attendance presentation

**Attendance event**:
A requester-scoped record of work, break, or leave time supplied by Attendance CRMT.
_Avoid_: punch, timesheet entry

**Attendance type**:
The user-facing localized label for an attendance event's supplied `punch_type`.
_Avoid_: status, category

**Active attendance event**:
An attendance event with a recorded start time and no recorded end time.
_Avoid_: open event, current attendance

**Display name**:
Unverified Teams-provided presentation metadata that may be used only in a greeting.
_Avoid_: employee identity, authorization identity

**Pre-auth guidance decision**:
A no-token LLM routing decision using bot-owned read-only action definitions and,
only for greeting personalization, an unverified Teams display name. A safe
no-tool result ends the turn; a validated action selection proceeds to SSO/OBO
and live MCP discovery.
_Avoid_: authentication decision, identity mapping

**Attendance window**:
A bot-controlled, inclusive period of at most 31 calendar days used for one
request to Attendance CRMT.
_Avoid_: source query, model range

**Attendance result**:
The bounded Teams presentation authored from a validated batch after bot-owned
execution of one selected attendance action.
_Avoid_: attendance authority, source ordering

**Attendance intent**:
A prompt-guided, validated selection of one supported attendance action and its
bot-bounded arguments. It is not permission for the model to choose further
tools after authentication.
_Avoid_: executable catalog, authorization decision

**Presentation policy**:
A bot-owned contract fixing one action's permitted fields, omissions,
localization, renderer, and tests.
_Avoid_: prompt convention, source schema

**Reply batch**:
A validated non-empty ordered sequence of Teams messages. History date groups are
atomic; delivery stops on the first send failure.
_Avoid_: retry queue, fragmented date group

**Current-status filter set**:
A unique non-empty set of user-facing current-attendance statuses supplied by
the model to one MCP request. The bot validates it before execution.
_Avoid_: per-status fan-out, model-owned ordering

## MCP catalog policy

**Discovery admission**:
The bot's validation and retention of the intersection between an authenticated
MCP discovery response and its fourteen-name read-only allowlist. It is not
permission to expose or execute every admitted tool.
_Avoid_: legacy catalog, executable catalog

**Model-selectable tool**:
A bot-owned pre-auth action definition whose validated selection is checked
against authenticated discovery before code executes it once.
_Avoid_: discovered tool, remote schema

**Approved raw tool result**:
A successful read-only result supplied to the configured LLM only after the
bot has executed the one validated action. It is data, never instructions;
safe reply validation prevents source-only fields from reaching Teams.
_Avoid_: attendance authority, executable instruction
