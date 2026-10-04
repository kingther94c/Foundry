# Codex blueprint comparison

> Subject: `origin/codex/create-branch-codex-blueprint` (PR #1, 2026-08-29,
> kept as an archive and not merged).
> Conclusion: this blueprint stays the trunk, absorbing the points below
> (confirmed by the user on 2026-08-29).

## Converged independently (mutually confirming, no action needed)

A single runtime owns the loop and adapters may not overstep | honest
disclosure of a trusted host, approval is not a sandbox | a versioned JSONL
event stream + five termination states | dirty working-tree support + a
baseline + optimistic concurrency protection on writes | a declarative
capability backend | fake/replay backends for offline testing | the workspace
boundary must defend against Windows reparse points, device names and ADS |
no automatic commit or push, no destructive git.

## Absorbed (already written into the trunk documents)

1. **A canary credential leak suite**: a canary token used in tests, asserted
   to appear in neither the console, the prompt, the journal, artifacts,
   exceptions, nor diagnostics (-> requirements §8, roadmap M3).
2. **Crash-recovery semantics**: a truncated JSONL record is tolerable; a
   session with no termination event is `interrupted` and is never judged
   completed (-> §6.1, M0).
3. **Content-addressed artifacts**: `sessions/<id>/artifacts/<sha256>`, with
   the event recording the digest, size and truncation state (-> §6.1, design
   §9).
4. **Failure fingerprints**: count repeated failures by "normalised operation +
   error class", so a change of wording does not reset the counter (-> §5.1).
5. **Approval binding + expiry**: an approval binds to the exact operation plus
   cwd plus the env policy plus a validity window; any change invalidates it
   (-> §4.1).
6. **Policy decision audit fields**: record the rule ID, the policy
   version/digest, and an operation digest (-> design §6).
7. **Configuration**: add an env layer (`FOUNDRY_*`); repository config and
   ordinary CLI arguments must not carry secrets (-> §4.3).
8. **File tool paths are workspace-relative**: absolute and device paths are
   refused, so an escape is not expressible (-> §5.4; this replaces Claude
   Code's absolute-path convention, and is safer under a single-root
   workspace).
9. **Completion disclosure**: claims may be empty, but then it must explicitly
   state "no verification was performed" -- disclosure is valid, invention is
   not (-> §6.3).
10. **Testing**: adapter contract tests against a fake HTTP server;
    negative assertions of the "assert this forbidden event never happened"
    form; production sessions are not turned into test fixtures by default
    (-> design §10).
11. **The corporate path contract**: CredentialSource = acquire / expiry /
    refresh / logout, with a pluggable mechanism; SecretHandle naming (a
    credential never circulates as a printable string) (-> §3.2, design §8).
12. **The M3 entry gate**: obtain the Gateway's tool-call streaming redaction
    fixtures before implementing anything ("Responses-compatible may cover
    conversation without covering tool continuation" is a real risk) (->
    roadmap M3).
13. Details: the startup banner shows the limits and policy in effect; the
    branch name is displayed prominently but carries no security meaning;
    config values record provenance (which layer they came from); retention is
    user-controllable.

## New intelligence (confirmed by the user, from their answers to Codex)

- The corporate Gateway: OpenAI models go through the **Responses API**; the
  token comes from an **intranet auth flow** (an HTTP exchange / an internal
  executable / browser SSO -- still to be confirmed); Claude models exist but
  their wire protocol is unverified -> OQ-6 partially closed; the `responses`
  adapter is promoted back to mandatory for M3.
- Windows: symlinks cannot be created, and there is no Developer Mode;
  proxy / custom CA / mTLS are not V1 requirements (the capability is kept,
  because it costs nothing).
- Open-source intent -> the user ruled **not for now** (D-021), license
  deferred.

## Disagreements resolved

| Disagreement | The Codex version | Ruling |
|---|---|---|
| apply_patch default | allowed by default inside the workspace | keep interactive approval as the default, switchable via accept_edits (D-023) |
| ChatGPT authentication | a research spike to come (its environment had no external network, and the document says so rather than claiming a fact) | our hard blocked evidence + the API key decision (D-009) supersedes its Phase 5 / Q-006 / R-001 outright |
| Open source + Apache-2.0 | intent to open source | not for now (D-021) |

## Ours alone, missing from theirs (unchanged)

Hard evidence on authentication feasibility | an empirically chosen edit format
(anchored search/replace + a leniency gradient) | the mechanical detail of the
six-step policy pipeline (mode baselines, where built-in rules sit, where
persistence goes) | ContextManager / masking / token accounting | the finish
tool as the channel that produces claims | settled decisions on the shell, the
dependency budget, golden fixtures and more | the event-bus / asynchronous
approval design that decouples the UI.
