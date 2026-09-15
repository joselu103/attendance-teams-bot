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
