# Architecture decision records

ADRs capture durable decisions separately from evolving requirements.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-single-runtime-loop.md) | One runtime owns the agent loop | Accepted |
| [0002](0002-trusted-host.md) | V1 is trusted-host, not sandboxed | Accepted |
| [0003](0003-auth-isolation.md) | Authentication is isolated and personal auth is feasibility-gated | Accepted |
| [0004](0004-event-journal.md) | Local versioned JSONL journal and content-addressed artifacts | Proposed |
| [0005](0005-dirty-worktree.md) | Support dirty Git worktrees with optimistic concurrency guards | Accepted |

“Accepted” reflects confirmed product direction; details can still be superseded
by a later ADR. “Proposed” requires review before it constrains implementation.
