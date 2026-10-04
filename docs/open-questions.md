# Foundry open-question tracker

> **Status values**: `open` (needs research / needs the user) / `answered`
> (settled, linked to the decision log) / `answered[provisional]` (Claude has
> supplied a definition and written it into the docs; the user may overrule) /
> `deferred` (explicitly postponed).
> New questions get the next number; numbers are never reused.

| # | Question | Status | Conclusion / where it went |
|---|---|---|---|
| OQ-1 | V1 product shape: interactive session, or a one-shot executor? | answered | interactive first, with the UI decoupled from the loop -> [D-004](decision-log.md) |
| OQ-2 | What order to build the two model paths in? | answered | the original "ChatGPT login first" fell away with the blocked conclusion; the reordered milestones are in [roadmap.md](roadmap.md) (a local endpoint solves "no API available to verify against") -> [D-009](decision-log.md) |
| OQ-3 | Do the corporate Gateway's protocol assumptions hold? | answered (in part) | the Gateway is multi-model (Claude included) -> a provider-agnostic IR -> [D-006](decision-log.md); the details -> OQ-6 |
| OQ-4 | What is the real motivation for building this? | answered | corporate compliance constraints + learning -> [D-007](decision-log.md) |
| OQ-5 | Is third-party ChatGPT login feasible? | answered | **blocked-with-evidence** ([research/auth.md](research/auth.md)); the personal path becomes an API key -> [D-009](decision-log.md) |
| OQ-6 | The corporate Gateway's **remaining unknowns**: the exact mechanism of intranet auth (an HTTP exchange / an internal executable / browser SSO), the endpoint's shape, the model list | **open (must be settled before M3)** | the protocol part is closed: the OpenAI family goes through Responses, and the token comes from intranet auth -> [D-022](decision-log.md). The M3 entry gate is obtaining a tool-call streaming redaction fixture first. **2026-09-01**: the local OpenClaw gateway has verified the transport layer of both adapters ([research/live-endpoint.md](research/live-endpoint.md)), but its models never emit tool_calls (`tool_choice=required` answers 502), so **the entry gate is still not passed** |
| OQ-7 | Which edit-tool format? | answered | a model-neutral anchored search/replace envelope -> [D-010](decision-log.md) |
| OQ-8 | Use a local OpenAI-compatible endpoint as a dev-only backend? | answered | accepted (the user's second round, confirmed along with D-009); development smoke tests only, not a product path |
| OQ-9 | Does session resume make V1? | answered | the schema is replayable, the feature is V2 -> [D-012](decision-log.md) |
| OQ-10 | Language convention for docs and code | answered | everything -- docs, code, identifiers, comments -- is in English |
| OQ-11 | What was `read_artifact` meant to be? | answered | defined as retrieving over-limit tool output that was spilled to disk -> [D-015](decision-log.md) (confirmed in round 3) |
| OQ-12 | Dirty working-tree policy | answered | warn and continue, plus a forced ASK on writes to dirty files -> [D-011](decision-log.md) |
| OQ-13 | Which single shell for run_command? | answered | **PowerShell 5.1** (preinstalled, zero dependencies; the segmenter follows 5.1 syntax, so no `&&`; the prompt tells the model) -> [D-018](decision-log.md) |
| OQ-14 | Which repository do the golden acceptance tasks run against? | answered | a small sample repository built as a fixture and committed here; corporate repositories are an M3 addition -> [D-020](decision-log.md) |
| OQ-15 | Who writes and ships the managed policy layer (the corporate DENY floor): IT pushing `C:\ProgramData\Foundry\policy.toml`? bundled with an internal wheel? or no managed layer in V1 at all (while this is personal use)? | **open** | does not block the design (the layer mechanism is already in place); must be settled before a corporate deployment |
| OQ-16 | Headless `foundry exec` in V1 (folded into M4) or V2? | answered | **in V1** -- the event architecture made it nearly free, and it is implemented (ASK->DENY fail-closed, `--json` event stream) |
| OQ-17 | Does a repository instruction file (`FOUNDRY.md`/`AGENTS.md`) make V1? | answered | yes, delivered in M2 (trust gating + a byte ceiling; verification commands are prioritised task > repository > model) -> [D-019](decision-log.md) |
| OQ-18 | Open source, and under what license? | answered | not open source for now, license deferred -> [D-021](decision-log.md) |
| OQ-19 | Default session retention (count / days / size ceiling)? Do artifacts need encryption at rest? | open (must be settled before M2) | Claude leans toward: retain 30 days by default plus a size ceiling, with manual deletion available; no promise of encryption that has not been verified |
