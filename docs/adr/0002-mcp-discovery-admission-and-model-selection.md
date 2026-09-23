# MCP discovery admission and model selection

MCP discovery is an untrusted capability inventory. The bot retains only its
fourteen allowed read-only names and ignores unknown advertised names, but this
admission is distinct from model-selectable execution. Prompt definitions,
argument validation, execution policy, and result rendering are all bot-owned;
remote descriptions and schemas never reach the model.

`list_my_attendance_events` remains the only enabled policy. `list_punch_types`
and `list_locations` are reserved policy placeholders and remain disabled until
Attendance MCP publishes authoritative versioned result schemas and the bot has
localized typed renderers. Employee-targeted, directory, exception, current
attendance, and organization-reporting tools remain unavailable until a verified
employee-selection and authorization/UI design exists.

The enabled policy has a bot-owned `reply_language` enum (`en` or `sl`) and a
versioned safe result projection. It is the only discovered policy exposed to
the LLM; remote descriptions, schemas, and all disabled policies remain outside
the prompt.
