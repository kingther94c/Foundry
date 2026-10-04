# Foundry — Design v0.1

> Companion to [requirements.md](requirements.md) v0.2. This document answers
> "how it is built": package structure, core interfaces, data flow and the key
> mechanisms. Interfaces are given as Python pseudocode -- they are the
> **deliverables that get frozen first** (per the charter: "define your own
> interfaces first, then implement them independently").
> Borrowings are marked (codex) / (claude-code) / (gemini-cli) / (aider) /
> (openhands) / (luban); the details are in [research/](research/).

## 1. Package structure

```text
foundry/
├── core/                    # must not import foundry.cli (CI-enforced)
│   ├── events.py            # event and submission (op) types, the protocol version
│   ├── conversation.py      # the provider-agnostic intermediate representation (IR)
│   ├── runtime.py           # AgentRuntime: the one loop
│   ├── context.py           # ContextManager: projection / truncation / masking / token accounting
│   ├── policy/              # PolicyEngine: rules, modes, the six-step pipeline, the command segmenter
│   ├── tools/               # ToolExecutor + the 8 tool implementations + the workspace boundary
│   ├── backends/            # the ModelBackend protocol + adapters (openai_compat / responses / replay)
│   ├── auth.py              # AuthProvider + DPAPI storage
│   ├── session.py           # SessionStore (JSONL) + the audit log
│   ├── httpc.py             # a stdlib HTTP/SSE client (~300 lines)
│   └── winapi.py            # ctypes: DPAPI, Job Objects, reparse tags
├── cli/                     # the terminal UI: event subscriber + approval UI (rich / prompt_toolkit)
├── prompts/                 # versioned assets: the base system prompt, the patch format description, the permissions section template
└── __main__.py              # entry point: bootstraps in UTF-8 mode
```

## 2. The intermediate representation (IR) -- the foundation under everything

The precondition for two backends sharing one loop. **Frozen first; an adapter
does nothing but convert between the IR and the wire protocol.**

```python
# conversation.py (illustrative; all frozen dataclasses, no bare dicts)
class ContentBlock:      # aligned with the MCP shapes: text | tool_use | tool_result | image (reserved)
    ...

@dataclass(frozen=True)
class Message:           # role: system | user | assistant | tool
    role: str
    blocks: tuple[ContentBlock, ...]

@dataclass(frozen=True)
class ToolCall:
    call_id: str         # how a result is associated when fed back; a round may carry N
                         # (the protocol layer must accept parallel tool calls)
    name: str
    arguments: str       # the raw JSON string; a parse failure = malformed, and it is not executed

@dataclass(frozen=True)
class ToolResult:
    call_id: str
    blocks: tuple[ContentBlock, ...]
    is_error: bool

@dataclass(frozen=True)
class ModelTurn:         # the product of one sampling
    text: str | None
    tool_calls: tuple[ToolCall, ...]
    usage: Usage         # the real usage fields, never estimated (luban: estimates were 36% low)
    raw: bytes           # the raw response, kept in the session for replay/audit
```

## 3. The event protocol (the only channel between core and the UI)

```python
# events.py -- two queues (codex): the UI submits Ops, core broadcasts Events
Op    = UserInput | ApprovalDecision | Interrupt | Shutdown
Event = TurnStarted | MessageDelta | ToolBegin | ToolOutputDelta | ToolEnd \
      | ApprovalRequest | TokenCount | TurnComplete | Termination | Error
PROTOCOL_VERSION = 1
```

- `ApprovalRequest{request_id, kind: command|patch|other, display: str, detail,
  options: [once, session, always, deny, abort]}`; the loop suspends on the
  corresponding future until an `ApprovalDecision{request_id, choice}` is
  submitted. **The display string and the object actually executed share one
  source** (what-you-approve-is-what-runs).
- Approval is an event rather than a blocking `input()`, so an Interrupt can
  arrive during an approval, headless can map ASK->DENY, and a future IDE
  front end comes for free.
- Before rendering any model or tool text, the UI strips ANSI/OSC sequences and
  escapes rich markup (terminal injection is a real attack surface).

## 4. AgentRuntime: the shape of the loop

```python
async def run_turn(user_input):
    enqueue(user_input)
    while True:
        history = context_manager.project(session)        # transcript -> what the model sees
        turn = await backend.sample(history, tools=profile.tool_schemas)
        session.record_model_turn(turn)
        if not turn.tool_calls:
            return end_of_turn(turn)                       # an ordinary turn ends, the conversation
                                                           # continues; a task is closed out through the
                                                           # finish tool -> the termination state machine (§9)
        for call in turn.tool_calls:                       # serial in V1; each passes policy independently
            decision = await policy.evaluate(call)
            result = await execute_or_reject(call, decision)
            session.record_tool(call, decision, result)
        # soft ceilings: consecutive tool rounds, a per-task token ceiling;
        # tripping one means a clean partial/blocked termination
```

- User input arriving mid-turn is queued and merged at the next `project()`
  (codex mid-turn steering).
- A sampling request is the full history, statelessly; one thread uses a stable
  `prompt_cache_key` (where the backend's capabilities allow it).
- The error taxonomy (`TransientError/AuthError/FatalError/PolicyDenied`) is
  mapped by the adapter and handled uniformly in the runtime for retries
  (exponential backoff + Retry-After + a ceiling); an AuthError triggers a
  single-flight refresh (a process lock plus a file lock against multi-instance
  races) and one retry.
- The cancellation propagation chain belongs to the runtime: Ctrl+C ->
  Interrupt op -> cancel the in-flight HTTP read -> TerminateJobObject kills
  the child process tree -> `Termination(cancelled)` is written to disk.
- An asyncio core; blocking tool bodies run in `asyncio.to_thread`.

## 5. ContextManager

- `project(session) -> list[Message]`: **the transcript is not the model
  context**, and the projection is an explicit function (the openhands event
  stream idea), which guarantees that replaying the same session produces a
  byte-identical request.
- Assembly order (codex layering): `[base system prompt] + [a permissions
  section generated from the policy configuration -- telling the model the real
  auto-allow/approval boundary] + [FOUNDRY.md/AGENTS.md (trust-gated, with a
  byte ceiling; D-019, delivered in M2)] + [environment context: OS/shell/cwd/
  git status] + [conversation history]`.
- The output budget: every tool output has a byte ceiling, and over it is
  truncated head+tail with a `[truncated: N bytes, artifact_id=...]` marker,
  with the full output written into the session directory for `read_artifact`
  to retrieve.
- **Observation masking** (V1's "compaction"): tool output older than the last
  N rounds (5 by default) is projected as a one-line stub (`[output elided;
  re-run tool if needed]`); the system prompt, the task, and the most recent
  rounds are kept in full. The evidence: equivalent to LLM summarisation with
  zero extra dependencies (arXiv 2508.21433; SWE-agent +3pt). LLM
  summarisation compaction is V2, and will enter the log as a `Compacted` event
  when it lands (openhands condensation-as-event).
- Token accounting: accumulate the real usage, and as the backend's context
  ceiling approaches, terminate cleanly as `partial(context_exhausted)` --
  never let the request 400.

## 6. PolicyEngine

```python
# the six-step pipeline (the claude-code Agent SDK's published specification, testable step by step)
def evaluate(call) -> Decision:            # Decision = ALLOW | ASK(reason) | DENY(reason)
    1. the pre_tool callback (a configured Python callable, or a stdin-JSON subprocess; may rewrite the input)
    2. DENY rules (merged across every layer; a hit ends it -- no ALLOW can flip it)
    3. ASK rules
    4. the mode baseline (default/accept_edits/plan/dont_ask)
    5. ALLOW rules
    6. interactive approval (headless: DENY)
```

- Rule syntax: `tool` / `tool(pattern)`, fnmatch, with a per-tool target key
  (run_command -> the segmented command string; file tools -> the
  workspace-relative path). The precedence order is fixed, with no numeric
  priorities (gemini-cli as the counter-example).
- Layer merging: **the sole authority on layering is requirements §4.3**
  (settings follow the precedence chain; rules are concatenated across layers
  and then resolved deny > ask > allow, with a managed DENY as the floor, and
  the repository-checked-in layer accepting only deny/ask).
- The command segmenter: a conservative tokenizer written for the single
  chosen shell, with **alias normalisation before segmentation**
  (`rm/ri/del/erase -> Remove-Item` and similar, which breaker matching depends
  on); the shell is settled as Windows PowerShell 5.1 (D-018), and the operator
  set is `;` and the pipe `|` (**5.1 has no `&&`/`||`**, so a model that
  misuses them gets a parse error, which the prompt warns about in advance);
  `$( )`, backticks, the `&` call operator, redirection and env prefixes all
  produce an untrusted segmentation -> ASK. The segmenter is its own module
  with table-driven tests (these are the 300 most security-sensitive lines in
  the project).
- The circuit breaker table (step 0, before everything): see the explicit table
  in requirements §4.1 (including write protection for
  `<workspace>/.foundry/` -- the counterpart to the approval persistence
  mechanism, preventing self-escalation under accept_edits).
- **A pre_tool rewrite of the input re-enters the pipeline from step 0**; the
  breaker, the rules, the approval display and the execution are all bound to
  the final input (an invariant, and in the test table).
- Approval persistence: `always` is written into the **user-layer
  configuration under `~/.foundry/`, keyed by workspace** (never into a file
  inside the workspace); the generated rule is an exact string match with no
  pattern generalisation, and takes effect at the next evaluation; model output
  cannot trigger persistence; an ASK timeout is DENY.
- The rule set is linted at startup: unknown tool names and rules that are
  necessarily dead produce warnings.

## 7. ToolExecutor and the key points of each tool

- A single entry point: `execute(call: ToolCall, ctx) -> ToolResult`; each tool
  is `validate(args) -> Invocation` (a failure is malformed and never reaches
  approval) + `run(inv)` (gemini-cli's validate-then-execute).
- Tools carry a `kind: readonly | mutator` label, which drives the default
  policy and (in V2) parallelism.
- **The workspace boundary check** (shared by the file tools): `realpath
  (strict)` + `normcase` + `commonpath` on both sides; an `lstat` per component
  to check the reparse tag; refuse ADS, device names, UNC, `\\?\`,
  drive-relative paths, and trailing dots or spaces. Its own module, with an
  attack-sample test set.
- `run_command` (winapi.py): `CreateJobObjectW + KILL_ON_JOB_CLOSE +
  AssignProcessToJobObject`; `CREATE_NEW_PROCESS_GROUP`; a graceful cancel
  sends `CTRL_BREAK_EVENT` first and then `TerminateJobObject`, with
  `taskkill /T /F` as a backstop; the millisecond window between Popen and
  Assign is listed as a known risk. Output is captured as bytes -> UTF-8 first,
  falling back to the OEM code page (whichever produces fewer U+FFFD wins); the
  subprocess env is the filtered minimal set + `PYTHONUTF8=1`.
- `apply_patch`: pure in-process Python (the patch body never passes through
  argv or a subprocess -- codex #15003); **per-file atomic semantics**
  (requirements §5.3): after everything is parsed and located, a file with any
  failing hunk is not touched on disk at all, while files that succeeded are
  written via temp + `os.replace`, and the per-file/per-hunk status report
  tells the model to resend only the failed files; a leniency gradient of exact
  -> CRLF/BOM -> trailing whitespace -> failure with a "nearest line" hint; 0
  or >1 anchor matches produce a structured error; the original encoding and
  EOL style are preserved; an optional post-edit `compile()` check. The patch
  format description is generated from the parser's constants into prompts/
  (one source, so it cannot drift; the application semantics are described in
  the same text).
- `git_status/git_diff`: hardened arguments + stripping `GIT_*` from the
  environment (against the fsmonitor/hooks/pager code execution surface).
- `read_artifact(artifact_id, offset?)`: artifact_id is an opaque token,
  resolved only through an in-memory index for the current session (all path
  semantics are refused), limited to this session's products; its output goes
  through the same truncation and redaction path as every other tool.
- `finish(status, summary, claims)`: the task close-out tool; the runtime
  verifies the claims and the git state per requirements §6.3 and then emits a
  Termination (D-017).

## 8. ModelBackend and AuthProvider

```python
class ModelBackend(Protocol):
    async def sample(self, messages, tools, params) -> AsyncIterator[StreamEvent]  # ends in a ModelTurn
    async def capabilities(self) -> Capabilities   # probed once per session and persisted (luban probing)

# Capabilities: parallel_tool_calls, streaming, prompt_caching, usage_fields, custom_tools, max_context
```

- Adapters: `openai_compat` (Chat Completions -- a personal API key, a local
  endpoint), `replay` (fixture-driven, mandated for tests), and **`responses`
  (mandatory for M3** -- the corporate Gateway's OpenAI-family models go
  through Responses, [D-022](decision-log.md)). `anthropic_messages` remains an
  add-on if needed (the Gateway's Claude wire protocol is unverified; it will
  be decided once observed). Before the ModelBackend protocol is frozen in M0,
  a paper walkthrough is done against the Responses wire protocol (an
  IR<->Responses mapping table) to keep the freeze promise.
- A protocol invariant: the provider's native continuation identifiers and
  tool-call ids stay **opaque**; local ids are created separately for the
  journal, and the originals are never interpreted or renumbered.
- The backend configuration table's fields follow codex's `ModelProviderInfo`
  (requirements §3.2); each model profile additionally carries `edit_format:
  anchored_patch | whole_file`, `tool_schema_dialect`, and
  `system_prompt_variant`.
- Streaming: line-by-line SSE parsing (httpc.py); an idle timeout; a dropped
  stream is retried as a TransientError (up to the ceiling).
- AuthProvider / CredentialSource: `acquire(scope) -> SecretHandle`,
  `refresh()`, `invalidate()`, `logout()`; **a SecretHandle is not a printable
  string**, only httpc's header injection point can resolve it, and the
  ContextManager and tool layers never hold a reference (architectural
  isolation, absorbed from the Codex version); DPAPI encrypt/decrypt
  (winapi.py), where a failed decrypt means not logged in. The corporate path's
  concrete acquisition mechanism (an HTTP exchange / an internal executable /
  browser SSO) is one implementation of that interface, pending OQ-6.

## 9. SessionStore and the termination state machine

- The line envelope is `{ts, ordinal, type, v, payload}`; the type set is
  `session_meta / model_request / model_response / tool_call / tool_result /
  policy_decision / approval / command_exec / token_usage / capability_probe /
  validation_claim / git_baseline / termination`. Artifact contents are stored
  content-addressed at `sessions/<id>/artifacts/<sha256>`, and events reference
  only the digest.
- Writes are serialised; termination, approval and command-completion events
  flush immediately; the reader tolerates a truncated final line, and no
  termination event means `interrupted`. Schema migration happens only on the
  read side; historical journals are immutable.
- A single write choke point function: exact-match replacement of
  self-held credentials (100% acceptable) + best-effort pattern scanning;
  command output is stored as raw bytes (base64).
- `model_request` is complete enough to rebuild (**except the
  auth/Authorization header** -- never written to disk, and re-injected by the
  HTTP layer on replay), so ReplayBackend uses the session file directly as a
  fixture; resume (V2) is the same replay mechanism. **The replay matching
  contract**: replay by ordinal + structural assertions (the tool call
  sequence, key fields), with a full-text request diff emitted as a test
  artifact for human review; byte-for-byte identity is a separate
  resume-ready test (otherwise a prompt tweak turns every fixture red, or blind
  replay verifies nothing at all). Fixtures are re-recorded with `foundry
  record` (against a local endpoint, delivered in M0).
- The termination state machine: `completed` requires (a) an automatic git
  check by the runtime, (b) every `validation_claim` cross-verified against
  `command_exec` events (the event_id exists and the exit code matches), and
  (c) HEAD not having moved; otherwise it is downgraded to `partial`.
  Read-only tasks take the `completed(no_changes)` path.
- `~/.foundry/audit.jsonl` is appended to separately (and is not writable by
  the tools).

## 10. Test strategy (from day one)

1. **Unit**: the policy pipeline as a (rules, mode, call) -> decision table;
   the command segmenter attack sample table; the path boundary attack sample
   table; the patch application gradient table; the encoding fallback table.
2. **Regression**: golden transcripts + ReplayBackend running the whole loop
   (no network, no credentials); any prompt or loop change must pass the full
   set. Fixtures come from `foundry record`, and **production sessions are not
   turned into test fixtures by default** (they contain user code).
3. **Adapter contract tests**: run the protocol layer against a local fake HTTP
   server (including rate limiting, dropped streams, malformed events and
   continuation identifiers), with no dependency on the real Gateway.
4. **Negative assertions** (absorbed from the Codex version): not only assert
   "the expected output appeared", but also assert "the forbidden event never
   happened" -- a refused call has no execution event, the canary credential is
   in no sink, destructive git commands were called zero times.
5. **E2E smoke**: a local OpenAI-compatible endpoint (free) -> a personal API
   key (sparingly) -> the corporate Gateway (when available).
4. **A failure telemetry loop** (the aider practice): an offline report script
   reads the session JSONL for patch first-try success rate, failure
   categories, and rounds/tokens per task -- decisions like "add a repo map /
   turn on fuzzy matching / downgrade some backend to whole-file" are then
   driven by numbers.

## 11. Dependency decision table

| Area | Choice | Rejected, and why |
|---|---|---|
| HTTP/SSE | stdlib `http.client` + `ssl` (~300 lines of our own) | httpx (7 wheels) / requests (5): certifi does not trust the Windows certificate store, so a corporate MITM proxy is guaranteed to break; the stdlib trusts the system store by default = zero configuration |
| Credentials | DPAPI via ctypes | keyring: 6 wheels + a 2560-byte backend ceiling |
| Process trees | Job Objects via ctypes | psutil: a C extension wheel + the PID snapshot approach loses orphans |
| Rendering | rich (4 wheels) | textual: ~9 wheels + an app framework that is far too heavy |
| Approval input | prompt_toolkit (optional, 2 wheels) | — |
| Validation | dataclasses + handwritten | pydantic: a platform binary wheel |
| Build | hatchling (build machine only) | setuptools brings nothing extra |

## 12. Known accepted risks (threat model appendix)

TOCTOU (replacement with a junction after the check); hard links are not
detectable by a path check; the millisecond window between Popen and the Job
assignment; DPAPI stops working after a forced password change on a local
account (treated as not logged in); decoding a mixed-encoding stream is
best-effort (with the raw bytes as a backstop); NTLM/Kerberos proxies are
unsupported (a 407 is reported explicitly); a managed DENY does not stop a
local administrator from tampering with the installation (that boundary is on
the Gateway server).
