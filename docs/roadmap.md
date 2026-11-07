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

## Toward codex parity (2026-11-06, the user's goal: "basically like codex")

The V2 work now has an order: what a codex user reaches for daily, first.
Each item lands on its own branch with a D-entry, tests and a mutation check.

1. ~~Slash commands and custom prompts~~ -- `/help /status /diff /plan /approve
   /undo /review /init /new`, `~/.foundry/prompts/` (landed, D-039).
2. ~~Compaction by summary~~ -- `/compact [focus]`, and automatically once
   masking leaves the context past 80% of the window (landed, D-040).
3. ~~Project instructions layered as codex does~~ -- the user's own file from
   the Foundry folder, then the repository's from its top down to the working
   directory (landed, D-041).
4. ~~The CLI flags codex users expect~~ -- `-m/--model`, `--mode`,
   `-c key=value`, `--profile`, and `exec --output-last-message` (landed, D-042).
5. ~~An MCP client~~ -- stdio servers' tools offered to the model under the
   same policy pipeline (landed, D-043).
6. ~~Reasoning summaries shown where the provider streams them~~ (landed,
   D-044).
7. ~~Images~~ -- attaching one to a task (`-i`, `/image`), a `view_image`
   tool, and an MCP tool's images, where the backend accepts them (landed,
   D-045).
8. ~~A Python-only repo map~~ -- the `repo_map` tool, from the standard
   library's `ast`, ranked by imports (landed, D-046).

With the list above done, the next gaps a codex user would notice day to day
(2026-11-07):

9. ~~Changing the mode and the model inside a session~~ -- `/mode NAME` and
   `/model NAME` (landed, D-047).
10. ~~Thinking kept valid after Foundry's own edits~~ -- Anthropic binds each
    thinking block to the conversation before it; after a masked result, a
    rebuilt permissions paragraph or a resume's new system prompt, the blocks
    made before the change are left out of later requests, instead of being
    sent for the API to drop, and warn about, every round (landed, D-048).
11. ~~A `notify` program run when a turn ends~~ -- `notify = [...]` in the
    user's config, told the turn's JSON as codex tells it (landed, D-050).
12. ~~`exec --output-schema FILE`~~ -- finish's `result`, checked against the
    schema by Foundry and printed as the run's only output (landed, D-051).

Every item of this list has landed (2026-11-07).

What a codex user still notices day to day, next:

13. ~~A message of several lines at the prompt~~ -- a paste is one message,
    and so is a block between lines of three quotes (landed, D-052).
14. ~~`exec -`~~ -- the task read from stdin, as `codex exec -` reads it
    (landed, D-053).
15. ~~How much context is left, in view~~ -- a line above the prompt, as
    codex shows it under its input (landed, D-054).
16. ~~`!COMMAND` at the prompt~~ -- a command the user runs, its output told
    to the model with the next message (landed, D-055).
17. ~~Going back to an earlier message~~ -- `/rewind [N]`, as codex's Esc Esc;
    the files stay as they are (landed, D-056).
18. ~~A resume picker~~ -- `foundry resume` with no id lists this workspace's
    recent sessions and asks which (landed, D-057).
19. ~~`@path` in a message~~ -- the file goes with it, as read_file shows it
    (landed, D-058).
20. ~~How to continue, as a session ends~~ -- the tokens used and the
    `foundry resume` command (landed, D-059).
21. ~~`exec` names its session~~ -- `session_started`, first, for
    `exec --resume` (landed, D-060).
22. ~~Reasoning effort on every protocol~~ -- `backend.effort` for the
    Responses API and Chat Completions too, and `/effort` mid-session
    (landed, D-061).
23. ~~Ctrl+C stops the turn, not the session~~ -- the prompt comes back with
    the conversation kept, each call left answered (landed, D-062).
24. ~~A task's ending is not the session's~~ -- after `finish` or a budget the
    prompt comes back; only the user ends the session (landed, D-063).
25. ~~`/mcp`~~ -- the servers, how each is, and the tools each offers
    (landed, D-064).
26. ~~A screenshot from the clipboard~~ -- `/image` alone, as codex's Ctrl+V
    pastes one (landed, D-065).
27. ~~`/review` of a branch or a commit~~ -- `--base REV` and `--commit REV`,
    as codex's review presets (landed, D-066).
