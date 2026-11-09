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
54. ~~Tab completion~~ -- `foundry completion powershell|bash`, built from the
    parser, as `codex completion` (landed, D-093).
55. ~~When automatic compaction starts~~ -- `runtime.auto_compact_token_limit`,
    codex's `model_auto_compact_token_limit`, only ever sooner (landed, D-094).
56. ~~The context window on every protocol~~ -- `backend.max_context_tokens`
    no longer Claude's alone, and compaction before the window is full
    (landed, D-095).
57. ~~The most one reply may be~~ -- `backend.max_output_tokens`, codex's
    `model_max_output_tokens`, on every protocol (landed, D-096).
58. ~~codex's command-line flags~~ -- `--full-auto`, `-a`, `-s`, `--yolo` and
    the rest refused with what Foundry takes instead; `-p` and `-V` taken
    (landed, D-097).
59. ~~`-c` with codex's names~~ -- `-c model_reasoning_effort=high` told
    `backend.effort`, as a file's key is (landed, D-098).
60. ~~A stream cut part-way asked again~~ -- `backend.stream_max_retries`, as
    codex's, instead of ending the task blocked (landed, D-099).
61. ~~Settings the protocol cannot send~~ -- `fallbacks` off Claude and a
    summary asked of Chat Completions refused, not dropped (landed, D-100).
62. ~~codex's slash commands~~ -- `/approvals`, `/mention`, `/logout`,
    `/feedback` told what Foundry has instead (landed, D-101).
63. ~~MCP resources~~ -- codex's `list_mcp_resources`,
    `list_mcp_resource_templates` and `read_mcp_resource` (landed, D-102).
64. ~~`foundry mcp-server`~~ -- Foundry as an MCP server over stdio, its tools
    `foundry` and `foundry-reply`, as codex's (landed, D-103).
65. ~~Approvals asked of the MCP client~~ -- what the mode would ask about put
    to a client that offers elicitation, as codex's mcp-server asks (landed,
    D-104).
66. ~~`foundry mcp get NAME`~~ -- one MCP server's settings in full, as
    `codex mcp get`, a value a credential's name marks hidden (landed, D-105).
67. ~~A cited file opens in the editor~~ -- `src/app.py:42` in the answer is
    a link to that file at that line, as codex's `file_opener` (landed,
    D-106).
68. ~~`@name` finds the file~~ -- `@app.py` attaches `src/app.py` when it is
    the only file git knows by that name, as codex's @ search (landed,
    D-107).
69. ~~A resumed session shows its conversation~~ -- what was typed, the
    answers and each tool call's line, before the prompt, as codex's resume
    (landed, D-108).
70. ~~The working line after a reasoning summary~~ -- back up, saying the
    summary's title, as codex's status line (landed, D-109).
71. ~~codex's names in an MCP server's table~~ -- `startup_timeout_sec`,
    `tool_timeout_sec` and a started server's `cwd`, so a table copied from
    codex loads (landed, D-110).
72. ~~A url server's own headers~~ -- codex's `http_headers` and
    `env_http_headers`, a credential only by its variable's name (landed,
    D-111).
73. ~~`/prompts:NAME`~~ -- a custom prompt called as codex calls it, and so a
    prompt a built-in's name hides (landed, D-112).
74. ~~Agent skills~~ -- `SKILL.md` folders, the user's and a trusted
    repository's `.agents/skills`, listed to the model and loaded with
    `load_skill`; `$name` and `/skills`, as codex's (landed, D-113).
75. ~~Skills found and switched as codex's~~ -- `.agents/skills` up to the
    repository's top, `[[skills.config]]` and `skills.max_context_tokens`
    (landed, D-114).
76. ~~Lifecycle hooks, as codex's~~ -- `hooks.json` and `[hooks]` from your
    own files: `PreToolUse` refuses, rewrites or adds a note, `PostToolUse`
    replaces a result or adds one; `/hooks` (landed, D-115).
77. ~~UserPromptSubmit and Stop hooks~~ -- a prompt stopped or sent with a
    note; a task sent on when the model is done, at an answer or at
    `finish` (landed, D-116).
78. ~~SessionStart, SessionEnd and Interrupt hooks~~ -- a note or a stop at
    a session's first prompt (`startup`, `resume`); told when a task is
    stopped and when the session closes (landed, D-117).
79. ~~PreCompact and PostCompact hooks~~ -- a compaction kept from happening
    or the task ended after one; SessionStart's `compact` source before the
    next request (landed, D-118).
