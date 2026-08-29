# Foundry delivery roadmap

This roadmap uses evidence gates rather than calendar promises. A phase ends
when its exit criteria are met; uncertain external integrations are isolated so
they do not force unsafe shortcuts into the core.

## Phase 0 — Blueprint and discovery (current)

### Deliverables

- reviewed product requirements, architecture, threat model outline, and ADRs;
- license decision and contribution/provenance policy;
- official-source review of Codex and other agents focused on interfaces and
  failure lessons, with licenses recorded;
- ChatGPT subscription-auth feasibility report;
- corporate auth and Gateway protocol/capability report;
- Windows path, process-tree, credential-store, PowerShell, and wheel spikes;
- prioritized risks and measurable V1 acceptance suite.

### Exit criteria

- Every external integration has an owner, evidence source, and fallback.
- ChatGPT auth is classified `supported`, `blocked`, or `not in V1`; no “probably
  works” state is accepted.
- A captured and sanitized corporate Responses stream proves actual tool-call and
  streaming semantics, not merely conversational text.
- Windows spikes demonstrate reparse rejection and process-tree cancellation on
  the actual target environment.

## Phase 1 — Offline vertical skeleton

Build packaging, typed configuration, event schema/journal, deterministic fake
backend, policy lattice, CLI shell, budgets, cancellation token, and a runtime
that can complete a scripted no-tool/read-only scenario.

### Exit criteria

- Unit tests run without network or credentials.
- Every scripted scenario ends exactly once and produces a recoverable journal.
- A wheel builds and installs in a clean Python 3.12 environment.

## Phase 2 — Safe workspace and Git tools

Implement Windows boundary service, baseline/concurrency guard, bounded file
tools, patching, artifact store, and machine-oriented Git status/diff.

### Exit criteria

- Adversarial Windows path and reparse tests pass on Windows 11.
- Dirty-worktree matrix covers staged, unstaged, untracked, rename, deletion,
  non-UTF-8/binary, concurrent edits, and already-dirty edited files.
- No test uses destructive Git cleanup to restore fixtures.

## Phase 3 — Commands, approval, and evidence finalization

Implement PowerShell/direct process execution, job-object cancellation, streaming
capture/redaction, operation-bound interactive approvals, validation evidence,
and the finalization gate.

### Exit criteria

- Timeout/Ctrl+C kills descendant processes and records `cancelled` or failure.
- Oversized/binary/Unicode/control-sequence outputs are safely bounded.
- Completion cannot be produced from an unexecuted or failed validation claim.

## Phase 4 — Corporate Gateway integration

Implement the discovered credential source, secret handle/vault, authenticated
transport, Responses adapter, error normalization, streaming, retry, and live
contract tests.

### Exit criteria

- A real controlled coding task completes through the Gateway.
- Token canaries do not appear in console, prompt capture, journal, artifacts,
  exceptions, or diagnostic export.
- Expiry, reacquisition, rate limit, dropped stream, malformed call, and timeout
  paths have bounded tests.
- Claude support is separately accepted or explicitly deferred based on observed
  Gateway protocol—not marketing model availability.

## Phase 5 — Personal authentication decision/integration

If and only if Phase 0 finds an official supported independent-client flow,
implement it behind `CredentialSource`, including browser handoff, state/PKCE or
device-flow protections as specified, protected storage, refresh, and logout.

If blocked, do not substitute scraping, copied credentials, or private APIs.
Instead choose explicitly among: release Gateway-only, keep the project at a
developer preview using fake/replay tests, or adopt a separately approved public
API route later.

## Phase 6 — V1 hardening and release

Run the full Windows suite, offline supply-chain/install rehearsal, documentation
review, security review, journal compatibility tests, and end-to-end acceptance.

### Exit criteria

- All product acceptance criteria pass with evidence attached to the release.
- Trusted-host limitations and data retention are prominent.
- Locked dependencies and hashes recreate the complete offline wheelhouse.
- No unresolved critical risk is relabeled as documentation-only.

## Not on the critical path

TUI/GUI, stable SDK, CI mode, multi-agent execution, MCP/plugins, semantic index,
non-Windows support, sandboxing, automatic Git publication, and corporate Claude
adapter work remain outside the V1 critical path unless the product requirements
are explicitly revised.
