# Foundry V1 architecture

**Status:** Proposed
**Companion:** [Product requirements](product-requirements.md)

## 1. Architectural drivers

The design is shaped by five constraints: a single provider-neutral agent loop,
an interactive Windows 11 trusted-host runtime, a dirty Git worktree, local
auditable evidence, and two materially different authentication environments.
The personal authentication path is not yet known to be implementable, so the
core cannot depend on it for development or tests.

## 2. Context and trust boundaries

```text
 User / terminal
       |
       v
  CLI controller ---- user config / repository config (untrusted input)
       |
       v
  AgentRuntime <------ local SessionStore
    |   |   |
    |   |   +--------> PolicyEngine --> Approval UI
    |   |
    |   +------------> ModelBackend --> external model/Gateway (untrusted)
    |
    +----------------> ToolService --> workspace + Git + child processes
                                           (trusted host, broad ambient access)
```

Task text, repository content, model output, tool arguments, Gateway responses,
and restored session data are data—not authority. Policy and executor invariants
must not be expressible as prompt instructions alone.

## 3. Package boundaries

The eventual package layout is proposed as:

```text
src/foundry/
  cli/          argument parsing, interactive rendering, approval UI
  runtime/      state machine, budgets, failure equivalence, termination
  protocol/     normalized model/tool/event value objects and schema versions
  providers/    Responses adapter, fake/replay backend, future narrow adapters
  auth/         credential-source interfaces and OS-protected persistence
  policy/       rules, precedence, normalized operation evaluation
  tools/        schemas and service orchestration
  platform/win/ path/reparse inspection, process tree, terminal handling
  git/          baseline, status/diff evidence, concurrent-change detection
  sessions/     JSONL journal, artifact store, redaction, recovery
  config/       typed config loading, provenance, validation
```

Dependencies point inward toward `protocol` and pure runtime abstractions. The
runtime depends on ports/interfaces, not concrete HTTP, console, filesystem, or
clock implementations. Platform-specific behavior is explicit rather than
hidden behind generic `pathlib` assumptions.

## 4. Core ports

These are conceptual typed contracts, not frozen Python signatures:

```python
class ModelBackend(Protocol):
    capabilities: BackendCapabilities
    def stream_turn(self, request: TurnRequest, cancel: CancelToken) \
        -> Iterator[ModelEvent]: ...

class CredentialSource(Protocol):
    def acquire(self, scope: CredentialScope) -> SecretHandle: ...
    def invalidate(self, handle: SecretHandle) -> None: ...
    def logout(self) -> None: ...

class PolicyEngine(Protocol):
    def evaluate(self, operation: NormalizedOperation,
                 context: PolicyContext) -> Decision: ...

class ToolService(Protocol):
    def prepare(self, call: ToolCall) -> NormalizedOperation: ...
    def execute(self, approved: ApprovedOperation,
                cancel: CancelToken) -> ToolResult: ...

class SessionSink(Protocol):
    def append(self, event: SessionEvent) -> EventReference: ...
```

`SecretHandle` deliberately avoids a generally printable token string. The HTTP
transport is the narrow component allowed to resolve it. Python cannot guarantee
memory erasure, so the design reduces copies and exposure rather than promising
secure zeroization.

## 5. Runtime state machine

```text
INITIALIZING
  -> READY
  -> REQUESTING_MODEL
  -> RECEIVING_MODEL
       -> PREPARING_TOOL
       -> POLICY_DECISION
            -> AWAITING_APPROVAL -> EXECUTING_TOOL -> RECORDING_RESULT
            -> EXECUTING_TOOL -> RECORDING_RESULT
            -> RECORDING_DENIAL
       -> REQUESTING_MODEL
       -> FINALIZING
  -> COMPLETED | PARTIAL | BLOCKED | FAILED | CANCELLED
```

### Turn algorithm

1. Check session budgets and cancellation.
2. Build a turn from public task context, prior normalized interaction, tool
   schemas, and bounded relevant results. Secrets and the audit journal are not
   blindly inserted.
