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
The bounded Teams presentation rendered by code from a reply-safe projection.
_Avoid_: attendance authority, source ordering

**Presentation policy**:
A bot-owned contract fixing one action's permitted fields, omissions,
localization, renderer, and tests.
_Avoid_: prompt convention, source schema

**Reply-safe projection**:
The minimized typed result view eligible for model context and Teams rendering.
It excludes internal IDs, notes, locations, and other source-only fields.
_Avoid_: raw tool result

**Reply batch**:
A validated non-empty ordered sequence of Teams messages. History date groups are
atomic; delivery stops on the first send failure.
_Avoid_: retry queue, fragmented date group

## MCP catalog policy

**Discovery admission**:
The bot's validation and retention of the intersection between an authenticated
MCP discovery response and its fourteen-name read-only allowlist. It is not
permission to expose or execute every admitted tool.
_Avoid_: legacy catalog, executable catalog

**Model-selectable tool**:
A discovery-admitted tool with a bot-owned prompt definition, argument policy,
and safe typed renderer. Only requester-scoped attendance events are currently
model-selectable.
_Avoid_: discovered tool, remote schema

**Reply-safe result view**:
The presentation-policy projection supplied to the LLM after a successful
read-only tool call. It is data, never instructions, and cannot contain IDs,
notes, locations, or raw source records.
_Avoid_: raw tool result, attendance authority
