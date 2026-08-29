# Foundry V1 product requirements

**Status:** Draft for review
**Revision:** 0.2
**Target:** Windows 11, CPython 3.12, offline-installable wheel

## 1. Purpose

Foundry is a new, open-source, local-first coding-agent runtime. Given an
explicit engineering task and a local Git repository, it lets a model inspect
the repository, edit files, run validation, and return an evidence-backed
result. Foundry owns its agent loop, tool contracts, policy, provider boundary,
and session record; it neither embeds nor invokes another coding agent.

V1 is an interactive CLI with deliberately internal Python interfaces. Those
interfaces should make later automation possible, but a stable public Python
SDK and unattended execution are not V1 commitments.

## 2. Product principles

1. **One runtime, multiple model connections.** Personal development and the
   corporate Gateway use the same loop and tool implementation.
2. **Evidence over confidence.** A model assertion is not proof that a command
   ran or a file changed.
3. **Explicit trust boundary.** V1 runs directly on a trusted Windows host.
   Approval and path checks reduce mistakes; they are not containment.
4. **Local and inspectable.** Session data remains local by default, telemetry
   is off, and secrets are excluded at their sources and redacted again at log
   sinks.
5. **Protect the user's work.** A dirty worktree is supported. Foundry must not
   discard, overwrite wholesale, stage, commit, or otherwise claim existing
   changes as its own.
6. **Protocols before providers.** Model-specific code translates a protocol;
   it does not create a second loop or bypass policy.
7. **Bounded operation.** Every run has finite turns, tool calls, elapsed time,
   command time, and captured output.

## 3. Confirmed context and decisions

| Area | Decision |
|---|---|
| Audience | One developer initially; architecture must not assume one identity forever |
| Repository | V1 operates only on a local Git repository |
| UX | Interactive CLI first; preserve an internal boundary for later programmatic use |
| Host | Windows 11 trusted host; no sandbox claim |
| Language/package | CPython 3.12; standard wheel; offline dependency wheelhouse |
| Personal model path | Seek an officially supported ChatGPT Plus/Pro subscription sign-in; do not assume it exists for an independent client |
| Development fallback | Runtime and tools must be testable with deterministic fake/replay backends while personal authentication is unresolved |
| Corporate path | Existing internal Gateway; OpenAI models support the Responses API; token obtained from an internal-subnet auth flow, then used with Gateway URL |
| Other corporate models | Claude models exist, but their wire protocol and tool-call semantics are unverified |
| Windows constraints | Browser, Git, PowerShell, and arbitrary child processes allowed; user profile is writable; symlink creation and Developer Mode are unavailable |
| Enterprise networking | Proxy, custom CA, and mTLS are not V1 requirements unless discovery contradicts this |
| Persistence | Local by default; no telemetry upload |
| Autonomy | Model may edit multiple files and run commands, subject to policy |
| Git | Dirty worktrees supported; automatic commit, push, PR, publish, and deploy prohibited |
| License | Open-source intent; exact license remains open |

## 4. Primary user journeys

### 4.1 Start a repository task

The user starts `foundry` inside, or points it at, a Git worktree. Foundry:

1. resolves and validates the repository root;
2. records repository identity, current branch/HEAD, status, and a baseline
   representation sufficient to distinguish pre-existing changes;
3. displays the trusted-host warning and effective limits/policy;
4. accepts a task and begins one bounded session.

### 4.2 Perform agentic work

The model receives task context and declared tool schemas. It may iteratively
request reads, searches, patches, commands, and Git inspection. Foundry validates
each call, asks for approval when policy requires it, executes it, normalizes the
result, persists a redacted event, and continues until a terminal condition.

### 4.3 Finish with evidence

Before reporting `completed`, Foundry obtains a fresh Git status/diff and checks
every claimed validation against recorded command, exit code, and timing data.
It summarizes files changed by this session separately from pre-existing user
changes, validations, residual risks, and the termination reason.

### 4.4 Authenticate

- **Personal research path:** a supported ChatGPT subscription sign-in, credential
  persistence, refresh, and logout, only if public official contracts permit an
  independent client to do so.