3. Stream from `ModelBackend`, persisting redacted normalized events.
4. On a tool request: schema-validate; canonicalize into an immutable operation;
   evaluate policy; acquire operation-bound approval if needed; revalidate
   time-sensitive invariants; execute; record and return a bounded result.
5. Fingerprint failures by normalized operation/error class. Enforce repetition
   and global limits.
6. On a proposed final response, run the finalization gate rather than accepting
   the model's status label.

No provider may call a tool directly. Provider adapters cannot hold `ToolService`
or `PolicyEngine` references.

### Finalization gate

The gate gathers fresh Git status/diff evidence, checks concurrent changes,
maps validation claims to evidence IDs, records truncation or skipped checks,
and selects the terminal status. `completed` means the requested work is believed
complete and its claims are evidence-consistent; it does not mean tests exist or
that trusted-host execution was isolated.

## 6. Normalized model protocol

`TurnRequest` contains instructions, conversation items, declared tools, limits,
and optional opaque continuation data. `ModelEvent` is a closed union:

- response started / text delta / text completed;
- tool call started / arguments delta / tool call completed;
- usage update;
- response completed;
- provider error.

The adapter validates final assembled tool arguments before emitting an
executable `ToolCall`. Unknown or malformed event sequences become provider
protocol errors. Provider-native IDs remain opaque and are mapped to local IDs
for journaling. If a Gateway lacks a capability, its capability profile disables
the feature; the runtime does not simulate unsupported semantics invisibly.

The corporate Claude route must first answer whether the Gateway exposes Claude
through Responses-compatible semantics or a distinct protocol. Only the latter
requires a new model adapter. It never requires a new agent loop.

## 7. Authentication architecture

Authentication has three layers:

1. `CredentialSource` acquires or refreshes authorization.
2. `CredentialVault` persists only what the supported flow permits, preferably
   via a Windows-protected credential facility.
3. `AuthenticatedTransport` applies authorization immediately before HTTP I/O.

The corporate implementation is expected to invoke or call the internal-subnet
auth contract and receive an expiring token. Whether this is an HTTP exchange,
an approved executable, or browser/SSO flow remains discovery work. Endpoint
allowlisting and redirect handling belong in this layer.

The ChatGPT subscription path is intentionally an empty adapter until official
documentation establishes client registration, authorization grant, scopes,
token audience, refresh/logout behavior, local redirect/device flow, storage
requirements, and whether independent open-source clients are permitted. Browser
automation and private endpoint discovery are not acceptable substitutes.

## 8. Policy model

Policy operates on normalized intent, not raw model JSON. Rules are composed
from strongest to weakest:

1. compiled safety invariants;
2. optional managed policy (future; may only restrict);
3. user policy;
4. repository policy (may only restrict its parents);
5. session approvals bound to one operation.

`DENY` dominates `ASK`, which dominates `ALLOW`. A decision records rule ID,
policy version/digest, operation digest, reason, and time. Approval cannot turn a
fixed deny into an allow. Initial command classification should be conservative:
parser-based recognition may reduce prompts later, but blacklists cannot make an
arbitrary PowerShell string safe.

## 9. Workspace and Windows path safety

Lexical prefix checks are insufficient on Windows. The file boundary service
must account for:

