# Bot-controlled attendance history pagination

The unlimited inclusive history contract supersedes date window splitting and
all-page aggregation for ATTENDANCE-HISTORY-PAGINATION-001. The bot reads one
full-period page of up to 50 events, then lets the user request the next page
with a stateless signed button; this keeps each turn bounded without silently
capping the total accessible history.

Both dates are required Europe/Ljubljana calendar boundaries, shown in the
reply; clear relative periods resolve to explicit dates and incomplete/ambiguous
periods require a fresh complete request. Future ends are accepted. The initial
administrator selector resolves once; its signed continuation retains that
target, while self history never carries an employee selector.

Versioned HMAC-SHA256 context binds scope, dates, administrator target when
applicable, fixed limit 50, next offset, language, and authenticated SDK
Teams tenant/AAD-user/conversation. A persistent external SecretStr key is
mandatory for enabled attendance and shared across replicas: no startup key
generation, no expiry, restart survival, and key replacement invalidates buttons.
Every click reauthenticates via SSO/one OBO and live MCP admission, then reads one
page directly without model selection or completion; REST rechecks authorization.

Pages read live data rather than snapshots, and repeat clicks use the original
position. Invalid metadata/signatures/bindings fail safely. Localized complete
date-group text batches precede a separate card; failure stops delivery without
retry. Final/empty pages have no button. Current-status paging and unrelated
report restrictions are unchanged. REST 1.0.0 and pre-release MCP 1.3.0 remain
compatible; local source verification clears no external readiness gate.
