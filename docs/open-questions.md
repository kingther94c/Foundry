# Open questions and research log

This is the uncertainty register, not a parking lot. An item leaves this file
only through an explicit decision or verified evidence.

## Questions requiring product-owner answers

| ID | Question | Why it matters | Proposed default |
|---|---|---|---|
| Q-001 | Which open-source license: Apache-2.0, MIT, or another? | Dependency compatibility, contributions, patent terms | Apache-2.0, subject to owner approval |
| Q-002 | May Foundry intentionally edit a file that was already dirty at session start after warning, or must it ask per such file? | Autonomy versus strongest protection of hand edits | Ask on first write to each baseline-dirty file |
| Q-003 | Is running commands an approval each time, once per exact command, or governed by remembered rules? | Defines interactive friction and approval storage | Ask for each normalized shell command in V1; direct read-only Git inspection may be allowed |
| Q-004 | Should repository configuration be supported in V1, and if so what filename? | Config trust and portability | User config first; add repo config only when a concrete need appears |
| Q-005 | What session retention default is acceptable (count/days/size), and should source-containing artifacts be encrypted at rest? | Privacy, disk use, implementation dependencies | Local retention cap; user-triggered deletion; no unverified encryption promise |
| Q-006 | When personal auth remains blocked, may the first V1 be called Gateway-only, or is ChatGPT subscription access required for the V1 label? | Release gate and sequencing | Treat it as a named product decision after the spike |
| Q-007 | Is “local main” merely the first test workflow, or should Foundry warn more strongly when operating on main? | Avoid encoding branch folklore as safety | Never hard-code branch behavior; show branch prominently |

## External discovery tasks

| ID | Investigation | Required evidence | Exit condition |
|---|---|---|---|
| R-001 | Official ChatGPT subscription authentication for an independent open-source local CLI | Current official public documentation/terms or written provider confirmation covering client eligibility and flow | Supported contract documented, or explicitly `blocked` |
| R-002 | Corporate auth mechanism | Sanitized contract: invocation/HTTP flow, token format handling, lifetime, scopes/audience, error/refresh/logout behavior | Fake and live credential-source contract tests defined |
| R-003 | Corporate Responses compatibility | Sanitized streaming fixtures for plain response, tool call/result continuation, usage, refusal, rate limit, disconnect, and malformed event | Capability profile and adapter mapping approved |
| R-004 | Corporate Claude exposure | Actual endpoint/protocol, tool schema, streaming and error semantics | Reuse Responses adapter or approve a separate narrow adapter/defer |
| R-005 | Windows credential persistence | Threat/operational comparison of Credential Manager, DPAPI-protected file, and any acceptable dependency | ADR selects mechanism and logout semantics |
| R-006 | Windows filesystem boundary | Handle-based proof for reparse detection, containment, race behavior, atomic replace, long/Unicode paths without Developer Mode | Spike tests pass on supported Windows 11 image |
| R-007 | Windows process lifecycle | PowerShell edition/path, quoting/encoding, job object behavior, Ctrl+C and descendant cleanup | Spike proves bounded cancellation and capture |
| R-008 | Offline packaging | Candidate dependency graph, wheels for Windows/Python 3.12, licenses, hashes, install procedure | Clean no-index install succeeds without build toolchain |
| R-009 | Reference-agent review | Version/commit, license, design observation, failure lesson; no copied code | Review informs ADRs without dictating Foundry interfaces |

## Critical risks

| Risk | Likelihood / impact | Mitigation |
|---|---|---|
| No supported independent ChatGPT subscription auth | High uncertainty / high | Time-box official-source spike; fake backend; never use private flow |
| “Responses-compatible” covers chat but not agentic tool continuation | Medium / high | Capture protocol fixtures before implementation |
| Trusted-host messaging is mistaken for confinement | Medium / critical | Prominent disclosure; distinguish file tools from commands; threat tests |
| Dirty worktree causes loss or false ownership claims | Medium / critical | baseline + per-write digest guard + adversarial Git suite |
| Windows path/reparse behavior defeats lexical checks | Medium / critical | handle-based Windows implementation and native tests |
| Child processes survive timeout/cancel | Medium / high | job objects and descendant fixture tests |
| Secrets leak through HTTP errors or command output | Medium / critical | structural exclusion, source/sink redaction, canary leak suite |
| Broad abstractions delay a usable vertical slice | Medium / medium | internal ports, fake-backed slice first, defer stable SDK |
| Offline dependency set cannot be reproduced | Medium / high | minimal dependencies and clean wheelhouse rehearsal early |

## Research performed in this blueprint pass

The repository was empty except for its initial placeholder. An attempt on
2026-08-29 to fetch the current official Codex manual through the available
documentation helper failed because the environment could not resolve
`developers.openai.com`. Direct attempts to retrieve named public GitHub sources
also returned access errors. Therefore this blueprint makes **no current factual
claim** that an independent ChatGPT subscription OAuth flow exists and records
reference-source comparison as R-001/R-009 rather than filling the gap from
memory.

This limitation affects literature review, not the internal requirements derived
from the product owner's answers. Before implementation, research must be rerun
in an environment with official-source access, with source versions and licenses
recorded in a provenance note.
