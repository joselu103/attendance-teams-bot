# MCP discovery admission and model selection

MCP discovery is an untrusted capability inventory. The bot retains only its
fourteen allowed read-only names and ignores unknown advertised names, but this
admission is distinct from model-selectable execution. Prompt definitions,
argument validation, execution policy, and result rendering are all bot-owned;
remote descriptions and schemas never reach the model.

`list_my_attendance_events` remains enabled. The expanded policy also exposes a
bot-owned composite other-employee attendance action only when both
`resolve_employee` and `list_attendance_events` are admitted, and exposes
`get_current_attendance` only when its server-current-time schema is admitted.
The bot resolves exactly one model-provided selector and keeps the resolved ID
internal; CRMT makes every authorization decision. Current status accepts only
the six contract statuses, has no `as_of`, and is paged to exhaustion.

Every policy has a bot-owned `reply_language` enum (`en` or `sl`). By explicit
user-approved privacy expansion, successful raw MCP tool results are supplied to
the LLM as data, but never rendered directly or disclosed in logs/errors; final
replies are rejected if they contain returned internal IDs. Remote descriptions
and schemas remain outside the prompt.
