# Foundry — Runtime Requirements v0.2

> This document supersedes the v0.1 draft written by another AI. Every
> substantive difference from v0.1 is backed by a numbered decision
> ([decision-log.md](decision-log.md)) or by research evidence
> ([research/](research/)).
> **Status markers**: unmarked = confirmed (either ruled by the user, or
> derived from a confirmed decision); `[provisional D-0xx]` /
> `[provisional OQ-xx]` = proposed by Claude, awaiting the user, and registered
> in [decision-log.md](decision-log.md) (status = provisional) or
> [open-questions.md](open-questions.md).

**Platform**: Windows · **Language**: Python 3.12 · **Installation**: a wheel,
installed offline with `pip --no-index --find-links`

---

## 0. Why Foundry (motivation and design priorities)

v0.1 never answered "why are we doing this at all". The motivation decides the
priorities, and it is now explicit ([D-007](decision-log.md)):

1. **Hard corporate constraints**: corporate machines may not install external
   agent products such as Codex CLI or Claude Code, and model access must go
   through the corporate Gateway's API; software distribution goes through an
   internal source (JFrog Artifactory), the availability of Node/Rust
   toolchains is uncertain, and Python plus an internal wheel directory is the
   most certain channel.
2. **Learning**: to understand coding-agent runtime design thoroughly. A
   mini-codex had previously been assembled with the Claude Agent SDK and the
   experience was mediocre -- Foundry has to win on complete control and design
   quality.

**The design priorities that follow** (used to resolve conflicts, in this
order): auditability > few dependencies / offline installability > a clear,
maintainable implementation > feature count.

**The charter (unchanged)**: Foundry owns all of the code and interfaces for
its agent loop, tools, policy, providers and sessions. It may study the public
source of Codex, Claude Code, gemini-cli, aider, OpenHands, luban and others
for design ideas, but it does not fork them, vendor them, call them at runtime,
or **impersonate them at the protocol level**.

```text
personal machine : Foundry -> an OpenAI API key -> OpenAI models          (changed: the original ChatGPT login is confirmed blocked, see §3.1)
corporate machine: Foundry -> the corporate Gateway -> several approved models (Claude included)
development      : Foundry -> ReplayBackend / a local OpenAI-compatible endpoint (new: testable without credentials)
```

All three share the same agent loop and tools; only the authentication and
protocol adapter layers change.

## 1. Product shape (absent in v0.1, [D-004](decision-log.md))

- **V1's core form is an interactive terminal session**: the user converses
  with the agent in the terminal, output streams, and side-effecting actions
  are approved on the spot (ASK).
- **Architectural mandate**: the UI and AgentRuntime are decoupled from day
  one. The runtime exposes only a **typed event stream + asynchronous approval
  requests and responses**; the terminal UI is merely the first subscriber.
  Headless mode (`foundry exec`, where every ASK is treated as DENY,
  fail-closed) is therefore nearly free: it lands in V1 in M4 if there is
  budget, and otherwise in V2 ([OQ-16](open-questions.md)).
- **CLI surface (the V1 minimum)**:
  - `foundry` — open an interactive session in the current directory
  - `foundry login / logout` — credential management for the personal path
  - `foundry sessions [list|show|export <id>]` — viewing and exporting sessions
  - `foundry doctor` — environment self-check (Python version, git, long
    paths, proxy/TLS connectivity)
- **The output contract**: the five termination states map to process exit
  codes: `completed=0, partial=10, blocked=11, failed=12, cancelled=13`; the
  final report carries evidence (the verification command + its exit code + a
  diff summary).
- **Configuration**: TOML at `~/.foundry/config.toml`; named profiles (such as
  `personal` / `corporate`), each bundling backend + model + policy presets.
  All layering and precedence is defined solely in §4.3.

## 2. Architectural components

```text
the Foundry CLI (terminal UI, a subscriber to the event stream)
    │  Submission(op) ▼ / Event ▲       <- two queues; approval is an asynchronous event pair
AgentRuntime (the one loop)
    ├── ContextManager     (new: the transcript->model-context projection, truncation, token accounting)
    ├── ModelBackend       (protocol adapters: OpenAI-compatible / Responses / Replay / a local endpoint)
    │     └── AuthProvider (credential acquisition/storage/refresh; the token is visible only at the HTTP injection layer)
    ├── PolicyEngine       (the six-step pipeline, see §4.1)
    ├── SessionStore       (versioned JSONL, the single source of truth, deterministically replayable)
    └── ToolExecutor       (file/command/Git tools; a Windows trusted host)
```

