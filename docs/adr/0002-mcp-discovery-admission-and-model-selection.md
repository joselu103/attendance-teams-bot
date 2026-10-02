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
internal; CRMT makes every authorization decision. Current status accepts an
optional non-empty `statuses` array of the six contract statuses, has no
`as_of`, and is paged to exhaustion through one MCP request. Omitted `statuses`
requests the whole office; status grouping is code-owned in the fixed product
order.

Every policy has a bot-owned `reply_language` enum (`en` or `sl`); unsupported
languages fail closed. Successful MCP results are converted to typed reply-safe
projections before entering model context. History exposes localized date/time
and controlled translated punch labels, preserving unmapped labels safely; it
omits IDs, notes, and locations. Current attendance exposes only names under
translated non-empty status groups, with no timestamps, locations, or event data.
Search remains deterministic and directory-safe.

After a result, the model returns a validated presentation plan containing only
a short title and optional context. Code owns factual blocks, chronology,
completeness, status membership, translations, empty states, errors, and employee
selection. A new model-admitted tool needs an explicit presentation policy and
tests before successful results may be exposed. Long history replies form a
validated non-empty ordered batch only at complete date-group boundaries; Teams
delivery is sequential and stops without blind retry on a send failure. Remote
descriptions and schemas remain outside the prompt.

Before authentication, the configured LLM receives only bot-owned definitions of
all currently supported read-only actions, the message, and an unverified Teams
display name. This provider disclosure is deliberately limited: the display name
may personalize a no-tool greeting, but is never identity, authorization,
employee mapping, or a tool argument. A structured no-tool response is accepted
only as a greeting, attendance clarification, or attendance-only scope guidance;
all other replies fail closed. A validated action selection then triggers SSO,
OBO, authenticated MCP discovery, and live-catalog re-admission, which remains
the authority for execution. This trades a limited pre-auth provider disclosure
for avoiding unnecessary token and MCP processing for greetings and unsupported
questions.
