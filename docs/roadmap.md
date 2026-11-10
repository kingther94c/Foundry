# Foundry — Roadmap

> Ordering principle (adopted from architecture critique #1): the largest
> uncertainty never sits on the critical path, and every milestone closes on
> "runnable + acceptable".
> The ordering error in v0.1 has been fixed: the original "ChatGPT login
> first" fell away with the blocked conclusion ([D-009](decision-log.md)); the
> personal API-key path and the corporate Gateway are in the same protocol
> family, so building the skeleton first is no longer blocked on auth.

## Status (2026-11-10)

| Milestone | Status | Notes |
|---|---|---|
| M0 skeleton + frozen interfaces | **done** | the four interfaces (IR / events / backend / session) are frozen; replay + `foundry record` are ready |
| M1a file tools + policy | **done** | attack table and decision table both green |
| M1b run_command + segmenter | **done** | Job Object process-tree cleanup leaves no orphans in practice |
| M2 personal path E2E + acceptance set | **done** | 8 golden scenarios; the `finish` evidence chain; `foundry report` |
| M3 corporate Gateway | **partial** | unchanged since 2026-09-01: the entry gate (a tool-call streaming fixture from the Gateway) is not passed, and [OQ-6](open-questions.md) is open |
| M4 packaging hardening + disclosure | **done** | the wheelhouse is not hash-pinned yet ([OQ-20](open-questions.md)) |
| Codex parity, items 1-106 | **done** | 2026-11-06 to 2026-11-09, D-039 to D-145; the table below |

4657 tests pass (one is skipped) on a machine with no network and no credentials. Every
feature since D-036 has been verified only against scripted backends: the
local gateway that served the first live checks has answered nothing since
2026-11-04, and there is no API key on the development machine. What comes
next is in [After parity](#after-parity-2026-11-10-prove-it-then-ship-it).

### Status as of 2026-09-01, after the first live endpoint check

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

## Toward codex parity (2026-11-06 to 2026-11-09, done)

The user's goal on 2026-11-06 was a Foundry that is "basically like codex":
what a codex user reaches for daily, first, each item on its own branch with
a D-entry, tests and a mutation check. All 106 items landed; the D-entry of
each holds what was decided and why. The list as it was worked through, with
a line of detail per item, is in git history (`git show
59d1ea0:docs/roadmap.md`).

| # | Item | Decision |
|---|---|---|
| 1 | Slash commands and custom prompts | D-039 |
| 2 | Compaction by summary | D-040 |
| 3 | Project instructions layered as codex does | D-041 |
| 4 | The CLI flags codex users expect | D-042 |
| 5 | An MCP client | D-043 |
| 6 | Reasoning summaries shown where the provider streams them | D-044 |
| 7 | Images | D-045 |
| 8 | A Python-only repo map | D-046 |
| 9 | Changing the mode and the model inside a session | D-047 |
| 10 | Thinking kept valid after Foundry's own edits | D-048 |
| 11 | A `notify` program run when a turn ends | D-050 |
| 12 | `exec --output-schema FILE` | D-051 |
| 13 | A message of several lines at the prompt | D-052 |
| 14 | `exec -` | D-053 |
| 15 | How much context is left, in view | D-054 |
| 16 | `!COMMAND` at the prompt | D-055 |
| 17 | Going back to an earlier message | D-056 |
| 18 | A resume picker | D-057 |
| 19 | `@path` in a message | D-058 |
| 20 | How to continue, as a session ends | D-059 |
| 21 | `exec` names its session | D-060 |
| 22 | Reasoning effort on every protocol | D-061 |
| 23 | Ctrl+C stops the turn, not the session | D-062 |
| 24 | A task's ending is not the session's | D-063 |
| 25 | `/mcp` | D-064 |
| 26 | A screenshot from the clipboard | D-065 |
| 27 | `/review` of a branch or a commit | D-066 |
| 28 | The endpoint's models | D-067 |
| 29 | What a command printed, in view | D-068 |
| 30 | Working, and for how long | D-069 |
| 31 | The answer as Markdown | D-070 |
| 32 | Named placeholders in custom prompts | D-071 |
| 33 | `-C DIR` | D-072 |
| 34 | How long a task took | D-073 |
| 35 | The newest line of a running command | D-074 |
| 36 | What a patch changed, in view | D-075 |
| 37 | Quiet reads, failures that say why | D-076 |
| 38 | Ctrl+C twice to end the session | D-077 |
| 39 | `foundry run TASK` goes on | D-078 |
| 40 | Told when an approval waits | D-079 |
| 41 | Which MCP tools are offered | D-080 |
| 42 | MCP servers at an address | D-081 |
| 43 | `foundry mcp list \| add \| remove` | D-082 |
| 44 | Which variables a command gets | D-083 |
| 45 | A backend's own key, headers and query | D-084 |
| 46 | Retries you can see | D-085 |
| 47 | Which files are instructions | D-086 |
| 48 | A key piped to login | D-087 |
| 49 | Resuming as codex spells it | D-088 |
| 50 | Reasoning carried across rounds on Responses | D-089 |
| 51 | How long the answers are | D-090 |
| 52 | A setting nobody reads is an error in the files too | D-091 |
| 53 | An unattended run says what it runs with | D-092 |
| 54 | Tab completion | D-093 |
| 55 | When automatic compaction starts | D-094 |
| 56 | The context window on every protocol | D-095 |
| 57 | The most one reply may be | D-096 |
| 58 | codex's command-line flags | D-097 |
| 59 | `-c` with codex's names | D-098 |
| 60 | A stream cut part-way asked again | D-099 |
| 61 | Settings the protocol cannot send | D-100 |
| 62 | codex's slash commands | D-101 |
| 63 | MCP resources | D-102 |
| 64 | `foundry mcp-server` | D-103 |
| 65 | Approvals asked of the MCP client | D-104 |
| 66 | `foundry mcp get NAME` | D-105 |
| 67 | A cited file opens in the editor | D-106 |
| 68 | `@name` finds the file | D-107 |
| 69 | A resumed session shows its conversation | D-108 |
| 70 | The working line after a reasoning summary | D-109 |
| 71 | codex's names in an MCP server's table | D-110 |
| 72 | A url server's own headers | D-111 |
| 73 | `/prompts:NAME` | D-112 |
| 74 | Agent skills | D-113 |
| 75 | Skills found and switched as codex's | D-114 |
| 76 | Lifecycle hooks, as codex's | D-115 |
| 77 | UserPromptSubmit and Stop hooks | D-116 |
| 78 | SessionStart, SessionEnd and Interrupt hooks | D-117 |
| 79 | PreCompact and PostCompact hooks | D-118 |
| 80 | PermissionRequest hooks | D-119 |
| 81 | Background hooks | D-120 |
| 82 | MCP tool hooks | D-121 |
| 83 | Model-request hooks | D-122 |
| 84 | `/copy [N]` | D-123 |
| 85 | `/export [PATH]` | D-124 |
| 86 | `/rename [NAME]` | D-125 |
| 87 | `/fork` and `foundry fork` | D-126 |
| 88 | `/goal` | D-127 |
| 89 | `foundry archive`, `unarchive` and `delete`; `/archive` and `/delete` | D-128 |
| 90 | `/side` and `/btw` | D-129 |
| 91 | `/recap`, `/clear`, `/rollout`, `/pwd` | D-130 |
| 92 | `foundry review` | D-131 |
| 93 | `exec --ephemeral` | D-132 |
| 94 | `exec --fork` and `exec fork` | D-133 |
| 95 | `/resume [SESSION]` | D-134 |
| 96 | `foundry policy check` | D-135 |
| 97 | `exec --color` | D-136 |
| 98 | `exec --trust` | D-137 |
| 99 | `/warnings` | D-138 |
| 100 | The terminal title, and `/title` | D-139 |
| 101 | The rest of codex's slash commands | D-140 |
| 102 | Sub-agents | D-141 |
| 103 | `developer_instructions` and `compact_prompt` | D-142 |
| 104 | `review_model` | D-143 |
| 105 | `[features]` | D-144 |
| 106 | Memories | D-145 |

Not planned for now: a Windows sandbox (codex's needs dedicated local users,
WFP filters and an elevated service; the threat model documents its absence),
hosted web search, interactive PTY sessions for long-running commands, and
codex's desktop notifications (`tui.notifications`), sent while its
terminal has lost focus, which a session reading lines cannot tell -- a
`notify` program (D-050) is told when a turn ends, and the terminal's title
says when an approval waits (D-139).

## After parity (2026-11-10): prove it, then ship it

[D-146](decision-log.md): no more parity items for now. The order of
priorities set on 2026-08-29 ([D-007](decision-log.md)) is auditable, then
few dependencies, then a clear implementation, then the number of features;
the last month went to the last of those. What is owed instead:

- **P0 -- take stock.** A full run of the suite as the baseline; the status
  above brought up to date; the parity list folded into its table; merged
  branches and worktrees no session uses cleared away.
- **P1 -- verify against a model.** The golden tasks on all three protocols,
  and the features only a model exercises end to end: compaction, reasoning
  carried across turns, retries after a cut stream, resume, sub-agents,
  memories, MCP tools, hooks, reviews, `exec --output-schema`. Until an
  endpoint is available again, the model is played by an agent that sees only
  Foundry's requests, behind a local relay that speaks each protocol and can
  inject failures (429s, cut streams, error events). What it finds becomes a
  fix with a test, and every recorded stream that shows a defect becomes a
  fixture. A real endpoint, when there is one, repeats the same scenarios.
  First round (2026-11-10): the golden fix, a feature in `tools/`, a run
  stopped by its budget and resumed, compaction, a sub-agent with
  `--output-schema`, and a review, across the three protocols, all completed
  with their evidence accepted and the credential in no file; injected 429s
  and cut streams were recovered. One defect: a failure reported inside a
  stream ([D-147](decision-log.md)).
  Second round: a REPL session with `/compact` and `/undo`, a refactor
  under injected 429s, overloads, a cut stream and an error event, an MCP
  tool with PostToolUse and Stop hooks, and memories written, merged and
  given to the next session -- all completed. Defects: a read-only task and
  an unattended run offered tools they could only have refused, and four
  small ones ([D-148](decision-log.md), [D-149](decision-log.md)).
- **P2 -- review the new attack surface.** Hooks, the MCP server and client
  (HTTP, elicitation, resources), memories, sub-agents and skills arrived
  after the last adversarial review (round six, 2026-09). Each gets the same
  treatment the policy pipeline had, and the threat model is brought up to
  date.
  Done (2026-11-10): four reviewers, each proving what it found, covered
  hooks and approvals, the MCP client, `foundry mcp-server`, and memories,
  skills and sub-agents. Fixed: a client's `config` that could loosen the
  server's ([D-150](decision-log.md)), approvals that did not show what would
  run ([D-151](decision-log.md)), `dont_ask` asking and hooks not told the
  policy's reason ([D-152](decision-log.md)), what a child process is given
  ([D-153](decision-log.md)), memories ([D-154](decision-log.md)), an MCP
  server hanging or ending a session ([D-155](decision-log.md)), the
  server's one task to a repository ([D-156](decision-log.md)), and skills
  and sub-agents ([D-157](decision-log.md)); each brought its threat-model
  row up to date.
- **P3 -- make the code smaller to read.** `cli/app.py` (2,706 lines),
  `core/runtime.py` (1,710), `cli/repl.py` (1,709) and `core/config.py`
  (1,391) are split along their seams, with no change of behaviour: the suite
  and a mutation check are the gate.
- **P4 -- the corporate machine (M3).** A hash-pinned wheelhouse
  ([OQ-20](open-questions.md)), the managed policy layer
  ([OQ-15](open-questions.md)), session retention
  ([OQ-19](open-questions.md)), the Gateway's fixtures and intranet auth
  ([OQ-6](open-questions.md)), the canary leak suite on the real credential
  path, and one golden task completed on a clean corporate install. Blocked
  on information from the corporate side.
- **P5 -- use it.** Further ResearchOS versions built with Foundry, as
  `tools/` and ResearchOS were, so that what Foundry needs next is decided by
  what gets in the way of real work rather than by a list.

Dated, outside Foundry itself: the macro run's scorings fixed in its plan
(2026-12-07 and 2027-02-08), and, if wanted, weekly forward snapshots under a
plan of their own ([ResearchOS macro run](../ResearchOS/runs/2026-11-macro-calibration-run/README.md)).

## V2 candidates (explicitly no commitment to order)

~~session resume~~ (landed, D-035) | ~~LLM summarisation compaction~~
(landed, D-040) | ~~a visible `update_plan`~~ (landed, D-037) | ~~skills and
slash commands~~ (slash commands and custom prompts landed, D-039) | ~~enabling the subagent seam~~ (landed as `spawn_agent`, D-141) | ~~an MCP bridge~~ (landed as the
MCP client, D-043) | ~~checkpoint/undo~~ (landed as patch undo, D-036) | ~~a
Python-only repo map~~ (landed, D-046) | research into a restricted-token
sandbox.