- **Corporate path:** an adapter calls the internal-subnet auth mechanism, keeps
  the acquired token out of model context and logs, and supplies it to a
  Responses-compatible Gateway transport.

## 5. Functional requirements

Requirement terms use **MUST**, **SHOULD**, and **MAY** normatively.

### 5.1 CLI and configuration

- **FR-CLI-01:** The CLI MUST provide task execution, session inspection, config
  diagnosis, and provider authentication/logout entry points.
- **FR-CLI-02:** Configuration MUST have documented precedence: command-line
  flags, environment, repository config, user config, built-in defaults. Secrets
  MUST NOT be accepted in repository config or ordinary CLI arguments.
- **FR-CLI-03:** Startup MUST show repository root, provider/model, policy mode,
  current limits, and trusted-host warning before the first side effect.
- **FR-CLI-04:** Ctrl+C MUST initiate cancellation, stop scheduling tools, clean
  up the current process tree, write a terminal event, and return a stable
  nonzero exit code.

### 5.2 Provider, auth, and model protocol

- **FR-PROV-01:** `ModelBackend` MUST expose one runtime-owned streaming turn
  contract and normalized output events: text delta, reasoning metadata if
  permitted, tool request, usage, completion, and provider error.
- **FR-PROV-02:** Backends MUST advertise capabilities rather than requiring the
  runtime to infer them (for example tool calls, parallel calls, continuation
  identifiers, and usage reporting).
- **FR-PROV-03:** Responses-compatible transports MUST preserve provider-issued
  opaque continuation and tool-call identifiers without interpreting them.
- **FR-PROV-04:** Retry MUST be limited to errors classified as transient and
  MUST respect server retry hints. Foundry MUST NOT blindly retry a semantically
  ambiguous write-producing request.
- **FR-AUTH-01:** Authentication MUST be separate from model protocol transport.
- **FR-AUTH-02:** Tokens MUST never enter prompts, tool results, persisted config,
  session events, exception strings, or diagnostic bundles.
- **FR-AUTH-03:** Credential persistence SHOULD use a Windows OS-protected store;
  the exact mechanism is an implementation-gated decision.
- **FR-AUTH-04:** Personal subscription login MUST remain disabled unless a
  supported public integration contract is verified. Foundry MUST NOT scrape a
  session, use private endpoints, copy another client's cache, or silently fall
  back to paid API credentials.
- **FR-AUTH-05:** The corporate credential source MUST be replaceable and MUST
  support acquisition, expiry, refresh/reacquisition, and logout/cache removal.

### 5.3 Runtime loop

- **FR-LOOP-01:** The runtime MUST own the only loop: prompt assembly, provider
  turn, tool validation, policy decision, approval, execution, result delivery,
  and termination.
- **FR-LOOP-02:** Limits MUST cover provider turns, requested/executed tool calls,
  session wall time, per-command wall time, provider retries, and bytes retained
  per result/session.
- **FR-LOOP-03:** Unknown tools, invalid schemas, invalid paths, stale approvals,
  and denied operations MUST never reach an executor.
- **FR-LOOP-04:** Repeated equivalent failures MUST terminate or require user
  intervention after a configurable small threshold; textual variation alone
  MUST NOT reset the counter.
- **FR-LOOP-05:** Parallel model tool requests MAY be represented, but V1 SHOULD
  execute mutating or command tools serially. Read-only calls may later run in
  parallel after deterministic event ordering is defined.
- **FR-LOOP-06:** Provider conversation state and the local audit event stream
  MUST be separate concepts. A resumable provider response is not the session
  source of truth.

### 5.4 Tools

V1 tools are `list_files`, `search_text`, `read_file`, `apply_patch`,
`run_command`, `read_artifact`, `git_status`, and `git_diff`.

- **FR-TOOL-01:** Every tool MUST have a versioned, closed input schema; unknown
  fields and malformed values are rejected before policy evaluation.
- **FR-TOOL-02:** File inputs MUST be workspace-relative logical paths. Absolute
  paths, device paths, alternate data streams, traversal, and reserved Windows
  names MUST be rejected.
