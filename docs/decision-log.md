# Foundry decision log

> Format: one number per decision. **Status**: `confirmed` (the user ruled) /
> `provisional` (proposed by Claude, awaiting the user) / `overturned`.
> An overturned decision is never deleted; its status changes and it links to
> the decision that replaced it.

---

## D-001 A wholly independent runtime: no forking, no calling into an existing coding agent
- **Date**: 2026-08-29 · **Status**: confirmed
- **Decision**: Foundry owns all of the code and interfaces for its agent loop, tools, policy, providers and sessions. It may study the public source of Codex, Claude Code, gemini-cli and others for design ideas, but it does not fork them, vendor them, or call them at runtime.
- **Rationale**: the corporate environment does not permit installing official agent products (only the corporate Gateway's API is available); and the project has a learning purpose of its own (understanding agent runtime design thoroughly).

## D-002 Python 3.12 + an offline wheel install
- **Date**: 2026-08-29 · **Status**: confirmed
- **Decision**: pin Python 3.12; build a standard wheel; the target installation is `python -m pip install --no-index --find-links <internal-wheel-dir> foundry`; dependencies are version-pinned with hashes; no Node.js or Rust toolchain is required.
- **Rationale**: software sources on corporate machines are restricted (JFrog Artifactory, with Node/Rust availability case by case); Python plus an internal wheel directory is the most certainly workable distribution channel.

## D-003 V1 is a trusted host and claims no sandbox
- **Date**: 2026-08-29 · **Status**: confirmed
- **Decision**: V1 is for trusted repositories only. Approval (policy) is a behavioural constraint, not a security boundary, and both the documentation and the CLI's first run must disclose this plainly.
- **Rationale**: a real sandbox on Windows (AppContainer / Job Object restrictions and the like) is expensive and easily gives a false sense of security; honest disclosure beats a half-built sandbox.

## D-004 The interactive terminal session comes first; headless follows
- **Date**: 2026-08-29 · **Status**: confirmed (the user's first round of answers)
- **Decision**: V1's core form is an interactive terminal session: conversation, streaming output, and side-effecting actions approved on the spot (ASK). A one-shot headless mode (something like `foundry exec`) comes in a later version.
- **Architectural constraint**: the UI and AgentRuntime are decoupled from day one -- the runtime exposes only an event stream plus an approval callback interface, and the terminal UI is merely its first consumer. Headless mode is then just a different consumer that "answers approvals automatically", with no change to the loop.

## D-005 ChatGPT login stays the highest priority
- **Date**: 2026-08-29 · **Status**: **overturned -> [D-009]** (the feasibility study confirmed it was blocked, and the user switched to an API key)
- **Decision**: browser-based ChatGPT login for the personal path remains the first hard requirement.
- **Rationale (the gist of the user's own words)**: the development environment has no OpenAI API key, so nothing can be verified through the API; the ChatGPT subscription is the only real model access available, and real E2E verification depends on that login working.
- **Risk and mitigation**: this remains the most uncertain item in the project (third-party use of a ChatGPT subscription may have no supported route, see [OQ-5](open-questions.md)). Mitigations:
  1. the bulk of runtime development does not depend on a real model -- a **mock / record-replay ModelBackend** must come first, so the loop, tools, policy and session are all testable offline;
  2. evaluate a **local OpenAI-compatible endpoint** (LM Studio / Ollama and the like) as a dev-only backend for smoke tests (see [OQ-8](open-questions.md));
  3. decide the fallback ladder only once the feasibility study produces evidence, rather than quietly downgrading.

## D-006 The corporate Gateway is multi-model (including non-OpenAI models such as Claude)
- **Date**: 2026-08-29 · **Status**: confirmed (the user's first round of answers)
- **Decision**: the Gateway hosts models from several vendors. The draft's assumption of "support OpenAI Responses-compatible first" **does not hold as the only assumption**.
- **Architectural constraints**:
  1. the internal message / tool call / streaming event types must be a **provider-agnostic specification of our own**, with a narrow adapter per protocol (Chat Completions / Responses / Anthropic Messages / a Gateway-specific protocol);
  2. the tool surface (especially the edit tool's format) must not be nailed to an OpenAI-specific format (such as V4A apply_patch); it needs to be configurable per model family, or to use a neutral format (see [OQ-7](open-questions.md));
  3. the Gateway's specific protocol, authentication and model list await the user's confirmation (see [OQ-6](open-questions.md)).

## D-007 Design priority: auditability / few dependencies / a clear implementation > piling on features
- **Date**: 2026-08-29 · **Status**: confirmed (derived from the motivation)
- **Decision**: the motivation is corporate compliance constraints + learning. The user has assembled a mini-codex with the Claude Agent SDK and found the experience mediocre -- Foundry has to win on "complete control + design quality", not on feature count.
- **Corollary**: a narrow but reliable tool surface is preferable; every dependency must pass the "is it worth a slot in the offline wheel directory" review; decisions and trade-offs must leave a trace (this document).

## D-008 Claude consumer OAuth is not part of V1
- **Date**: 2026-08-29 · **Status**: confirmed (inherited from the draft)
- **Decision**: the personal path in V1 covers ChatGPT/OpenAI only; Claude subscription login is not built. The Claude models on the corporate path go through the Gateway and have nothing to do with consumer OAuth.

## D-009 The personal path is an OpenAI platform API key; ChatGPT login is marked blocked-with-evidence
- **Date**: 2026-08-29 · **Status**: confirmed (the user's second round of answers, on the feasibility evidence)
- **Decision**: research ([research/auth.md](research/auth.md)) confirmed that a non-Codex third-party tool has no supported way to run inference on a ChatGPT subscription -- the official "Sign in with ChatGPT" grants identity, not inference; the Codex subscription endpoint has an originator allowlist (a non-Codex client gets 403), so using it requires impersonating Codex, which violates both OpenAI's ToS and this project's charter, and has led to bans before. Following the process agreed in v0.1 §3.1: mark it blocked, keep the evidence, ask the user. The user chose an OpenAI platform API key for the personal path.
- **Accompanying**: development verification does not depend on a key -- ReplayBackend (full offline coverage) + a local OpenAI-compatible endpoint (LM Studio/Ollama smoke tests, which the user accepted); only real cloud E2E consumes the key. Overturns [D-005].

## D-010 The edit format is a model-neutral anchored search/replace envelope
- **Date**: 2026-08-29 · **Status**: confirmed (the user's second round of answers)
- **Decision**: apply_patch uses an "envelope (Add/Delete/Update File) + anchored-text search/replace hunks" format (no line numbers), with the patch passed as a single opaque string argument; small files get a whole-file escape hatch; the format description goes explicitly into the system prompt.
- **Rationale**: the Gateway is multi-model (Claude included), Codex's V4A is a GPT-private trained format (the official support list is GPT-5.x only) and Claude mangles it; Claude Code, OpenHands and aider all converged independently on anchored text replacement; aider measured a 9x reduction in error rate from a lenient application gradient. The alternative of "two formats by model family" was rejected (it doubles the test surface), leaving an `edit_format` downgrade field in the per-model profile.

## D-011 Dirty working tree = warn and continue + a forced ASK on writes to dirty files
- **Date**: 2026-08-29 · **Status**: confirmed (the user's second round of answers)
- **Decision**: record a baseline at session start (the HEAD SHA + the list of dirty files); force an ASK for apply_patch on any file already dirty in the baseline; destructive commands such as `git checkout -- / reset --hard / clean / stash drop` go into a built-in, non-relaxable DENY (the circuit breaker).

## D-012 The session schema is replayable from day one; the resume feature is deferred to V2
- **Date**: 2026-08-29 · **Status**: confirmed (the user's second round of answers)
- **Decision**: the JSONL records enough to rebuild every model request byte for byte (which is also the basis of ReplayBackend testing); the feature layer of `foundry resume` (listing / selection / state validation) is deferred to V2.

## D-013 PolicyEngine = a six-step pipeline + a deny-wins merge rule + a circuit breaker
- **Date**: 2026-08-29 · **Status**: confirmed (the user's third round, confirmed as a batch)
- **Decision**: `pre_tool callback -> DENY -> ASK -> mode baseline -> ALLOW -> interactive approval (headless = DENY)`; rules concatenate across layers, and deny-from-anywhere wins; a fixed precedence order, rejecting numeric priorities; a "can't parse -> ASK" safety valve; a hard-coded circuit breaker (.git, ~/.foundry, destructive commands).

## D-014 The dependency budget: stdlib first, with only rich at runtime (+ optional prompt_toolkit)
- **Date**: 2026-08-29 · **Status**: confirmed (the user's third round, confirmed as a batch)
- **Decision**: HTTP/SSE written on the stdlib (switching to the Windows system certificate store means zero configuration, and a corporate MITM proxy needs none); DPAPI and Job Objects through ctypes; no httpx/requests/keyring/psutil/pydantic/textual. The rationale and the rejected comparison are in [design.md](design.md) §11.

## D-015 read_artifact = retrieving over-limit tool output from disk
- **Date**: 2026-08-29 · **Status**: confirmed (the user's third round, confirmed as a batch)
- **Decision**: an artifact is the complete original text of a tool output from this session that exceeded the context budget and was written to disk, addressed by artifact_id, read-only, confined to the session directory. It pairs with the ContextManager truncation policy.

## D-016 The concurrency model is an asyncio core
- **Date**: 2026-08-29 · **Status**: confirmed (the user's third round, confirmed as a batch)
- **Decision**: streaming, cancellation and timeouts are expressed with asyncio (the Windows ProactorEventLoop supports subprocesses); synchronous tool bodies are wrapped in `asyncio.to_thread`. Interface signatures are frozen on that basis -- converting from sync to async afterwards is a rewrite.

## D-017 The finish tool is the only channel that produces a termination state and ValidationClaims
- **Date**: 2026-08-29 · **Status**: confirmed (the user's third round, confirmed as a batch; the mechanism was added after adversarial review found it missing)
- **Decision**: the V1 tool surface goes from 8 to 9: `finish{status, summary, claims:[{claim_text, command_event_id}]}`; the runtime verifies (the event exists, the exit code matches, the git check, HEAD has not moved) before emitting a Termination; a mismatch downgrades to `partial`. An ordinary turn in an interactive session ends normally without calling finish; a session closed with no finish is recorded as `cancelled`/`partial` depending on context.
- **Rationale**: a machine-enforceable completed gate with no structured production channel degrades into a gentlemen's agreement over free text -- and the acceptance item "forgery is refused" would have nothing to be implemented on.

## D-018 The single shell for run_command is Windows PowerShell 5.1 (powershell.exe -NoProfile)
- **Date**: 2026-08-29 · **Status**: confirmed (the user's third round, OQ-13)
- **Decision**: zero extra dependencies, preinstalled on all Windows. The segmenter is written for 5.1 syntax (`;`, the pipe `|`; **no `&&`/`||`**); the system prompt tells the model explicitly that "5.1 has no `&&`, use `;` instead"; a model that misuses them gets a clear parse error it can correct itself. Rejected: cmd (models are less fluent in it), Git Bash (its installation cannot be assumed), pwsh 7 (needs ~100MB of offline distribution, against the dependency budget).

## D-019 The repository instruction file FOUNDRY.md/AGENTS.md makes V1 (delivered in M2)
- **Date**: 2026-08-29 · **Status**: confirmed (the user's third round, OQ-17)
- **Decision**: a `FOUNDRY.md` at the project root (with `AGENTS.md` read for compatibility) declares the build/test commands and repository caveats, injected into the context with trust gating and a byte ceiling; verification command precedence is: specified in the task > declared by the repository > chosen by the model.

## D-020 The golden acceptance task set is a small sample repository we build ourselves (the fixture is committed)
- **Date**: 2026-08-29 · **Status**: confirmed (the user's third round, OQ-14)
- **Decision**: build a Python sample project of a few dozen files (with deliberate bugs and tests) as a fixture committed into the Foundry repository; it is portable, shareable and usable offline; corporate repositories are an M3 supplement to acceptance.

## D-021 V1 is not open source for now, and the license is deferred
- **Date**: 2026-08-29 · **Status**: confirmed (the user's fourth round; the Codex blueprint had proposed Apache-2.0 open source)
- **Decision**: proceed in a private repository, license to be decided. The documents may keep corporate context without sanitising it. If it is ever open-sourced, the following must be added: a provenance/license review convention, and generalising the corporate details.

## D-022 Accept the corporate Gateway intelligence: the OpenAI family goes through Responses, and the token comes from intranet auth
- **Date**: 2026-08-29 · **Status**: confirmed (the user's fourth round, from their answers to Codex)
- **Decision**: the OpenAI-family models on the Gateway support the **Responses API**; the token is obtained through an **intranet auth flow** and used with the Gateway URL (the exact mechanism -- an HTTP exchange / an internal executable / browser SSO -- is still to be confirmed); Claude models exist but their wire protocol is unverified.
- **Impact**: the `responses` adapter is promoted from "added if needed" to **mandatory for M3**; OQ-6 narrows to "the intranet auth mechanism + the endpoint's shape + the model list"; **the M3 entry gate is obtaining the Gateway's tool-call streaming redaction fixtures** before implementing ("Responses-compatible covers conversation but not tool continuation" is an expensive rework risk).
- **Accepted in the same batch**: the target Windows environment cannot create symlinks and has no Developer Mode (-> V1 refuses reparse points across the board); proxy / custom CA / mTLS are not V1 acceptance items (the capability is kept).

## D-023 apply_patch still goes through interactive approval by default (rejecting the Codex version's default allow)
- **Date**: 2026-08-29 · **Status**: confirmed (the user's fourth round)
- **Decision**: the Codex blueprint's FR-POL-04 argues for allowing exact patches inside the workspace by default (matching the Codex CLI default). The ruling: keep our default of interactive approval; once the user is comfortable, switching to `accept_edits` gives the same experience. It is safer during the trust-building period. The forced ASK on dirty files and the breaker table apply in both modes.

## D-024 Absorb the 13 engineering points from the Codex blueprint
- **Date**: 2026-08-29 · **Status**: confirmed (the user's fourth round: ours is the trunk, absorbing what is worth absorbing)
- **Decision**: the canary leak suite | crash recovery semantics (tolerating truncation, no termination event means `interrupted`) | content-addressed artifacts | failure fingerprints (counted by normalised operation + error class, not reset by a change of wording) | approvals bound to cwd/env/validity | policy decisions recording the rule ID and a policy digest | an env configuration layer + secrets forbidden in repository config and CLI arguments | file tool paths limited to workspace-relative (absolute and device paths not expressible) | claims may be empty but must then disclose "not verified" explicitly | a report that separates attribution of changes | adapter contract tests against a fake HTTP server + negative assertions | the CredentialSource contract and a non-printable SecretHandle | the dirty working-tree matrix and "fixture restoration must not use destructive git".
- **Source**: see [research/codex-blueprint-comparison.md](research/codex-blueprint-comparison.md) (the Codex branch `codex/create-branch-codex-blueprint` / PR #1 is kept as an archive and not merged).

## Review revisions (the 2026-08-29 adversarial review; see the git history and the review archive)
- §4.1, fixing a mechanical contradiction: the read-only default is a built-in ALLOW rule (step 5); the mutator default falls to step 6 approval (not an ASK rule, or accept_edits and approval persistence would never take effect); the dirty-file ASK is a built-in ASK rule (step 3, overriding accept_edits); the mode baseline definitions were added (dont_ask = fail-closed DENY).
- The approval "always" rule is written into the user layer (keyed by workspace) rather than a file inside the workspace; the breaker gains write protection for `<workspace>/.foundry/` -- closing self-escalation under accept_edits.
- The read-only allowlist gained argument constraints (paths go through containment); bare git was removed from the allowlist (to prevent bypassing the hardened git tools).
- apply_patch semantics were unified as **per-file atomic** (the original text mixed codex's all-or-nothing with aider's partial application).
- The secrets choke point scope was corrected: byte-level exact match, before base64, covering artifact writes and reads and event emission; model_request does not persist the auth header.
- The breaker table was canonicalised (alias normalisation moved ahead of it; `git restore`/`stash clear` and the PowerShell/cmd delete forms were added).
- The ReplayBackend matching contract: replay by ordinal + structural assertions (not byte-for-byte, or a prompt tweak turns the whole set red); the `foundry record` re-recording workflow moved into M0.
- M1 was split into M1a (file tools, not blocked by OQ-13) and M1b (run_command + the segmenter); the `responses` adapter was demoted to "added if needed".

## D-025 The breaker table covers every git subcommand that moves HEAD; `git clean -n` is an exception
- **Date**: 2026-08-30 · **Status**: confirmed (review round six)
- **Background**: the system prompt said by hand that "git commit/push/rebase/merge are always refused and cannot be approved", yet `git pull` -- which performs exactly that merge -- walked straight past the breaker table. A user writing one `run_command`/`git *` allow rule auto-approves it; and because it moves HEAD, `_finalize` then downgrades the whole run to `partial`, so **a session that ran an "allowed" command can never report completed**. The same holds for `cherry-pick` / `revert` / `am`, while `git apply` bypasses apply_patch's read-first requirement, dirty-file guard and anchor resolution.
- **Decision**: `HISTORY_MOVING_GIT` gains pull / cherry-pick / revert / am; `git apply` is listed separately (with a reason pointing at apply_patch). In the other direction, `git clean -n|--dry-run` only lists and deletes nothing, and was previously refused as "destroys uncommitted work" -- which is false for it, and it is precisely the model's only way to "look before asking", so it is exempted per reading (a flag the naive reading cannot see unlocks nothing).
- **Preventing a repeat**: that paragraph of the prompt is generated by `categorical_denials()` from the breaker table's constants; `test_prompt_matches_breaker.py` asserts alignment in both directions. A hand-written list drifts, and this one did.

## D-026 `command_timeout_s` is a ceiling rather than a default, shipped equal to the tool's own ceiling
- **Date**: 2026-08-30 · **Status**: confirmed (review round six)
- **Decision**: the key had type checking, range checking, provenance, and "the repository can only tighten" protection -- and no code read it. It now reaches the tool layer through `ToolContext.max_command_timeout_s` and is clamped in `RunCommand.execute`; the tool keeps its own lower default (120s), and the configuration item ships at the tool's ceiling (600s), so **the out-of-the-box behaviour is unchanged** while operations or a repository lowering it really takes effect, with the timeout message explaining why it was clamped.
- **In the same batch**: `session_retention_days` still has no implementation ([OQ-19](open-questions.md)) and is annotated in the code as reserved -- deleting a user's session logs on a default they never chose is not something to do casually.

## D-027 The event stream is redacted as a stream (keeping a lookback window), not event by event
- **Date**: 2026-08-30 · **Status**: confirmed (found by the clean-room check)
- **Background**: redaction.py declares three sinks, and the event one was never implemented -- the journal wrote `[redacted]` while `foundry exec --json` printed the same credential verbatim to stdout. After field-by-field redaction was added, the clean-room end-to-end still caught a leak: the model's text arrives **streamed in chunks**, the credential fell across two `MessageDelta`s, neither fragment matched, and the renderer reassembled it on screen.
- **Decision**: `EventSink` retains a tail whose length is "the longest credential Foundry actually holds" -- which is exactly the removal scope this module promises -- and releases it when the next chunk arrives or the stream ends. A **pattern** match longer than that tail can still be missed across chunks; pattern scanning is already labelled best-effort, and that positioning does not change.
- **Lesson**: a cross-layer property has to be verified across layers. The unit tests saw a clean event stream; the terminal showed plaintext.

## D-028 The connection class for a proxy tunnel must follow the **target's** scheme; a TLS-fronted proxy is refused explicitly
- **Date**: 2026-09-01 · **Status**: confirmed (the proxy path audit that the live endpoint prompted)
- **Background**: when choosing a connection class for an https target, `_connect` looked at the **proxy's** scheme. An ordinary `HTTP_PROXY=http://proxy:8080` therefore built a plain `HTTPConnection` and called `set_tunnel`, and `HTTPConnection.connect()` stops once CONNECT is sent -- it never calls `wrap_socket`. The result: **the API key, the prompt and the entire conversation crossed the tunnel in plaintext**, readable by the proxy and every hop after it. Reproduced against a real socket: the first byte into the tunnel is `'P'`, not `0x16`. The default `base_url` is `https://api.openai.com/v1`, so this is **the default path on any machine with a proxy configured**.
- **Decision**: the connection class follows the target's scheme -- an https target always uses `HTTPSConnection(proxy, context=...)` + `set_tunnel(origin)`, i.e. a plaintext CONNECT to the proxy and then TLS to the origin. The stdlib's `set_tunnel` cannot express "TLS to the proxy as well", so an `https://` proxy raises a `ConfigError` explaining the limitation rather than being quietly treated as one of the two. The non-tunnel branch (an http target through a proxy) now returns `Proxy-Authorization` to the request.
- **Preventing a repeat**: the test must **capture the bytes and look at them** -- assert the first byte into the tunnel is `0x16`, and that the canary is not in the plaintext. Asserting "it connected to the proxy host" does not count: the version that leaked satisfied that assertion too.

## D-030 Evidence must come **after** the change it claims to verify
- **Date**: 2026-09-01 · **Status**: confirmed (subsystem audit)
- **Background**: `verify_claims` only asked "is this command's exit code right", never "did it run after the change being verified". So: the model runs the tests green -> edits the code -> does not run them again -> finish cites the green run from **before** the edit. The exit code matches perfectly, and so a session that **verified nothing** reported `completed`.
- **Decision**: the runtime records the journal ordinal of the last change that successfully modified the workspace, and the gate refuses any claim numbered earlier than it. A change means a mutator tool (`apply_patch` and the like); `run_command` does not count -- the command is the source of the evidence itself.
- **Note**: this is a hole **in the project's core promise** that survived six rounds of adversarial review. Those rounds all attacked the segmenter and patching; the "ordering" dimension of the evidence chain had never been examined. There is now an end-to-end regression.

## D-029 A single BLOCKED does not end the REPL session
- **Date**: 2026-09-01 · **Status**: confirmed
- **Decision**: a dropped stream or an unreachable gateway ends **this round**, not the session. Previously any `outcome.status is not None` broke out, so one bit of network flakiness cost the user the whole conversation context -- while the comment on the code that raised it said "transient, so it will retry", and in fact no layer retries (the deltas are already on screen, and the round cannot be replayed). The comment now tells the truth, and BLOCKED prints a message and continues.
