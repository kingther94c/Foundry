# Foundry — Roadmap

> Ordering principle (adopted from architecture critique #1): the largest
> uncertainty never sits on the critical path, and every milestone closes on
> "runnable + acceptable".
> The ordering error in v0.1 has been fixed: the original "ChatGPT login
> first" fell away with the blocked conclusion ([D-009](decision-log.md)); the
> personal API-key path and the corporate Gateway are in the same protocol
> family, so building the skeleton first is no longer blocked on auth.

## Status (2026-09-01, after the first live endpoint check)

| Milestone | Status | Notes |
|---|---|---|
| M0 skeleton + frozen interfaces | **done** | the four interfaces (IR / events / backend / session) are frozen; replay + `foundry record` are ready |
| M1a file tools + policy | **done** | attack table and decision table both green |
| M1b run_command + segmenter | **done** | Job Object process-tree cleanup leaves no orphans in practice |
| M2 personal path E2E + acceptance set | **done** | 8 golden scenarios; the `finish` evidence chain; `foundry report` |
| M3 corporate Gateway | **partial** | the `responses` adapter has run against a **real** Responses endpoint (streaming + usage, [research/live-endpoint.md](research/live-endpoint.md)); **the entry gate is still not passed** -- that gateway's models do not emit tool_calls, and a tool-call streaming fixture is still needed ([OQ-6](open-questions.md)) |
| M4 packaging hardening + disclosure | **done** | a clean venv `--no-index` install passes in practice; the threat model is documented; `foundry exec` made V1 |

1436 tests pass on a machine with no network and no credentials. **The first
live endpoint check, 2026-09-01**: both adapters completed a full round against
the local OpenClaw gateway (streaming, usage, and no leak of the real token
included), and exposed 4 defects a scripted backend could never reach. The
audit of the proxy path that followed found **the single worst defect in the
project**: with a corporate proxy configured, the API key crosses the CONNECT
tunnel **in plaintext** ([threat-model.md](threat-model.md) §3(b3)) -- and that
is the default path under the default `base_url`, which six rounds of
adversarial review never touched, because no test in `tests/` had ever set a
proxy environment variable while exercising `HttpClient`. The details are in
[research/live-endpoint.md](research/live-endpoint.md). The personal API-key
path and the corporate Gateway's tool-call behaviour both remain unverified.

Review round six closed on a **clean-room end-to-end** run (rebuild the
wheelhouse -> install into a fresh venv with `--no-index` -> use the installed
package for a real task: failing tests, a patch, a green re-run, and a finish
citing the event id from its own tool output). All 14 checks passed. The
defects that round found had a different shape from the previous five: not
inside any one layer, but on the seam between two -- see
[threat-model.md](threat-model.md) §3(b2).

## M0 — Walking skeleton (the interface-freeze milestone)

**Goal**: `foundry` can open an interactive session on the sample repository
and answer a question using read-only tools, driven by the event stream
throughout.

- Freeze the four interfaces: the IR (conversation.py), the event protocol
  (events.py), the ModelBackend protocol, and the SessionStore line envelope.
- The loop + the ContextManager projection (no masking) + 2 tools
  (`read_file`, `list_files`) + a minimal rich UI.
- Backends: `replay` + `openai_compat` (smoke-tested against a **local
  endpoint** -- verifiable without credentials, which answers the "no API in
  the development environment" constraint); `foundry record` for the
  fixture re-recording workflow.
- SessionStore persistence + the first golden transcript regression test.
- **Acceptance** (revised in review, splitting binding from smoke): binding =
  the replay suite is green on a machine with no network and no credentials
  (machine-decidable) + the crash-recovery case (a truncated journal is judged
  `interrupted`, not completed); smoke = one session against a local endpoint
  (an optional development environment; the user installs LM Studio/Ollama
  themselves) containing at least one read_file call and terminating cleanly as
  `completed(no_changes)` -- the termination status and event sequence are
  machine-asserted, while answer quality is eyeballed and is not a gate.

## M1a — File tools + the policy pipeline (revised in review: the original M1 was too fat for a solo developer, so it was split in two)

**Goal**: it can change code, and it can be stopped. **Not blocked by OQ-13,
so work can start immediately.**

- apply_patch (the anchored format + per-file atomicity + a leniency gradient),
  the workspace boundary module, and the remaining file/git tools.
- The six-step PolicyEngine pipeline (covering file tools first) + the approval
  UI (once/session/always) + the dirty working-tree policy (a baseline plus an
  ASK rule for dirty files).
- **Acceptance**: every path-escape sample is refused (junctions, ADS, `..`,
  device names, drive-relative, UNC); an out-of-bounds or malformed tool call
  is refused and the loop survives; the three accept_edits / dirty-file /
  persisted-rule cases (the test table in requirements §4.1); ASK timeout =
  DENY; **the dirty working-tree matrix** (requirements §8.5, including a
  concurrent modification producing a `stale` write refusal).

## M1b — run_command + the segmenter (the shell is settled: PowerShell 5.1, [D-018](decision-log.md))

**Goal**: it can run commands, and it can clean up after them.

- run_command (Job Object process trees, env filtering, encoding fallback, an
  output ceiling with artifact spillover) + the command segmenter (alias
  normalisation + can't-parse -> ASK) + the full circuit-breaker table +
  audit.jsonl.
- **Acceptance**: a DENY cannot be overridden by an ALLOW (at either the user
  or the project layer); cancelling a running multi-level child process tree
  leaves no orphans; the segmenter attack sample table is green; every
  breaker alias-bypass sample is refused.

## M2 — Personal path E2E + the acceptance task set

**Goal**: a real cloud model completes the golden tasks.

- `foundry login` (API key + DPAPI storage); the error taxonomy +
  retry/backoff; token accounting + observation masking; the `finish` tool +
  the termination state machine + ValidationClaim checking; `foundry sessions
  [list|show|export]` (revised in review: the V1 CLI requirements needed an
  owner); the `FOUNDRY.md` repository instruction file
  ([D-019](decision-log.md)).
- A golden task set of 5-10 tasks (fix a test / add a test / refactor / answer
  a question; the sample repository is a fixture built and committed here,
  [D-020](decision-log.md)) + a failure-telemetry report script.
- **Acceptance**: requirements §8.2 + §8.5 (the personal half); the `completed`
  forgery cases (citing a nonexistent event, or an exit code that does not
  match) are refused.

## M3 — Corporate Gateway

**Goal**: usable on a corporate machine. **The entry gate
([D-022](decision-log.md)): obtain the Gateway's tool-call streaming redaction
fixtures first** (an ordinary response / a tool call and its continuation /
usage / rate limiting / a dropped stream / a malformed event), and only then
start implementing -- "Responses-compatible" may cover conversation without
covering agentic tool continuation.

- The `responses` adapter (mandatory) + CredentialSource (the intranet auth
  mechanism, [OQ-6](open-questions.md)) + TLS/proxy testing against the real
  thing (including `FOUNDRY_CA_BUNDLE`).
- Capability probing + degradation paths; per-model profiles (edit_format and
  prompt variants for the Claude family); the managed policy layer (the
  ProgramData DENY floor).
- **Acceptance**: at least one golden task completes in the corporate
  environment; the **canary leak suite** (requirements §8.4) passes on the real
  credential path; token expiry / re-acquisition / rate limiting / dropped
  streams / malformed calls / timeouts each have a bounded test; capability
  probe results are persisted; a managed DENY is demonstrably not relaxable.
  Claude may accept or defer individually, based on the protocol behaviour
  actually observed.

## M4 — Packaging hardening + disclosure

**Goal**: distributable, and accountable.

- The wheelhouse pipeline (pip-compile hashes -> download -> a CI completeness
  check); clean-machine install acceptance (requirements §8.1).
- The threat model document + the first-run disclosure copy; a secrets choke
  point audit; `foundry doctor`.
- Headless `foundry exec` (ASK->DENY fail-closed, JSONL event output) -- the
  event architecture should make it nearly free, so whether it lands in V1 or
  slips depends on the remaining budget.

## V2 candidates (explicitly no commitment to order)

session resume (the schema is ready) | LLM summarisation compaction (the
Compacted event is already reserved) | a visible `update_plan` | skills and
slash commands | enabling the subagent seam | an MCP bridge (the IR already
lines up) | shadow-git checkpoint/undo | a Python-only repo map (stdlib ast) |
research into a restricted-token sandbox.