- **FR-TOOL-03:** Before every file operation, the executor MUST validate the
  final resolved path and each existing ancestor against the workspace boundary,
  rejecting reparse points (including symlinks and junctions) by default.
- **FR-TOOL-04:** File reads MUST be bounded and explicitly report truncation,
  encoding decisions, and binary detection.
- **FR-TOOL-05:** `apply_patch` MUST be atomic per file, verify expected context,
  preserve unrelated content, and fail rather than guess after a stale-context
  mismatch. A multi-file request MAY partially apply only if the result clearly
  identifies each committed and rejected file operation.
- **FR-TOOL-06:** `run_command` MUST use an explicit executable/argument
  representation internally. If a PowerShell command string is exposed to the
  model, it MUST be visibly classified as shell interpretation and reviewed by
  the stricter command policy.
- **FR-TOOL-07:** Command execution MUST set cwd, timeout, environment policy,
  capture limits, and process-tree cancellation. Output MUST stream through a
  secret scrubber before persistence or model delivery.
- **FR-TOOL-08:** `read_artifact` MUST only read an artifact handle previously
  issued by Foundry; it MUST not be a second unrestricted path reader.
- **FR-TOOL-09:** Git tools MUST use machine-oriented output where possible and
  preserve evidence needed to distinguish staged, unstaged, untracked, renamed,
  and pre-existing changes.

### 5.5 Policy and approval

- **FR-POL-01:** Policy returns `ALLOW`, `ASK`, or `DENY` with a stable rule ID
  and human-readable reason before every side effect.
- **FR-POL-02:** Effective policy MUST be monotonic: centrally fixed denies
  cannot be weakened by repository config, task text, model output, or a
  one-time approval.
- **FR-POL-03:** Approval MUST bind to the exact normalized operation, cwd,
  relevant environment policy, and expiry. Changing any bound field invalidates
  it.
- **FR-POL-04:** The default V1 policy SHOULD allow workspace reads and precise
  patches, ask for commands and destructive overwrites, and deny writes outside
  the workspace plus Git/network publication operations.
- **FR-POL-05:** Foundry MUST repeatedly state that approval is not a sandbox and
  commands may access the user's files, credentials, environment, and network.

### 5.6 Git baseline and user-change protection

- **FR-GIT-01:** Dirty worktrees MUST be accepted after displaying a baseline
  summary; a strict-clean option MAY be provided.
- **FR-GIT-02:** Baseline capture MUST record HEAD identity and enough status and
  content metadata to identify files already dirty at session start. A single
  initial `git diff` is insufficient for untracked files and race detection.
- **FR-GIT-03:** Before writing a path, Foundry MUST detect changes since its last
  observed version (content hash or equivalent) and refuse stale writes.
- **FR-GIT-04:** Foundry MUST NOT run destructive restoration/cleanup, stage,
  commit, amend, checkout/switch, rebase, merge, push, or create a PR in V1.
- **FR-GIT-05:** Completion reporting MUST distinguish “touched by Foundry” from
  “pre-existing or concurrently changed”; it MUST NOT claim line-level ownership
  that cannot be proved.
- **FR-GIT-06:** V1 MAY initially be validated on a local `main` branch, but
  branch name MUST NOT change safety semantics or be hard-coded.

### 5.7 Session record and terminal status

- **FR-SES-01:** Sessions MUST be an append-only, versioned JSONL event stream
  with a stable session ID and monotonic sequence numbers.
- **FR-SES-02:** Events MUST include runtime lifecycle, model request metadata,
  normalized model output, tool request/result, policy decision, approval,
  command evidence, validation evidence, usage, and termination. Raw secrets and
  unrestricted environment snapshots are forbidden.
- **FR-SES-03:** Large outputs MUST be stored as bounded local artifacts; events
  reference their digest, size, media type, and truncation state.
- **FR-SES-04:** A session MUST end exactly once as `completed`, `partial`,
  `blocked`, `failed`, or `cancelled`.
- **FR-SES-05:** `completed` requires a fresh final Git inspection and a
  mechanically supported validation summary. “No tests were requested/run” is
  valid evidence disclosure; fabricated or inferred success is not.
