# Domain Docs

Before exploring this repo, read:

- `CONTEXT.md` at the repo root, if it exists.
- `docs/adr/` for decisions relevant to the area being changed.

If either is absent, proceed silently. The domain-modeling workflow creates them only when terminology or decisions need recording.

## File structure

Single-context repo:

/
├── CONTEXT.md
├── docs/adr/
└── src/

Use terminology defined in `CONTEXT.md` when it exists. Surface any conflict with an existing ADR rather than silently overriding it.
