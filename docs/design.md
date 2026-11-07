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
│   ├── tools/               # ToolExecutor + the tool implementations + the workspace boundary
│   ├── mcp.py               # the MCP client over stdio and HTTP: servers' tools as policy-checked tools (D-043, D-081)
│   ├── config_edit.py       # `foundry mcp add/remove`: a server's table in the user's config.toml, checked (D-082)
│   ├── images.py            # images the model is shown: checked, sized, base64 (D-045)
│   ├── backends/            # the ModelBackend protocol + adapters (openai_compat / responses / replay)
│   ├── auth.py              # AuthProvider + DPAPI storage
│   ├── session.py           # SessionStore (JSONL) + the audit log
│   ├── httpc.py             # a stdlib HTTP/SSE client (~300 lines)
│   └── winapi.py            # ctypes: DPAPI, Job Objects, reparse tags
├── cli/                     # the terminal UI: event subscriber + approval UI (rich); repl.py, the
│                            #   interactive prompt and its slash commands (D-039); a paste or a
│                            #   block between lines of three quotes is one message (D-052);
│                            #   markdown.py, the model's answer as Markdown a line at a time (D-070)
├── prompts/                 # versioned assets: the base system prompt, the patch format description, the permissions section template
└── __main__.py              # entry point: bootstraps in UTF-8 mode
```

## 2. The intermediate representation (IR) -- the foundation under everything

The precondition for two backends sharing one loop. **Frozen first; an adapter
does nothing but convert between the IR and the wire protocol.**

```python
# conversation.py (illustrative; all frozen dataclasses, no bare dicts)
class ContentBlock:      # aligned with the MCP shapes: text | tool_use | tool_result | image (D-045)
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
Event = SessionStarted | TurnStarted | MessageDelta | Reasoning | ToolBegin \
      | ToolOutputDelta | ToolEnd | ToolRejected | ApprovalRequest | TokenCount \
      | TurnComplete | Termination | Error | Notice | PlanUpdated | Compacted
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
- On a terminal the model's text is rendered as Markdown a line at a time
  ([D-070](decision-log.md)), each whole line stripped of control sequences
  and printed as `Text`; elsewhere it is printed as the model wrote it.
- A task's end is followed by `worked for 1m 12s` (under a second, nothing):
  from its first `TurnStarted` to its `TurnComplete` or `Termination`
  ([D-073](decision-log.md)).
- On a terminal the renderer keeps a transient `working (12s, Ctrl+C to stop)`
  line up from `TurnStarted` or `ToolBegin` until the next thing it draws or
  asks ([D-069](decision-log.md)); the REPL and `_close` take it down however
  a task or the session ends.
