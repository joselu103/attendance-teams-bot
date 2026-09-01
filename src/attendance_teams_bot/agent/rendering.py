from attendance_teams_bot.mcp.contracts import AttendanceEvent, AttendanceEventPage

UNAVAILABLE_REPLY = "Attendance data is temporarily unavailable. Please try again later."
TOOL_FAILURE_REPLIES = {
    "INVALID_ARGUMENT": "Check the attendance date range and try again.",
    "FORBIDDEN": "You do not have permission to view that attendance.",
    "IDENTITY_UNMAPPED": (
        "Your Teams account is not linked to an active attendance employee. "
        "Contact an administrator."
    ),
    "IDENTITY_AMBIGUOUS": "Your Teams account cannot be linked safely. Contact an administrator.",
    "BACKEND_UNAVAILABLE": UNAVAILABLE_REPLY,
    "AUTHENTICATION_REQUIRED": "Please sign in and try again.",
    "TOKEN_INVALID": "Please sign in and try again.",
    "INTERNAL_ERROR": UNAVAILABLE_REPLY,
    "CORRELATION_ID_INVALID": UNAVAILABLE_REPLY,
}


def render_attendance_page(page: AttendanceEventPage) -> str:
    if not page.items:
        return "No attendance events were found for that date range."
    response = "\n".join(_render_attendance_event(event) for event in page.items)
    if page.next_offset is not None:
        response += "\nShowing the first 50 events; more events are available."
    return response


def _render_attendance_event(event: AttendanceEvent) -> str:
    checked_in = (
        event.checked_in_at.isoformat(sep=" ", timespec="minutes")
        if event.checked_in_at
        else "Open"
    )
    checked_out = (
        event.checked_out_at.isoformat(sep=" ", timespec="minutes")
        if event.checked_out_at
        else "Open"
    )
    punch_type = event.punch_type or "Unspecified attendance"
    location = event.location or "Unspecified location"
    return f"{checked_in}–{checked_out}: {punch_type} at {location}"