- drive-relative (`C:foo`) versus drive-absolute paths;
- UNC and device namespaces (`\\server`, `\\?\`, `\\.\`);
- case-insensitive comparison and trailing dot/space normalization;
- reserved device names and alternate data streams (`name:stream`);
- symlinks, junctions, mount points, and other reparse points;
- time-of-check/time-of-use replacement of an ancestor;
- Unicode and long paths.

V1 rejects all reparse points in workspace paths rather than trying to classify
safe targets. The implementation should traverse existing components using
Windows handle-based inspection, validate the final handle location/identity,
and use exclusive/atomic replacement where possible. Merely calling
`Path.resolve()` and comparing strings is not an adequate design.

Workspace file tools obey this boundary. Commands do not: on a trusted host, a
compiler or PowerShell script can access anything the user can. The CLI must not
conflate command cwd with confinement.

## 10. Patching and concurrent changes

At session start, `GitBaseline` captures HEAD, index/worktree status, staged and
unstaged evidence, and metadata/digests for relevant untracked files. Before a
read-derived write, the patch operation includes the last observed content
digest. Immediately before replacement the executor checks it again.

Patch application stages content in the same directory, flushes it as required,
then performs an atomic replacement supported by Windows. The result records old
and new digests. If content changed between observation and replacement, the
operation fails as stale and returns fresh context to the runtime. Foundry never
uses `git reset`, `checkout`, `clean`, or stash as a safety mechanism.

This protects file versions, not semantic line ownership. If Foundry intentionally
edits a file already dirty at baseline, the final report says so explicitly.

## 11. Command execution

The executor launches a new process group/job object, with:

- a validated workspace cwd;
- an allowlisted/inherited environment policy that excludes known secrets where
  practical (while acknowledging children may access ambient user resources);
- separate stdout/stderr streaming decoders;
- byte and time caps;
- terminal control-sequence neutralization for display;
- cancellation and timeout that terminate the entire assigned process tree;
- final exit code, duration, truncation flags, and output artifact references.

PowerShell is the initial interactive shell because it is available in the
target environment. Direct executable + argument invocation is preferred for
Foundry-owned Git operations. The exact PowerShell edition/path and encoding
behavior require a Windows spike.

## 12. Session and artifact model

Directory sketch:

```text
<user-data>/foundry/
  sessions/<session-id>/events.jsonl
  sessions/<session-id>/artifacts/<sha256>
  config.toml
```

Each event envelope includes `schema_version`, `session_id`, `sequence`,
`event_id`, UTC timestamp, event type, public payload, and correlation IDs. Event
payloads reference previous evidence; they do not mutate it. Sensitive fields
are structurally omitted, then free text passes through conservative redaction.
Artifacts are content-addressed, permission-restricted, bounded, and never
retrievable by arbitrary path through the model tool.

The writer serializes appends and flushes terminal/approval/command-completion
events. Recovery ignores a malformed trailing record, verifies sequence/digests
where available, and marks a nonterminal session interrupted. Schema migration
is read-side; historical journals remain immutable.

## 13. Configuration model

Configuration is typed and records provenance for every effective value.
Suggested sections are runtime budgets, provider profile, auth source name,
policy, command environment, storage/retention, and rendering. Repository config
is treated as untrusted content, cannot name a credential value, cannot weaken
policy, and should require explicit trust before first use.

Provider profiles contain endpoint, model ID, protocol, non-secret headers, TLS
verification, proxy, connect/read timeout, retry policy, and capability overrides.
TLS/proxy fields should exist in the abstraction even if advanced enterprise
variants are not acceptance requirements.

## 14. Testing architecture

Most tests use a scripted backend whose event stream, delays, errors, and usage
are deterministic. A replay backend reproduces redacted protocol fixtures but
must not turn production sessions containing user code into test fixtures by
default.

Test layers:

1. pure property/unit tests for schemas, policy lattice, budgets, redaction, and
   state transitions;
2. temporary-repository contract tests for tools and dirty baselines;
3. Windows integration tests for paths, reparse points, job objects, signals,
   encoding, and atomic replacement;
4. provider contract tests against a fake HTTP server;
5. opt-in live Gateway smoke tests with canary credentials and sanitized logs;
6. offline wheelhouse installation tests on a clean Windows runner/VM.

Tests assert prohibited events never occur, not just that expected text appears.

## 15. Failure taxonomy

Stable error categories prevent adapters from leaking secrets and let the loop
make bounded decisions: configuration, authentication-required, authorization,
rate-limit, transient transport, timeout, provider-protocol, invalid-tool-call,
policy-denied, approval-declined/expired, stale-file, tool-failed, budget-exceeded,
cancelled, and internal-error.

Only explicitly transient categories are candidates for retry. A policy denial
is a result the model may work around only by choosing a materially different,
permitted approach—not by repeating or rewriting the same request.

## 16. Deferred design

Public Python API stability, unattended approval, concurrent tools, session
resume semantics, managed enterprise policy, Claude-specific adapter, remote
telemetry, semantic indexing, non-Windows platforms, and sandboxing are deferred.
Interfaces should avoid gratuitously blocking them, but V1 must not implement
speculative frameworks for all of them.