- Every string an event carries passes the redactor on its way out, nested
  ones included (a plan's steps sit inside a tuple).
- Under a tool's line: nothing when one of Foundry's own read-only tools
  worked (`ok` with `-v`), `ok` for any other, and `failed` with the reason
  for any that failed; a patch refused in part names what it refused
  ([D-076](decision-log.md)).
- `ToolEnd{call_id, tool, ok, summary, duration_ms, output}`: for
  `run_command`, `summary` is the exit code and `output` the last lines the
  command printed (at most 20 and 4,000 characters), shown under it -- 5 lines,
  all with `-v` ([D-068](decision-log.md)); for `apply_patch`, what landed:
  each file as `updated src/app.py (+2 -1)` and its unified diff (at most 400
  lines, no line cut), shown under it -- 40 lines, all with `-v`
  ([D-075](decision-log.md)); empty for other tools.
- `ToolOutputDelta{call_id, text, stream}` ([D-074](decision-log.md)): the
  newest complete line a running command printed -- on whichever pipe ended a
  line last -- at most every half second and only when it changed; on a
  terminal it follows the working line. Only whole lines, so each is scrubbed
  whole; the full output still comes with `ToolEnd`.
- `PlanUpdated{call_id, steps: [{step, status}], explanation}`
  ([D-037](decision-log.md)): the whole plan after each `update_plan`; an empty
  `call_id` means a resume restored it.
- `SessionStarted{session_id, workspace, model, resumed}`
  ([D-060](decision-log.md)): `exec`'s first event, so a script knows what
  `exec --resume` takes.
- `Reasoning{text}` ([D-044](decision-log.md)): a finished stretch of the
  model's reasoning, before the answer it led to; whole, so it is scrubbed as
  one string. `runtime.show_reasoning = false` stops it; the journal keeps it
  on the `model_response` either way.

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
- The error taxonomy (`TransientError/AuthError/FatalError`) is
  mapped by the adapter and handled uniformly in the runtime for retries
  (exponential backoff + Retry-After + a ceiling); an AuthError triggers a
  single-flight refresh (a process lock plus a file lock against multi-instance
  races) and one retry. A policy denial is not an error: the call is answered
  with a tool result the model reads, and a `tool_rejected` event records it.
- The cancellation propagation chain belongs to the runtime: Ctrl+C ->
  Interrupt op -> cancel the in-flight HTTP read -> TerminateJobObject kills
  the child process tree -> `Termination(cancelled)` is written to disk.
  At the interactive prompt it ends the task, not the session
  ([D-062](decision-log.md)): `AgentRuntime.interrupted()` answers each call
  of the model's last message still without a result -- the running one as
  stopped, with what it changed unknown (a mutator other than `run_command`
  counts as a change), the rest as not run -- and the prompt comes back. An
  abort at an approval ends its task the same way. At the prompt one Ctrl+C
  only says how to end the session; two in a row end it
  ([D-077](decision-log.md)).
- An asyncio core; blocking tool bodies run in `asyncio.to_thread`.

## 5. ContextManager

- `project(session) -> list[Message]`: **the transcript is not the model
  context**, and the projection is an explicit function (the openhands event
  stream idea), which guarantees that replaying the same session produces a
  byte-identical request.
- Assembly order (codex layering): `[base system prompt] + [a permissions
  section generated from the policy configuration -- telling the model the real
  auto-allow/approval boundary] + [FOUNDRY.md/AGENTS.md: the user's own from
  the Foundry folder, then the repository's from its top down to the working
  directory (trust-gated, sharing a byte ceiling; D-019, delivered in M2,
  layered in [D-041](decision-log.md))] + [environment context: OS/shell/cwd/
  git status] + [conversation history]`.
- The output budget: every tool output has a byte ceiling, and over it is
  truncated head+tail with a `[truncated: N bytes, artifact_id=...]` marker,
  with the full output written into the session directory for `read_artifact`
  to retrieve.
- **Observation masking** (V1's "compaction"): once the projected context would
  pass half the backend's window, tool output is projected as a one-line stub
  (`[output elided; re-run tool if needed]`), oldest first and only until it
  fits again; the system prompt, the task, and the last N rounds (5 by default)
  are always kept in full. Below that threshold nothing is masked
  ([D-031](decision-log.md)). The evidence: equivalent to LLM summarisation with
  zero extra dependencies (arXiv 2508.21433; SWE-agent +3pt).
- **Thinking and the prefix** ([D-048](decision-log.md)): a masked result, a
  rebuilt permissions paragraph or a resume's new system prompt changes what
  Anthropic bound each earlier thinking block to. The context compares each
  projection with the last request sent and, from the first difference on,
  leaves those blocks out of this and every later request (text and tool
  calls stay); blocks before the difference, and replies made after it, are
  sent. The history itself keeps every reply as received.
- **Compaction by summary** ([D-040](decision-log.md)): masking cannot touch
  the last rounds, so once the projection is still past 80% of the window, the
  runtime asks the model for a handoff summary and the history becomes the
  user's own messages (newest first within 10% of the window) plus the summary
  (at most 15%, with the plan appended). `/compact [focus]` does it on request;
  `runtime.auto_compact = false` turns the automatic one off. It enters the log
  as a `compacted` record (openhands condensation-as-event); a failure changes
  nothing and is not retried until the next task, and files read before it
  are read again before a patch.
- Token accounting: accumulate the real usage, and as the backend's context
  ceiling approaches, terminate cleanly as `partial(context_exhausted)` --
  never let the request 400.

## 6. PolicyEngine

```python
# the six-step pipeline (the claude-code Agent SDK's published specification, testable step by step)
def evaluate(call) -> Decision:            # Decision = ALLOW | ASK(reason) | DENY(reason)
    1. the pre_tool callback (a configured Python callable, or a stdin-JSON subprocess; may rewrite the input)
    2. DENY rules (merged across every layer; a hit ends it -- no ALLOW can flip it);
       then plan mode and read-only tasks (/review) refuse every mutator, before any ASK (D-039)
       (a rule's tool may be a pattern, such as mcp__docs__*, matched case-sensitively; D-043)
    3. ASK rules
    4. the mode baseline (default/accept_edits/plan/dont_ask)
    5. ALLOW rules
    6. interactive approval (headless: DENY)
```

- A read-only task sets `PolicyEngine.read_only` for its turn (`/review`,
  [D-039](decision-log.md)): every mutator is refused at step 2 with that
  reason, whatever the mode, and the field is cleared when the turn ends.
  `/review --base REV` and `/review --commit REV` ([D-066](decision-log.md))
  review a branch or one commit the same way.
- The mode changes only when the user says so at the session's prompt:
  `/approve` in plan mode ([D-038](decision-log.md)) switches it to `default`
  or `accept_edits`, and `/mode NAME` ([D-047](decision-log.md)) to any of the
  four. Either builds the system prompt's permissions paragraph again, writes
  `mode_change`, and tells the model with the next message. Each
  `policy_decision` records the `mode` it was made in. A resume starts in the
  configured mode; `/new` keeps what `/mode` chose.
- `/model NAME` ([D-047](decision-log.md)) sends the following requests to
  another model behind the same endpoint, writes `model_change`, and tells the
  model with the next message; the system prompt is not rebuilt, so the
  prefix the provider binds thinking blocks to is unchanged. `/model` alone
  ([D-067](decision-log.md)) lists the endpoint's models: each HTTP backend's
  `list_models()` asks `GET /models` with its own headers, once, within 15
  seconds and a megabyte, and the ids are shown, never sent or journaled.
- `/effort LEVEL` ([D-061](decision-log.md)) changes `backend.effort` for the
  following requests -- `output_config.effort`, `reasoning.effort` or
  `reasoning_effort`, as the protocol spells it -- and writes `effort_change`;
  the model is not told. Each protocol's levels are checked when the
  configuration loads and when the command is typed; `default` sends none.
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
- Programs Foundry starts ([D-049](decision-log.md)) -- git, PowerShell for
  commands, `taskkill`, MCP servers -- are started by an absolute path that
  `winapi.find_program` found on PATH. Windows otherwise looks in the current
  directory first, and that is usually the repository.
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
  subprocess env is the filtered minimal set + `PYTHONUTF8=1`, changed by the
  user's `[shell_environment_policy]` ([D-083](decision-log.md)): `inherit`
  (core, all, none), then `include`, `exclude`, `set` and `include_only`; a
  credential-shaped name only by its exact name in `include`, its value then
  registered with the redactor. `!COMMAND` gets the same.
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
  `git_diff` shows what is not committed, or with `base` what this branch
  changed since it left a branch or commit (from their merge base, uncommitted
  work included), or with `commit` one commit's message and patch
  ([D-066](decision-log.md)); a revision is resolved to a full commit id
  first, and one that starts with `-` never reaches git.
- `read_artifact(artifact_id, offset?)`: artifact_id is an opaque token,
  resolved only through an in-memory index for the current session (all path
  semantics are refused), limited to this session's products; its output goes
  through the same truncation and redaction path as every other tool.
- `finish(status, summary, claims)`: the task close-out tool; the runtime
  verifies the claims and the git state per requirements §6.3 and then emits a
  Termination (D-017). Under `exec --output-schema` ([D-051](decision-log.md))
  it also takes `result`, required with `completed` and checked against the
  schema by `core/json_schema.py` (a subset of JSON Schema; a schema using
  more is refused before the run); the result is carried by the termination
  record and event and is what `exec` prints.
- `update_plan(explanation?, plan)` ([D-037](decision-log.md)): replaces the
  session's plan (at most 20 one-line steps, at most one `in_progress`; empty
  clears it). Read-only and always allowed; the runtime keeps the plan, emits
  `PlanUpdated`, and records it in the call's `tool_result` (`plan`), from
  which a resume restores it. `finish` lists the steps not marked completed and
  decides nothing over them.
- MCP tools ([D-043](decision-log.md)): each `[mcp_servers.<name>]` in the
  user's own config is started at session start under a job object, with the
  command environment plus its `env` and `env_vars`; its tools are registered
  as `mcp__<server>__<tool>`, mutators to the policy unless the entry says
  `read_only = true`. A call is `tools/call` with a timeout and the cancel
  flag; the result's text is truncated like a command's. A failing server is a
  warning and a `capability_probe` record, never a failed session. `/mcp`
  ([D-064](decision-log.md)) lists the servers, how each is, and their tools,
  showing a server's program but not its arguments. An entry's
  `enabled_tools` and `disabled_tools` ([D-080](decision-log.md)) leave the
  other tools unregistered, said at start, in `/mcp` and in the
  `capability_probe` record (`left_out`); a name the server does not list is
  a warning. An entry with `url` instead of `command`
  ([D-081](decision-log.md)) is a server reached over streamable HTTP: a POST
  per message, JSON or an event stream back, the session id and protocol
  version on every message after initialize and DELETE at close; a bearer
  token from the variable `bearer_token_env_var` names, sent only over https
  or to this machine and registered with the redactor; each request on a
  thread of its own, so Ctrl+C and the timeout cut its connection. `/mcp`
  shows such a server's scheme and host, not its path. `foundry mcp list | add
  | remove` ([D-082](decision-log.md)) manage the tables from the command
  line: an edit is text, appended or taken out, and written only if the
  result parses to the old settings with exactly that server added or gone.
- `notify` ([D-050](decision-log.md)): a program from the user's own config,
  started when a turn ends with the turn's JSON as its last argument (codex's
  `agent-turn-complete`), found on PATH, without a shell, with the command
  environment and its output discarded; nothing waits for it. Not when the
  user stopped the turn; a batch file is refused, since cmd.exe would read the
  model's words in the payload as commands. It is also started, before the
  question, when an approval waits for the user (`approval-requested`: the
  tool, the call's display and the reason; [D-079](decision-log.md)); each
  `notified` record names its `type`.
- `view_image` ([D-045](decision-log.md)): a read-only, workspace-bound tool
  that returns the image on its result (`ToolOutput.images`). An image is
  checked by `core/images.py` from its own bytes -- PNG, JPEG, GIF or WebP, at
  most 3.75 MB, no side over 8000 pixels -- and is never resized. Not offered
  when `backend.images = false`. `/image` with no path
  ([D-065](decision-log.md)) takes the clipboard's image instead of a file's,
  read by a fixed PowerShell script that writes it as PNG in base64.
- `repo_map(path?)` ([D-046](decision-log.md)): an outline of Python code from
  `ast` -- classes, fields, methods, signatures, constants, with the line each
  starts on. One file gets its whole outline; a directory gets a brief one,
  files ranked by how many others import them (tests last) and fitted to about
  3,000 tokens: outlines, then a line of names, then paths, so nothing left
  out goes unnamed. Read-only; parsing runs nothing; cached by mtime and size.

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
  through Responses, [D-022](decision-log.md)), and `anthropic_messages` (the
  native Messages API with prompt caching and verbatim replay of thinking,
  [D-032](decision-log.md); the corporate Gateway's Claude wire protocol is
  still unobserved). Before the ModelBackend protocol is frozen in M0,
  a paper walkthrough is done against the Responses wire protocol (an
  IR<->Responses mapping table) to keep the freeze promise.
- A protocol invariant: the provider's native continuation identifiers and
  tool-call ids stay **opaque**; local ids are created separately for the
  journal, and the originals are never interpreted or renumbered.
- The backend configuration table's fields follow codex's `ModelProviderInfo`
  (requirements §3.2); each model profile additionally carries `edit_format:
  anchored_patch | whole_file`, `tool_schema_dialect`, and
  `system_prompt_variant`. `env_key`, `env_http_headers` and `query_params`
  ([D-084](decision-log.md)) are codex's: an `EnvKeySource` reads the key from
  the named variable only; header values are read from variables when the
  session starts and registered with the redactor; `with_query` puts the
  parameters on every endpoint and on the model list.
- Streaming: line-by-line SSE parsing (httpc.py); an idle timeout; a dropped
  stream is retried as a TransientError (up to the ceiling). The one GET,
  `get_json`, is for the model list (D-067): a timeout of its own for the
  connection and every read, a cap on the body, no retries.
- Reasoning ([D-044](decision-log.md)): each adapter yields
  `ReasoningComplete(text)` when a stretch of reasoning is complete -- an
  Anthropic thinking block (asked for with `display: "summarized"`), a
  Responses reasoning item's summary (asked for only when
  `backend.reasoning_summary` is set), or the `reasoning_content` a Chat
  Completions server streams. It is never sent back.
- Images ([D-045](decision-log.md)): Anthropic takes an `ImageBlock` in the
  user message and inside a `tool_result`; Chat Completions and Responses take
  a tool's output as text only, so the images a run of results carried follow
  it as one user message. The context counts an image as 1,600 tokens.
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
  validation_claim / git_baseline / termination / user_input / tool_reply /
  resumed / undo / mode_change / compacted / model_change / notified /
  user_command / rewound / effort_change` (`user_input`, `tool_reply` and
  `resumed` added for resume, [D-035](decision-log.md); `undo` for undo,
  [D-036](decision-log.md); `mode_change` for plan approval,
  [D-038](decision-log.md); `compacted` for compaction, whose own request and
  response carry `purpose: "compact"`, [D-040](decision-log.md);
  `model_change` for `/model`, [D-047](decision-log.md); `notified` for the
  notify program, [D-050](decision-log.md); `user_command` for `!COMMAND`,
  [D-055](decision-log.md); `rewound` for `/rewind`, which keeps the history
  it left as `compacted` does, [D-056](decision-log.md); `effort_change` for
  `/effort`, [D-061](decision-log.md)). An image
  is journaled by the id of the artifact that holds its bytes, written once
  however many requests repeat it ([D-045](decision-log.md)).
  Artifact contents are stored content-addressed at
  `sessions/<id>/artifacts/<sha256>`, and events reference only the digest.
- Writes are serialised and every record is flushed as it is written;
  termination, approval, command-completion, policy-decision, resumed, undo,
  mode-change, model-change, effort-change, compacted and rewound records are also synced to disk. The reader tolerates a truncated final line,
  and no termination event means `interrupted`. Schema migration happens only
  on the read side; historical journals are immutable. One process writes a
  journal at a time: the store holds an OS lock on `sessions/<id>/lock`.
- A single write choke point function: exact-match replacement of
  self-held credentials (100% acceptable) + best-effort pattern scanning;
  command output is stored as raw bytes (base64).
- `model_request` is complete enough to rebuild (**except the
  auth/Authorization header** -- never written to disk, and re-injected by the
  HTTP layer on replay), so ReplayBackend uses the session file directly as a
  fixture; resume ([D-035](decision-log.md)) rebuilds the conversation from
  the same records. **The replay matching contract**: replay by ordinal +
  structural assertions (the tool call sequence, key fields), with a full-text
  request diff emitted as a test artifact for human review; byte-for-byte
  identity is a separate resume-ready test, `tests/test_resume.py`
  (otherwise a prompt tweak turns every fixture red, or blind replay verifies
  nothing at all). Fixtures are re-recorded with `foundry record` (against a
  local endpoint, delivered in M0).
- The termination state machine: `completed` requires (a) an automatic git
  check by the runtime, (b) every `validation_claim` cross-verified against
  `command_exec` events (the event_id exists and the exit code matches), and
  (c) HEAD not having moved; otherwise it is downgraded to `partial`.
  Read-only tasks take the `completed(no_changes)` path. A journal holds one
  termination per run, plus one for each task an interactive session went on
  past -- every one, since only the user ends such a session
  ([D-063](decision-log.md)); its status is the last one's unless a later
  `resumed` record starts a run that has not ended, which reads as
  `interrupted`. Before an interactive session sends anything after a task
  that ended, `AgentRuntime.answer_left_calls()` answers the calls it left:
  `finish` as accepted with its status, the rest as not run.
- **Resume** ([D-035](decision-log.md), `core/resume.py`). The history is each
  `model_request`'s messages taken where they first appear (never masked
  there), then what followed the last request: its `model_response` (with the
  `opaque` block), the `tool_reply` records, any `user_input`. Round numbers
  come from `model_request.turn` (older journals: one more than the responses
  so far). Every tool call without a result is given one, in call order, before
  any user text: an accepted finish with its status, a call not run with the
  reason, or an outcome unknown. The CLI then refuses a moved HEAD, another
  workspace, a newer schema or a full context; reuses the original baseline
  (so the system prompt is unchanged); adds files changed outside the session
  to the dirty-file guard; starts the evidence over (no command history, no
  files read, no grants); writes `resumed`; and puts a note before the first
  task saying all of that to the model.
- **Undo** ([D-036](decision-log.md), `core/undo.py`). `apply_patch` puts
  `changes` in its `tool_result`: per file, `before` (an artifact holding the
  exact earlier bytes, or null if the file did not exist or its content held a
  known credential and was not `kept`), `before_sha`, and `after` (the digest
  written, null if removed); a move gives two linked entries. Undo groups the
  changes no `undo` record has reverted by file, newest first. For each file it
  keeps the run of changes that can be reverted: the file must hold the newest
  one's `after`, each change's earlier content must be kept, and each must
  start from what the one before it left. A move is reverted only with its
  other end. Each file is then checked again, written atomically (or removed)
  through `Workspace.resolve`, and an `undo` record lists the reverted
  `(result ordinal, path)` pairs and the skipped files with the reason. `/undo`
  also tells the model, forgets those files in the read tracker and moves the
  last-mutation mark to the undo record; a resume after an undo names the files
  in its note.
- **Plan** ([D-037](decision-log.md)). `update_plan`'s `tool_result` carries
  `plan: {explanation, steps: [{step, status}]}`; a resume takes the newest one
  in the journal, reading back only steps that still validate.
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