- **FR-SES-06:** Crash recovery MUST tolerate a truncated final JSONL record and
  classify a session lacking a terminal event as interrupted, never completed.
- **FR-SES-07:** Retention/deletion MUST be user-controllable; exact defaults are
  open for review.

## 6. Non-functional requirements

- **NFR-SEC-01:** Threat modeling MUST cover prompt injection, malicious repos,
  reparse-point races, command injection, terminal escape sequences, secret
  leakage, oversized output, and compromised model/Gateway responses—despite the
  trusted-repository assumption.
- **NFR-REL-01:** Core runtime tests MUST be deterministic and offline using fake
  provider streams, fake clocks, temporary Git repositories, and controlled
  subprocess fixtures.
- **NFR-PORT-01:** V1 support is native Windows 11. CI on another OS may test pure
  logic but cannot establish Windows path/process correctness.
- **NFR-PKG-01:** The project MUST build a standard Python 3.12 wheel, publish a
  locked dependency set with hashes, and prove installation with `--no-index`
  from a complete wheelhouse on a clean Windows environment.
- **NFR-PKG-02:** Runtime MUST NOT require Node.js, Rust, Codex, Claude Code, or
  another coding-agent installation. Build-time transitive toolchains must also
  be understood before accepting dependencies.
- **NFR-OBS-01:** Structured local diagnostics MUST be useful with all credential
  values removed. Telemetry and remote crash reporting are off by default.
- **NFR-COMP-01:** Because the project is intended to be open source, borrowed
  ideas and dependencies MUST be recorded with provenance and license review;
  code must not be copied merely because it is publicly visible.

## 7. Explicit V1 non-goals

- A security sandbox, VM, container, low-integrity process, or guarantee that a
  command cannot access data outside the workspace.
- Unattended CI operation or a stable public Python SDK.
- Multiple simultaneous agents, subagents, remote execution, or distributed
  sessions.
- IDE integration, GUI/TUI, voice, image/computer use, MCP servers, or plugin
  marketplace.
- Non-Git workspaces, macOS/Linux support commitments, WSL as the runtime, or
  symlink-enabled workspaces.
- Automatic commit/push/PR/publish/deploy or repository branch management.
- Claude consumer OAuth. Corporate Claude support is gated on Gateway protocol
  discovery and is not implied by the OpenAI adapter.
- Prompt caching, semantic code indexing, vector databases, and long-term memory.

## 8. V1 acceptance criteria

V1 is releasable only when all of the following are demonstrated on Windows 11:

1. A wheel installs into a clean Python 3.12 virtual environment entirely from
   an offline wheelhouse.
2. A deterministic fake backend completes golden scenarios for read-only work,
   multi-file editing, validation, denial, malformed calls, retry exhaustion,
   cancellation, provider truncation, and repeated failure.
3. The corporate Responses-compatible path completes a controlled repository
   task using the real internal auth and Gateway without leaking a canary token.
4. Dirty-worktree tests prove pre-existing staged, unstaged, renamed, and
   untracked content is neither discarded nor misreported.
5. Windows tests cover drive-relative/UNC/device/ADS paths, case folding,
   reparse-point rejection, process-tree timeout, Ctrl+C, Unicode filenames and
   output, and terminal-control sanitization.
6. Every terminal state can be reconstructed from a redacted session log; a
   crash-truncated log cannot be mistaken for completion.
7. The CLI gives accurate final status, final Git evidence, commands and exit
   codes, truncation notices, and remaining risks.
8. Security documentation prominently states the trusted-host limitation.

Personal ChatGPT subscription sign-in is a separate release gate: it is part of
V1 only if the feasibility spike verifies an official supported route. Its
absence does not justify implementing an unofficial route; release naming and
developer validation strategy must be revisited if it remains blocked.

## 9. Success measures

Initial measures emphasize correctness rather than usage:

- zero known cases where a denied/malformed call reaches an executor;
- zero test canary credentials in prompts, logs, artifacts, or diagnostics;
- zero loss of pre-existing work across the dirty-worktree suite;
- all claimed validations trace to a recorded command and exit code;
- bounded termination in every adversarial scripted-provider scenario;
- one documented clean offline install and one real Gateway end-to-end run.