The components added relative to v0.1, and why:
- **ContextManager** (architecture critique #5): v0.1 capped "tool output size"
  and left nobody owning the context budget. Its responsibilities: per-tool
  output truncation (an explicit `[truncated]` marker, with the full output
  written to disk as an artifact), observation masking of older tool output,
  per-round token accounting (reading the real usage fields), and a clean
  termination as the context approaches its ceiling (V1 does no LLM
  summarisation compaction; the evidence is arXiv 2508.21433, which shows
  masking and LLM summarisation to be equivalent).
- **The event stream protocol** (architecture critique #4): typed events
  (`turn_started / message_delta / tool_begin / tool_output_delta / tool_end /
  approval_request / token_count / turn_complete / error / termination`) plus a
  version number; **the single authority for the enumeration is `events.py` in
  design.md**, and this list is a reference to it. Approval is modelled as an
  `approval_request` event and a subsequent `approval_decision` submission,
  with the loop suspended while it waits -- not a blocking `input()` inside a
  tool.
- **ReplayBackend** (architecture critique #3): ModelBackend's third
  implementation, reading recorded request/response fixtures. **The acceptance
  criterion: every loop/policy/tool test must pass on a machine with no network
  and no credentials.**

## 3. Model paths

### 3.1 The personal path: an OpenAI API key (v0.1's ChatGPT login is confirmed blocked)

Following v0.1 §3.1's own rule ("if bounded research finds no supported route
-> mark it blocked, record the evidence and ask"), the research conclusion of
2026-08-29 (evidence: [research/auth.md](research/auth.md)):
- the official "Sign in with ChatGPT" grants identity only (name/email/avatar),
  not inference or subscription quota, and requires an application review;
- the Codex subscription inference endpoint
  `chatgpt.com/backend-api/codex` has an originator allowlist and answers 403
  to a non-Codex client; using it requires impersonating Codex, which violates
  OpenAI's ToS (circumventing protective measures) and this charter, and there
  are real cases of accounts being banned;
- the only programmatic route OpenAI endorses is a platform API key.

**Decision ([D-009](decision-log.md), confirmed by the user on 2026-08-29)**:
the personal path is an OpenAI platform API key (Chat Completions /
Responses, `api.openai.com`). ChatGPT login is marked
**blocked-with-evidence**: not implemented, not impersonated.

Hard requirements:
- `foundry login` guides entry of an API key (or reads one from an environment
  variable); the credential is encrypted with **DPAPI (ctypes
  CryptProtectData)** and stored at `~/.foundry/auth.json`, written
  atomically; a failed decrypt counts as "not logged in" and leads back to
  login, rather than being a fatal error.
- The token/key must not enter logs, error messages, the event stream or the
  model context; enforcement is in §6.1 (the same redaction function applied at
  three points: the session write, the event emission, and context assembly).
- **Development verification does not depend on a key**: ReplayBackend covers
  every unit and regression test; a local OpenAI-compatible endpoint (LM Studio
  / Ollama, through the same Chat Completions adapter) gives a free real-model
  smoke test; only real cloud E2E consumes the key.

### 3.2 The corporate Gateway (multi-model, Claude included)

Known facts ([D-006](decision-log.md) / [D-022](decision-log.md)): the Gateway
hosts models from several vendors; **the OpenAI family goes through the
Responses API**; the token is obtained through an **intranet auth flow** and
used with the Gateway URL; Claude models exist but their wire protocol is
unverified.

Hard requirements:
- **The internal representation is provider-agnostic**: conversation /
  tool-call / tool-result / streaming events use Foundry's own intermediate
  representation (with the content block structure aligned to the MCP shapes,
  leaving the door open for a future bridge); each wire protocol (Chat
  Completions / Responses / Anthropic Messages / a Gateway-specific one) gets
  its own narrow adapter, and an adapter only converts -- it never owns the
  loop.
- The backend configuration table (borrowing from codex's
  `ModelProviderInfo`): `{name, base_url, protocol, credential_source,
  http_headers, query_params, request_max_retries, stream_idle_timeout_ms,
  capabilities_override}`; every effective configuration value records its
  provenance (which layer it came from).
- **Capability probing + graceful degradation** (the luban lesson): an
  enterprise Gateway will inevitably deviate from the standard (streaming
  modes, parallel tool calls, caching, usage fields); every optional feature
  needs a degradation path, and the probe results are recorded in the session.
  **Unsupported semantics are never silently simulated.**
- **The CredentialSource contract** (absorbed from the Codex version):
  `acquire / expiry / refresh / logout`, with a pluggable mechanism (an HTTP
  exchange / an internal executable / browser SSO, still to be discovered);
  the credential circulates as a non-printable handle that only the HTTP
  transport layer resolves -- authentication is separate from the protocol
  adapter.
- **The M3 entry gate**: obtain the Gateway's **tool-call streaming redaction
  fixtures** first (an ordinary response / a tool call and its continuation /
  usage / rate limiting / a dropped stream / a malformed event), and only then
  implement -- "Responses-compatible" may cover conversation without covering
  agentic tool continuation, and that is the most expensive rework risk.
- TLS/proxy: `ssl.create_default_context()` by default (which automatically
  trusts the Windows system certificate store -- zero configuration in the
  corporate MITM proxy case); `FOUNDRY_CA_BUNDLE`/`SSL_CERT_FILE` overrides are
  supported; proxies are read from `HTTPS_PROXY`/`NO_PROXY`; proxy / custom CA
  / mTLS are **not V1 acceptance items** (the capability is kept), and a 407
  Negotiate is reported as an explicit error.
- To be confirmed ([OQ-6](open-questions.md)): the specific intranet auth
  mechanism, the endpoint's shape, and the model list.

### 3.3 The development/test path (new)

- **ReplayBackend**: the requests and responses recorded by SessionStore can
  rebuild every model call byte for byte (which is also the basis of
  [D-012]'s resume-ready schema); golden transcripts are committed as
  regression fixtures.
- **A local endpoint**: any `base_url` compatible with OpenAI Chat Completions
  will do (LM Studio/Ollama); for development smoke tests only, not a product
  path.

## 4. Policy and approval (trusted host)

### 4.1 PolicyEngine: the six-step pipeline ([D-013](decision-log.md), adopting the Claude Code Agent SDK's published specification)

```text
0 the circuit breaker (a hard-coded table, before everything; see below)
1 the pre_tool callback (an optional extension point; may ALLOW/DENY/ASK/rewrite the input)
2 DENY rules   <- a DENY at any layer cannot be overridden by an ALLOW at any layer
3 ASK rules    (built-in ASK rules live here too: e.g. D-011's writes to dirty files)
4 the permission mode baseline
5 ALLOW rules  (the built-in read-only tool ALLOW rules live here)
6 interactive approval (under headless / dont_ask this step = DENY, fail-closed)
```

**Exactly where each mechanism sits in the pipeline** (a review correction: the
first v0.2 draft wrote "tools default to ASK" in a form that would have killed
both accept_edits and approval persistence):
- The default allowance for read-only tools
  (`list_files/search_text/read_file/read_artifact/git_status/git_diff`) is a
  **built-in ALLOW rule (step 5)**.
- The "default ASK" for mutators (`apply_patch/run_command`) is **not an ASK
  rule**; it is what happens when nothing matches and the call falls through to
  step 6's interactive approval.
- `accept_edits` mode is the step 4 baseline granting ALLOW to apply_patch
  inside the workspace (which works precisely because step 3 has no built-in
  ASK rule in the way).
- D-011's forced ASK on dirty files is a **built-in ASK rule (step 3)** -- and
  because it sits before the mode, it overrides accept_edits.
- The ALLOW rule generated by a user's "approve permanently" sits at step 5,
  which is why it takes effect for mutators (no catch-all ASK rule at step 3
  blocks it).

**Mode baseline definitions** (one line each; no mode ever skips steps 0 or
2):
- `default`: nothing matched -> step 6 interactive approval.
- `accept_edits`: apply_patch inside the workspace -> ALLOW; otherwise as
  default.
- `plan`: apply_patch and non-read-only run_command -> DENY (with the reason
  returned to the model); switch modes once the plan is approved.
- `dont_ask`: nothing matched -> **DENY** (fail-closed; headless reuses these
  semantics). There is no mode in which "nothing matched" means allowed.

Invariants (verified by a test table): an ALLOW from the callback does not skip
the DENY/ASK rules; a DENY cannot be relaxed layer by layer; **after a pre_tool
rewrite, the input re-enters the pipeline from step 0, and the breaker, the
rules, the approval display and the execution are all bound to the final,
rewritten input**; **command segmentation** must happen before rule matching
(see §4.2). The test table contains at least: accept_edits allowing a patch to
a clean file; a patch to a dirty file still ASKing under accept_edits; a
persisted ALLOW rule taking effect for run_command; dont_ask DENYing when
nothing matched.

- The decision vocabulary is fixed as `ALLOW / ASK / DENY`, rules take the form
  `tool` or `tool(pattern)` (fnmatch), the precedence order is fixed as
  deny > ask > allow, and **numeric priorities are rejected** (the gemini-cli
  lesson).
- A DENY returns a machine-readable reason to the model as the tool result (the
  loop survives and the model can adjust); only a user Abort terminates the
  turn.
- Approval granularity: `once / this session (memory only) / permanently`. **A
  permanent rule is written into the user-layer configuration under
  `~/.foundry/` (keyed by workspace), never into a file inside the
  workspace** -- otherwise the model could escalate its own privileges under
  accept_edits (found in review); the generated rule is an **exact string
  match** (no pattern generalisation) and takes effect at the next policy
  evaluation. The command/diff shown in the approval UI and the object actually
  executed must be the same string (what-you-approve-is-what-runs); an ASK
  timeout defaults to DENY. Model output can never trigger rule persistence.
- **Approval binding and expiry** (absorbed from the Codex version): one
  approval binds "the normalised operation + cwd + the env policy + a validity
  window", and a change to any field invalidates it and requires a new
  approval; the timeliness invariant is re-checked before execution (against
  state drift between approval and execution).
- A persisted policy decision records: the rule ID, the policy version/digest,
  an operation digest, the reason, and the time -- so "why was this allowed at
  the time" can be reviewed afterwards.
- **The built-in circuit breaker (step 0, a hard-coded table that no ALLOW
  rule, mode or callback can override)**:

  | Category | Entries (in canonical form; the segmenter normalises aliases first: `rm/ri/del/erase -> Remove-Item` and so on) |
  |---|---|
  | protected write paths | `.git/`, `~/.foundry/` (credentials/policy/audit), the session directory, `<workspace>/.foundry/` (if present) |
  | destructive git | `checkout -- <path>`, `restore` (in its working-tree-overwriting form), `reset --hard`, `clean`, `stash drop`, `stash clear` |
  | recursive deletion | `Remove-Item -Recurse`, `rmdir /s`, `del /f /s`, `rm -rf` and equivalent forms targeting the repository root, the user's home directory, or a drive root |

- **plan mode**: read-only exploration, per the mode baselines; very cheap and
  very valuable.
- A built-in allowlist of read-only safe commands (no prompt), **with argument
  constraints** (a review correction): path arguments must pass the same
  workspace containment check as the file tools (drive-absolute and UNC
  arguments are refused); **bare `git` is not on the allowlist** -- the model
  should use the hardened built-in git_status/git_diff tools, and git through
  run_command goes through normal policy (otherwise the allowlist bypasses
  §5.2's hardening).
- Rule validation at startup: a rule that matches no tool, or that can never
  fire, produces a warning (the silent-dead-rule trap Claude Code fell into).

### 4.2 An honest design for run_command

Deciding whether a command is safe presupposes parsing it, and Windows has
three syntaxes (cmd / PowerShell / git-bash), while string prefix matching has
been shown bypassable over and over (Claude Code CVE-2025-66032 and others).
The V1 rules:
- Fix a **single shell: Windows PowerShell 5.1**
  (`powershell.exe -NoProfile -Command`, [D-018](decision-log.md)):
  preinstalled, zero dependencies; the segmenter follows 5.1 syntax (`;`, the
  pipe `|`; **5.1 has no `&&`/`||`**); the system prompt tells the model
  explicitly that "there is no `&&`, use `;` instead".
- Automatic ALLOW only matches when every segment of the conservative
  segmentation matches; when a command contains substitution, chaining,
  redirection or other metacharacters and cannot be segmented with confidence,
  it is **always ASK** (the "can't parse -> ASK" safety valve).
- DENY string matching is defence in depth only, and the document states
  explicitly that it is not a boundary.
- Execution: an explicit `cwd`, a timeout, an output ceiling; **a Job Object
  (ctypes, KILL_ON_JOB_CLOSE) owns the process tree**, so cancel/timeout kills
  every descendant at once, with `taskkill /T /F` as a backstop; no persistent
  shell session (stateless per call, the mini-swe-agent lesson).
- **Env filtering**: the subprocess gets a minimal core set by default
  (PATH/SYSTEMROOT/TEMP/PYTHON* and so on), with
  `*KEY*/*TOKEN*/*SECRET*/*PASSWORD*/AWS_*` removed by default; the user may
  pass specific variables through explicitly; Foundry's own credentials never
  enter a subprocess env.
- Output is captured bytes-first: decoded as UTF-8 first, falling back to the
  OEM code page (cp936), with `errors='replace'`; `PYTHONUTF8=1` is injected
  into the subprocess; the session keeps the raw bytes (base64) so they can be
  re-decoded later.

### 4.3 Configuration layering and repository trust (this section is the sole authority on layering; §1 and design.md refer to it)

- **Settings (scalars such as model, shell, timeouts)**: CLI flag >
  environment variable (`FOUNDRY_*`) > project-local (gitignored) > profile
  (selected inside the user configuration) > the user's
  `~/.foundry/config.toml` > built-in defaults. **Secrets may come only from
  environment variables or the credential store, and must never appear in
  repository configuration or ordinary CLI arguments.**
- **Policy rules**: the rule lists from every layer are **concatenated** and
  then evaluated uniformly as deny > ask > allow (layer order does not affect
  the result; deny-from-anywhere wins); the exception is the **managed layer**
  (an administrator-ACL'd directory such as `C:\ProgramData\Foundry\policy.toml`),
  whose DENYs are a floor, and the repository-checked-in layer accepts only
  deny/ask.
- **In-repository configuration can only tighten** (adding DENY/ASK), and can
  never add an ALLOW; connection-class configuration such as endpoint,
  credentials, headers and proxy may come only from machine-local layers. The
  first time any repository-provided configuration or instruction file is used
  in a new directory, a one-time trust prompt appears and is recorded in
  `~/.foundry/trusted.json`.
- **The repository instruction file ([D-019](decision-log.md), delivered in
  M2)**: V1 reads a `FOUNDRY.md` at the project root (with `AGENTS.md` read for
  compatibility), declaring the build/test commands and repository caveats, and
  injects it into the context (trust-gated, with a byte ceiling); verification
  command precedence: specified in the task > declared in the repository file >
  chosen by the model.
- **The honest position on "a corporate fixed DENY cannot be overridden"**
  (security critique #4): within an untampered installation, no runtime route
  (a task, an approval, a CLI flag) can relax a managed DENY -- that is
  achievable; resisting a local administrator deliberately editing the source
  or configuration is not, and the real boundary is on the Gateway server
  (model allowlisting, request logging). This position must be written into the
  disclosure documentation, with no over-promising.

### 4.4 Threat model and disclosure (a new section; v0.1 omitted prompt injection entirely)

- **The threat model statement**: every tool result (file contents, command
  output, git diff) is untrusted input and may carry injected instructions
  aimed at the model. With no sandbox, the only real defence is PolicyEngine
  gating every side effect independently of the model's intent.
- The realistic definition of a "trusted repository": one whose every file you
  are willing to have both executed and read as instructions.
- Fixed disclosure printed on first run and at the start of every session: no
  sandbox; every approved command runs with full user privileges (it can read
  and write all files including credentials, access the network, and read
  environment variables); use only in trusted repositories.
- Explicit V1 non-goals: no defence against malicious repository content; no
  defence against a local administrator bypassing managed policy. The V2
  directions (a restricted token / a container) go on the roadmap, to show this
  is a staged choice.
- Terminal rendering safety: strip ANSI/OSC sequences from model output and
  tool stdout before rendering (OSC 52 clipboard injection is a real attack
  surface), and always escape rich markup.
- No automatic commit, push, PR, publish or deploy (inherited from v0.1).

## 5. The agent loop and tools

### 5.1 The loop

- The shape: `while the model returns tool calls: policy -> execute -> feed
  results back -> resample`; a final message from the model with no tool calls
  ends the turn. User input arriving mid-turn is queued and merged at the next
  sampling (codex mid-turn steering).
- Each request is the stateless full history, which suits both replay and a
  Gateway that persists no state.
- Limits: a soft ceiling on consecutive tool rounds (configurable), a per
  command timeout, and an optional per-task token ceiling; after 2-3
  consecutive failed edits to the same file, a read_file is forced (the
  SWE-agent error-compounding curve).
- **Failure fingerprints** (absorbed from the Codex version): repeated failures
  are counted by "normalised operation + error class", and **a change in the
  error text does not reset the counter**; past a small threshold, terminate or
  hand over to a human, so the model cannot retry forever by rewording.
- **The protocol layer must accept N tool calls in one round** (both Responses
  and Claude issue them in parallel); V1's execution layer runs them serially
  in order, each passing policy independently, and moves to the next round only
  once all have been fed back.
- A malformed, unknown, or out-of-bounds tool call is not executed, and a
  structured error is returned to the model.
- The error taxonomy: `TransientError (429/5xx/network, retried with
  exponential backoff, respecting Retry-After) / AuthError (refresh and retry
  once) / FatalError (context exceeded, a content refusal) / PolicyDenied`;
  retry logic belongs to AgentRuntime rather than being scattered across
  backends; sustained throttling terminates as `blocked(rate_limited)` rather
  than waiting indefinitely.
- The concurrency model: an **asyncio core** ([D-016](decision-log.md);
  streaming, cancellation and timeouts express naturally; the Windows
  ProactorEventLoop supports subprocesses); synchronous tools are wrapped in
  `asyncio.to_thread`.

### 5.2 The V1 tool surface (9 tools, each with an output ceiling and a truncation marker)

| Tool | Description | Default policy |
|---|---|---|
| `list_files` | sorted by mtime, with entry and depth ceilings | ALLOW |
| `search_text` | a hit ceiling (~50); beyond it, prompt to narrow rather than truncate silently | ALLOW |
| `read_file` | windowed (~250 lines) with line numbers and an elision count; registers read-before-edit state | ALLOW |
| `apply_patch` | see §5.3 | falls to step 6 interactive approval; ALLOW inside the workspace under the accept_edits baseline (mechanism in §4.1) |
| `run_command` | see §4.2 | falls to step 6 interactive approval |
| `read_artifact` | reads the complete tool output this session wrote to disk after exceeding a limit. artifact_id is an **opaque token** resolved only through the current session's in-memory index (no path semantics are accepted), limited to this session's products; the output re-enters the context through the same truncation and redaction path as any other tool (undefined in v0.1, now defined by [D-015](decision-log.md)) | ALLOW |
| `git_status` / `git_diff` | hardened invocation: `git --no-pager -c core.fsmonitor= -c core.hooksPath=`, stripping `GIT_*` from the env, with `GIT_TERMINAL_PROMPT=0` | ALLOW |
| `finish` | the model explicitly closing out the task: `{status, summary, claims:[{claim_text, command_event_id}]}`; the runtime verifies per §6.3 and then emits a Termination event ([D-017](decision-log.md); the only channel that produces a ValidationClaim) | ALLOW |

Tool description text is a short paragraph plus one example (the SWE-agent ACI
evidence: a concise, consolidated, guard-railed tool surface measurably scores
higher). V1 freezes at these 9 with no additions; `update_plan` (a visible
plan) is a V2 candidate.

### 5.3 apply_patch ([D-010](decision-log.md): a model-neutral anchored format)

- The format: **an envelope + anchored-text search/replace hunks** -- the
  envelope supports Add/Delete/Update File (borrowing V4A's file operation
  structure), and an Update hunk is an exact anchored-text replacement that
  does not depend on line numbers (borrowing the converged conclusion of Claude
  Code's str_replace and aider's SEARCH/REPLACE); the patch is transmitted as a
  **single opaque string argument** (the code-in-json lesson). The rationale:
  the Gateway is multi-model (Claude included), V4A is a GPT-private trained
  format, and Claude mangles it.
- The format description goes explicitly into the system prompt rather than
  relying on "the model just knows"; the grammar documentation and the parser
  are generated from one source (the codex #2578 lesson).
- Application semantics (a review correction; the original text mixed codex's
  atomicity with aider's partial application, which are mutually exclusive):
  **per-file atomic** -- first parse and locate every hunk in **every** file;
  a file with any hunk that fails to locate is not touched on disk at all,
  while a file whose hunks all pass is written atomically with temp +
  `os.replace`; the return value is a per-file/per-hunk status report telling
  the model to **resend all hunks of the failed files only**. These semantics
  are written into the model-facing format description in prompts/ in the same
  breath (the error copy and the behaviour must not fork). The leniency
  gradient: an exact match -> CRLF/BOM normalisation -> trailing-whitespace
  tolerance -> **a failure with a hint** (quoting the nearest real line); >80%
  fuzzy matching is off by default (a silent misplacement is worse than a
  failure).
- Uniqueness: 0 or more than 1 anchor match produces a structured error
  (including the number of occurrences).
- read-before-edit: editing an unread file is refused; if the file has changed
  since it was last read, the anchor text is re-checked for a unique match.
- The target file's original encoding and line-ending style are preserved on
  write; a non-UTF-8 file produces an explicit error.
- An optional post-edit syntax check (for Python, at the `compile()`/pyflakes
  level) with the result attached to the tool result (the SWE-agent +3pt
  evidence).
- An escape hatch: a whole-file rewrite mode for small files; a weaker model's
  backend can be downgraded to whole-file at the profile level.
- The patch body never travels through argv or a subprocess (the ~32KB Windows
  argv ceiling, codex #15003).

### 5.4 The workspace boundary

- The workspace is a single directory, either passed explicitly or defaulting
  to cwd, which **must lie inside a git repository** (otherwise startup refuses
  with a clear error); a monorepo may use a subdirectory as the workspace;
  multi-repository tasks are a non-goal.
- **A file tool's path argument is a workspace-relative logical path**
  (absorbed from the Codex version): absolute paths, device paths and UNC
  paths are always refused -- an escape is not expressible at the interface
  layer.
- Path containment: `realpath(strict)` resolution on both sides + `normcase` +
  `commonpath`, with a per-component reparse point check (junction/symlink,
  `st_reparse_tag`); explicitly refuse ADS (`file:stream`), device names
  (CON/NUL/COM1... including forms with an extension), the UNC/`\\?\`
  prefixes, drive-relative paths (`C:foo`), and trailing dots or spaces. The
  target environment **cannot create symlinks and has no Developer Mode**, so
  V1 refuses reparse points outright rather than classifying "safe targets".
  Hard links and TOCTOU are listed as known accepted risks in the threat model.
- Honest disclosure: the workspace limit constrains only the file tools;
  run_command is inherently unconstrained, and the real gate is policy.
- **The dirty working tree ([D-011](decision-log.md))**: warn and continue;
  record a baseline at session start (the HEAD SHA + `git status
  --porcelain=v2` + metadata/digests for the relevant untracked files -- a
  single `git diff` does not cover untracked files); apply_patch on a file
  already dirty in the baseline **forces an ASK**; destructive git commands are
  a built-in DENY (see §4.1's circuit breaker).
- **Optimistic concurrency protection on writes** (absorbed from the Codex
  version): before each write, verify the target file has not changed since it
  was last read (a content digest); if it has, fail with `stale`, return the
  latest context, and never overwrite; the result records the old and new
  digests. The branch name is displayed prominently but **carries no security
  meaning** (no hard-coded special behaviour for main).

## 6. Sessions and the completion condition

### 6.1 SessionStore

- Location: `~/.foundry/sessions/<session-id>/events.jsonl` plus
  `artifacts/<sha256>` in the same directory (**content-addressed**, absorbed
  from the Codex version: events reference only the digest, size, media type
  and truncation state, and an artifact cannot be fetched by an arbitrary
  path); never inside the workspace, and the file tools have a built-in DENY
  for that directory; appends are atomic; the retention period is
  user-configurable.
- The line envelope is frozen from day one: `{ts, ordinal, type, v, payload}`;
  a first-line header records `{schema_version, foundry_version, session_id,
  workspace, profile, model}`. The evolution rule: add only, never change or
  delete, and readers skip unknown types. **The single authority for the event
  type set is design.md §9** (the git baseline is recorded as a `git_baseline`
  event rather than a header field, consistent with §6.3's "a ref movement is
  recorded as an event").
- Recorded event categories: model request/response (complete enough to rebuild
  the request -- **resume-ready, [D-012]**; **except the auth/Authorization
  header**, which is re-injected by the HTTP layer on replay and never
  persisted), tool call/result (including the policy decision and its reason),
  approval (the displayed string + the user's decision), command (argv, exit
  code, duration, the raw output bytes), token usage, validation, termination,
  capability probe, and git_baseline.
- **The resume feature itself is deferred to V2**; V1 promises only that the
  schema is replayable (which doubles as the basis of ReplayBackend testing --
  two birds, one stone).
- **Crash recovery semantics** (absorbed from the Codex version): a truncated
  final record is tolerated on read; **a session with no termination event is
  always judged `interrupted` and can never be mistaken for completed**;
  termination, approval and command-completion events flush immediately after
  being written.
- Secret handling (phrased so it can be accepted against; the scope was
  corrected in review):
  - (a) **byte-level exact match**: the UTF-8 and UTF-16LE byte encodings of
    credentials Foundry itself holds are replaced at the single write choke
    point, **before the raw output is base64-encoded** -- the guaranteed scope
    is "the literal byte sequences of known self-held credentials", and nothing
    larger is promised;
  - (b) **artifact writes and read_artifact reads go through the same choke
    point** (otherwise over-limit output becomes a bypass);
  - (c) the same redaction function is applied at three points: the
    session/artifact write, the event emission (before a ToolOutputDelta/Error
    leaves the runtime), and context assembly;
  - (d) pattern scanning for common token formats and high-entropy strings is
    labelled best-effort;
  - (e) the session log is itself treated as sensitive data (under the user
    profile, and stated in the documentation).
- Telemetry: none. The local JSONL is the entire record.

### 6.2 The audit log

`~/.foundry/audit.jsonl`, separate from the sessions (the luban pattern): every
tool call (DENYs included) appends `{ts, workspace, tool, target, decision,
outcome}`; the file tools are hard-coded not to modify it. This is the honest
compensating control for a no-sandbox V1.

### 6.3 The completion condition (machine-enforceable)

- **The production channel** (a review correction: the original text omitted
  it): a validation claim is submitted through the `finish` tool (§5.2,
  [D-017](decision-log.md)) -- the model calls `finish{status, summary,
  claims}`, and the runtime emits a Termination only after verifying it; in an
  interactive session an ordinary turn that does not call finish simply ends
  (the conversation continues), and the session's final status is settled at
  finish or when the session closes (closing with no finish -> recorded as
  `cancelled`/`partial` depending on context).
- The `completed` gate: at finish, the runtime automatically runs
  git_status/git_diff and checks them; every
  `ValidationClaim{claim_text, command_event_id}` is cross-verified against the
  event stream (the event exists, is of type command_exec, and its exit code
  matches the claim) -- any mismatch refuses `completed` (downgrading to
  `partial` with an explanation).
- Baseline integrity: the completion check verifies HEAD has not been moved (a
  model that commits or resets through run_command pollutes the evidence
  chain); any ref movement is recorded as an event and downgrades to `partial`.
- Read-only tasks (Q&A, reviews) take the `completed(no_changes)` path: a git
  status matching the baseline satisfies it.
- **Claims may be empty**: explicitly declaring "no verification was performed
  for this task" is valid disclosure; a fabricated or inferred success is not
  (absorbed from the Codex version).
- **A report that separates attribution**: the final report distinguishes
  "files this session touched" from "files already dirty in the baseline, or
  modified concurrently during the session"; it claims only the file versions
  it can prove, and never claims line-level attribution.
- Other states: `partial / blocked / failed / cancelled` (plus `interrupted`,
  determined at recovery), each of which must carry a termination reason event;
  a session has **exactly one** termination event.

## 7. Packaging and dependencies

- Python 3.12; a pure-Python wheel (`py3-none-any`); hatchling as the build
  backend (version-pinned).
- **The dependency budget ([D-014](decision-log.md); enumerated, and a new
  dependency needs a decision record)**:
  - Required at runtime: `rich` (terminal rendering, 4 pure-py wheels)
  - Optional at runtime: `prompt_toolkit` (enhanced approval/input, 2 wheels
    with wcwidth)
  - **Explicitly not used**: httpx/requests (~300 lines of our own on stdlib
    http.client + ssl, bought in exchange for zero-configuration use of the
    Windows system certificate store and 0 wheels), keyring (DPAPI through
    ctypes directly, and its backend has a 2560-byte ceiling), psutil (Job
    Objects through ctypes), pydantic (dataclasses + handwritten validation),
    textual, tree-sitter.
- Pinning: `requirements.in -> pip-compile --generate-hashes -> the wheelhouse
  (pip download --only-binary :all:)`; installation is `pip install --no-index
  --find-links wheelhouse --require-hashes foundry`; CI checks the wheelhouse
  is complete for cp312/win_amd64 (or pure py3).
- Code layering: a single wheel, but `foundry.core` must not import
  `foundry.cli` (CI-enforced), so a future split costs nothing.
- Every `open()` passes `encoding='utf-8'` explicitly; CI runs with
  `-X warn_default_encoding`.

## 8. V1 acceptance criteria (entirely absent in v0.1)

1. **Install acceptance**: a clean Windows 11 + Python 3.12 with no external
   network installs successfully from the wheelhouse with `pip --no-index` and
   completes `foundry doctor`.
2. **The golden task set** (a small sample repository built as a fixture and
   committed, [D-020](decision-log.md); 5-10 tasks): at least one each of
   fixing a failing test, adding a test, a small refactor, and a read-only
   question; each specifies the expected termination state and the shape of the
   evidence. The regression method (a review correction): ReplayBackend replays
   by ordinal with **structural assertions** on the request (the tool call
   sequence, key fields), with the full request diff emitted as a test artifact
   for human review; "byte-for-byte rebuild" is a separate resume-ready test.
   Prompt or loop changes must pass the whole set of structural assertions; the
   fixture re-recording workflow (`foundry record`, re-recorded against a local
   endpoint) is an M0 deliverable.
3. **Negative cases**: an out-of-bounds, unknown or malformed tool call is
   refused and the loop survives; a DENY cannot be overridden by an ALLOW
   (including at the managed layer); every path escape sample (junction, ADS,
   `..`, device names, drive-relative, UNC) is refused; cancelling a running
   multi-level child process tree leaves no orphans; an ASK timeout = DENY;
   `completed` is refused when a validation claim is forged (citing a
   nonexistent event).
4. **The canary leak suite** (absorbed from the Codex version): run the whole
   flow with a canary credential and assert it appears in **none** of the
   console, the prompt, the session journal, artifacts, exception strings, or
   diagnostic exports -- the only way "credentials do not leak" moves from a
   claim to something acceptable against.
5. **The dirty working-tree matrix**: staged / unstaged / untracked / renamed /
   deleted / non-UTF-8 and binary / concurrently modified / editing a file
   already dirty in the baseline -- proving existing work is neither discarded
   nor misattributed; **test fixture restoration must not use destructive git
   commands**.
6. **Crash recovery**: a truncated journal can never be judged completed; every
   termination state can be reconstructed from the redacted log.
7. **Offline test acceptance**: every unit and integration test is green on a
   machine with no network and no credentials.
8. **Real E2E**: at least one golden task completes on the personal API key
   and (when available) on the corporate Gateway.

## 9. Non-goals (explicitly not in V1)

A sandbox (an honest trusted host); session resume (the schema is ready, the
feature is V2); LLM summarisation compaction (V1 uses masking + a clean
termination); MCP (aligning the internal representation with its shapes is
enough); subagents (a seam is left); multi-repository tasks; browser/network
tools; embeddings/RAG; automatic commits; skills and slash commands (V2);
Claude consumer OAuth; NTLM/Kerberos proxies.