28. ~~The endpoint's models~~ -- `/model` alone lists them, the session's
    marked (landed, D-067).
29. ~~What a command printed, in view~~ -- its last lines and its exit code
    under it, as codex shows them (landed, D-068).
30. ~~Working, and for how long~~ -- a line with the time taken while the
    model is asked or a tool runs (landed, D-069).
31. ~~The answer as Markdown~~ -- rendered a line at a time on a terminal,
    as codex renders it (landed, D-070).
32. ~~Named placeholders in custom prompts~~ -- `$FILE` filled from
    `FILE=value`, as codex's prompts take them (landed, D-071).
33. ~~`-C DIR`~~ -- the directory to work in, as codex spells it (landed,
    D-072).
34. ~~How long a task took~~ -- `worked for 1m 12s` as it ends, as codex says
    it (landed, D-073).
35. ~~The newest line of a running command~~ -- in the working line while it
    runs, so a long build is visibly alive (landed, D-074).
36. ~~What a patch changed, in view~~ -- each file's diff under the patch once
    it lands, asked about or not, as codex shows an edit (landed, D-075).
37. ~~Quiet reads, failures that say why~~ -- one line per read, the reason
    for anything that failed (landed, D-076).
38. ~~Ctrl+C twice to end the session~~ -- one at the prompt says how, as
    codex asks for a second (landed, D-077).
39. ~~`foundry run TASK` goes on~~ -- the task is the first message and the
    prompt comes back, as `codex "task"` (landed, D-078).
40. ~~Told when an approval waits~~ -- the `notify` program hears
    `approval-requested` before the question blocks (landed, D-079).
41. ~~Which MCP tools are offered~~ -- `enabled_tools` and `disabled_tools`
    per server, as codex's (landed, D-080).
42. ~~MCP servers at an address~~ -- `url` and `bearer_token_env_var`, over
    streamable HTTP, as codex's (landed, D-081).
43. ~~`foundry mcp list | add | remove`~~ -- the servers in your config.toml
    from the command line, as `codex mcp` (landed, D-082).
44. ~~Which variables a command gets~~ -- `[shell_environment_policy]`, as
    codex's, without letting a secret through by a pattern (landed, D-083).
45. ~~A backend's own key, headers and query~~ -- `env_key`,
    `env_http_headers` and `query_params`, as codex's model providers
    (landed, D-084).
46. ~~Retries you can see~~ -- each retry of a model request said before its
    wait, as codex's "Reconnecting...", journaled and counted; a spent quota
    not retried at all (landed, D-085).
47. ~~Which files are instructions~~ -- `AGENTS.override.md`,
    `project_doc_fallback_filenames` and `project_doc_max_bytes`, as codex's
    (landed, D-086).
48. ~~A key piped to login~~ -- `foundry login --with-api-key` and `login
    status`, as codex's, without ever showing the key (landed, D-087).
49. ~~Resuming as codex spells it~~ -- `resume --last`, `resume --all` and
    `exec resume` (landed, D-088).
50. ~~Reasoning carried across rounds on Responses~~ -- encrypted, sent back
    with its turn to the same model, as codex does (landed, D-089).
51. ~~How long the answers are~~ -- `backend.verbosity`, codex's
    `model_verbosity`, on both OpenAI shapes (landed, D-090).
52. ~~A setting nobody reads is an error in the files too~~ -- with where it
    lives here, and codex's names told Foundry's (landed, D-091).
53. ~~An unattended run says what it runs with~~ -- model, protocol, profile,
    mode and effort under the session's line, as codex's exec opens (landed,
    D-092).

Not planned for now: a Windows sandbox (codex's needs dedicated local users,
WFP filters and an elevated service; the threat model documents its absence),
hosted web search, and interactive PTY sessions for long-running commands.

## V2 candidates (explicitly no commitment to order)

~~session resume~~ (landed, D-035) | ~~LLM summarisation compaction~~
(landed, D-040) | ~~a visible `update_plan`~~ (landed, D-037) | ~~skills and
slash commands~~ (slash commands and custom prompts landed, D-039) | enabling the subagent seam | ~~an MCP bridge~~ (landed as the
MCP client, D-043) | ~~checkpoint/undo~~ (landed as patch undo, D-036) | ~~a
Python-only repo map~~ (landed, D-046) | research into a restricted-token
sandbox.