80. ~~PermissionRequest hooks~~ -- an approval prompt answered by the user's
    own program: a call allowed once or refused, only where someone would
    be asked (landed, D-119).
81. ~~Background hooks~~ -- `async` hooks started and not waited for, their
    notes heard before the next request or with the next prompt, ended as
    the session closes (landed, D-120).
82. ~~MCP tool hooks~~ -- `type = "mcp_tool"`: a tool of a server the session
    has, called with `${...}` filled in from the event, its answer read as a
    command hook's (landed, D-121).
83. ~~Model-request hooks~~ -- PreModelRequest and PostModelRequest, told of
    each try of a model request, retries included (landed, D-122).
84. ~~`/copy [N]`~~ -- the latest answer, or one of its code blocks, on the
    clipboard, as it was shown (landed, D-123).
85. ~~`/export [PATH]`~~ -- the whole conversation as Markdown, from the
    journal, on the clipboard or in a new file (landed, D-124).
86. ~~`/rename [NAME]`~~ -- a session's name, shown in its listings and taken
    wherever its id is, `foundry resume NAME` (landed, D-125).
87. ~~`/fork` and `foundry fork`~~ -- a copy of a session, continued apart
    from it, the source kept as it was (landed, D-126).
88. ~~`/goal`~~ -- a goal the session works toward turn after turn, with a
    token budget, `get_goal` and `update_goal` (landed, D-127).
89. ~~`foundry archive`, `unarchive` and `delete`; `/archive` and
    `/delete`~~ -- by id or name (landed, D-128).
90. ~~`/side` and `/btw`~~ -- a question asked on the side, read-only, then
    dropped from the conversation (landed, D-129).
91. ~~`/recap`, `/clear`, `/rollout`, `/pwd`~~ -- and what Foundry says of
    codex's commands it has another way for (landed, D-130).
92. ~~`foundry review`~~ -- `--uncommitted`, `--base`, `--commit [--title]`
    or instructions, headless and read-only, as `codex review` (landed,
    D-131).
93. ~~`exec --ephemeral`~~ -- a run that keeps no record once it ends
    (landed, D-132).
94. ~~`exec --fork` and `exec fork`~~ -- a copy of a session continued
    headlessly (landed, D-133).
95. ~~`/resume [SESSION]`~~ -- another of this workspace's sessions, from
    inside one (landed, D-134).
96. ~~`foundry policy check`~~ -- what the rules decide for a command,
    without running it (landed, D-135).
97. ~~`exec --color`~~ -- always, never or auto (landed, D-136).
98. ~~`exec --trust`~~ -- the repository trusted from the command line, for
    an unattended run (landed, D-137).
99. ~~`/warnings`~~ -- the session's warnings and errors, shown again
    (landed, D-138).
100. ~~The terminal title, and `/title`~~ -- what the session is doing,
     where a tab bar shows it (landed, D-139).
101. ~~The rest of codex's slash commands~~ -- `/cwd`, and a line for each
     one Foundry has no counterpart for (landed, D-140).
102. ~~Sub-agents~~ -- `spawn_agent`, a task done in a conversation of its
     own (landed, D-141).
103. ~~`developer_instructions` and `compact_prompt`~~ -- codex's
     instruction settings (landed, D-142).
104. ~~`review_model`~~ -- the model reviews run on (landed, D-143).
105. ~~`[features]`~~ -- codex's feature flags read as Foundry's own
     settings, and `foundry features` (landed, D-144).

Not planned for now: a Windows sandbox (codex's needs dedicated local users,
WFP filters and an elevated service; the threat model documents its absence),
hosted web search, interactive PTY sessions for long-running commands, and
codex's desktop notifications (`tui.notifications`), sent while its
terminal has lost focus, which a session reading lines cannot tell -- a
`notify` program (D-050) is told when a turn ends, and the terminal's title
says when an approval waits (D-139).

## V2 candidates (explicitly no commitment to order)

~~session resume~~ (landed, D-035) | ~~LLM summarisation compaction~~
(landed, D-040) | ~~a visible `update_plan`~~ (landed, D-037) | ~~skills and
slash commands~~ (slash commands and custom prompts landed, D-039) | ~~enabling the subagent seam~~ (landed as `spawn_agent`, D-141) | ~~an MCP bridge~~ (landed as the
MCP client, D-043) | ~~checkpoint/undo~~ (landed as patch undo, D-036) | ~~a
Python-only repo map~~ (landed, D-046) | research into a restricted-token
sandbox.
