# Claude Code runtime design (research note)

> 来源：后台调研 agent（2026-08-29），基于 code.claude.com 官方文档、Anthropic 工程博客（SWE-bench post、Agent SDK post）与社区逆向分析（minusx 等）。

## Summary

Claude Code's runtime is deliberately simple at its core — a single flat message loop ("gather context → take action → verify work → repeat") over a small set of filesystem/shell tools — with almost all engineering effort invested in three places: (1) a client-enforced permission pipeline (hooks → deny → ask → mode → allow → interactive callback, with deny from any settings layer irrevocably winning), (2) "error-proofed" tools whose contracts are designed so the model can recover from its own mistakes (exact-string-replace edits with unique-match errors, absolute-path requirements, read-before-edit state tracking, hard output truncation), and (3) context economy (on-demand loading of skills/subdir memory, subagent context isolation, tool-output eviction then summarization when context fills). Anthropic's own stated rationale for string-replace editing is empirical: "We experimented with several different strategies for specifying edits to existing files and had the highest reliability with string replacement" (SWE-bench post), and independent agent frameworks converged on the same interface (str_replace appears in 5 of 13 surveyed agents in an arXiv taxonomy). Community reverse-engineering (minusx) confirms the philosophy: no embeddings/RAG, no multi-agent graph, at most a shallow subagent branch, heavy use of a cheap model for auxiliary calls, and a model-maintained todo list to fight drift.

Sessions are append-only JSONL transcripts (`~/.claude/projects/<project>/<session-id>.jsonl`) whose format Anthropic explicitly declares internal and version-unstable; resume replays the file, fork copies it to a new ID, and compaction replaces history with a summary plus recent exchanges and up to five recently read files, re-injecting CLAUDE.md from disk afterward. CLAUDE.md is context, not configuration — anything that must always happen belongs in hooks or permission rules, which the client enforces regardless of what the model decides.

## Designs

### Permission rules: allow/ask/deny, evaluation order
- **How**: Rules are `Tool` / `Tool(specifier)` strings in three lists. Evaluation: deny → ask → allow, first match wins, specificity does NOT change order. Bare-name deny removes the tool from the model's schema entirely; scoped deny leaves it visible but blocked. Bash rules match whole command text; compound commands split on `&&`/`||`/`;`/`|`/newlines with each segment matched independently; commands it can't fully parse (or >10k chars) always prompt. Read/Edit path rules use gitignore syntax with 4 anchors; a Read deny also blocks Edit/Write on the same path. Built-in read-only command whitelist never prompts. "Don't ask again" persists as generated allow rules into `.claude/settings.local.json`.
- **Takeaway**: COPY: deny→ask→allow order; deny-wins-across-all-layers; bare vs scoped deny semantics; persisting approvals as generated local rules; built-in read-only whitelist; the safety valve "can't parse → ASK". ADAPT: conservative PowerShell/cmd operator splitting instead of full AST parsing. REJECT: wrapper stripping, symlink dual-path checks, redirection-target write checks (V1).

### Permission modes
- **How**: default / acceptEdits / plan (read-only until plan approved; approval switches mode) / bypassPermissions (deny rules still fire) / dontAsk (auto-deny anything not pre-approved, for CI) / auto (classifier model reviews actions). Hard-coded circuit breaker: "critical paths" (`rm -rf /`, `~`, writes to `.git`/`.claude`) are never auto-approved by any allow rule or hook.
- **Takeaway**: COPY default/acceptEdits/plan/dontAsk as mode = baseline decision when no rule matched; COPY the critical-path circuit breaker. REJECT auto-mode classifier (second model call per action).

### Settings layering
- **How**: managed → CLI flags → project-local (.gitignored) → shared project → user. Rule lists concatenate across layers; deny at ANY level cannot be overridden by allow at any other. Repo-supplied allow rules gated behind a workspace-trust dialog; deny/ask apply untrusted (they only restrict).
- **Takeaway**: ADAPT to user + project-local (+ optional managed floor); COPY the merge law exactly (deny-from-anywhere wins; project layer can only tighten); COPY the one-time trust prompt before applying a repo's checked-in ALLOW rules.

### Hooks
- **How**: shell commands on ~20 lifecycle events; exit 2 = hard block; JSON output can allow/deny/ask/rewrite input. Hooks run BEFORE rules; hook allow does NOT skip deny/ask rules; hook deny beats everything. Positioned as the deterministic complement to advisory CLAUDE.md.
- **Takeaway**: ADAPT to a single `pre_tool(tool, input) -> ALLOW|DENY|ASK|rewrite` extension point; COPY the composition law. Full hook zoo rejected for V1.

### Tools & string-replace editing
- **How**: Edit = exact string replace (`old_string`/`new_string`), replacement only on exactly one match, informative errors on 0/>1 matches, `replace_all` option. Harness-enforced read-before-edit (per-session path→content state; edits to unread files refused). Absolute-path requirement kills relative-path-after-cd bugs. Bash output truncated ~30K chars; Glob mtime-sorted capped 100; Grep = ripgrep with modes. No embeddings/RAG — agentic search only.
- **Takeaway**: COPY: anchored exact-text editing with unique-match errors + read-before-edit tracking + content-hash staleness check; retry-oriented error messages; output truncation caps; mtime-sorted list; ripgrep-shaped search. This aligns with Foundry's decision (D-009) for anchored search/replace hunks in apply_patch.

### CLAUDE.md memory
- **How**: markdown loaded at session start (managed → user → ancestors → project → local), delivered as user-message context, "shapes behavior but is not a hard enforcement layer"; guidance <200 lines; re-read from disk and re-injected after every compaction (prevents the top-reported "instructions lost after compact" bug).
- **Takeaway**: COPY one project FOUNDRY.md/AGENTS.md + optional user-level file, trust-gated; COPY re-injection after compaction. REJECT hierarchy/imports/auto-memory for V1.

### Plan mode
- **How**: a permission mode, not a different loop — write tools blocked regardless of allow rules; approval dialog approves plan AND switches mode.
- **Takeaway**: COPY — with a PolicyEngine it is nearly free (mode that forces DENY/ASK on apply_patch + non-read-only run_command).

### Subagents
- **How**: isolated context windows, restricted tools, return a summary; spawn depth capped; output scanned for instruction-shaped patterns (injection defense). Deliberately shallow — no agent graph.
- **Takeaway**: REJECT for V1; leave a seam (loop callable with fresh context + restricted tools returning final text).

### Sessions: JSONL, resume, compaction
- **How**: append-only JSONL per session; format declared internal/version-unstable, supported interfaces are export commands; resume = replay, fork = copy-to-new-id. Compaction order: evict older tool outputs first (cheap, lossless), then summarize if still over budget, keep recent exchanges + recently-read-file list, re-inject CLAUDE.md. 30-day retention sweep.
- **Takeaway**: COPY the compaction order of operations (evict-then-summarize) — V1 ships the evict/mask stage only; COPY retention knob; COPY treat-line-format-as-internal + provide export.

### Skills / slash commands
- **How**: SKILL.md with frontmatter; progressive disclosure (only name+description in context, body loads on invocation); `disable-model-invocation` for side-effectful user-only workflows.
- **Takeaway**: ADAPT minimal `/name` = prompt file + `$ARGUMENTS` substitution (V2); progressive disclosure is the right token-budget pattern.

### Agent SDK principles
- **How**: loop = gather context → take action → verify work → repeat; six-step permission pipeline: 1 hooks → 2 deny → 3 ask → 4 mode → 5 allow → 6 interactive callback; verification: defined rules/linting > visual feedback > LLM-as-judge.
- **Takeaway**: ADOPT the six-step pipeline verbatim as the PolicyEngine spec — every behavior testable as a table of (rules, mode, call) → outcome. Build the "verify" leg from the start.

## Risks

- Shell-rule matching is the weakest link, worse on Windows (PowerShell AST, alias canonicalization, env-runner wrappers like `npx`/`docker exec` are documented bypasses). Never ship naive prefix matching without "can't parse → ASK".
- Permission rules bind the client, not the model — CLAUDE.md-style instructions enforce nothing; every security property must live in the code path every tool call traverses.
- String-replace failure modes: whitespace mismatch, non-unique old_string, line-number-prefix leakage from numbered reads, stale reads — mitigations (occurrence-count errors, replace_all, content-hash tracking) must be implemented or the model loops burning tokens.
- Deny-rule path anchoring footguns: a deny that matches nothing is silent — validate rules at startup, warn on dead rules.
- Compaction losing instructions is the top user-reported memory bug — re-injection is the fix.
- Context rot: a single verbose test-suite output can wreck a session; caps are the first-line defense, not compaction.
- Prompt injection via tool output on a no-sandbox host: ship default deny rules for `.env`/credential patterns; disclose that hostile repo content can steer the agent.
- Over-copying risk: every layered subsystem is a maintenance liability for one user — "with every layer of abstraction you make your system harder to debug."

## Sources

- https://code.claude.com/docs/en/permissions
- https://code.claude.com/docs/en/permission-modes
- https://code.claude.com/docs/en/settings
- https://code.claude.com/docs/en/hooks
- https://code.claude.com/docs/en/agent-sdk/permissions
- https://code.claude.com/docs/en/tools-reference
- https://code.claude.com/docs/en/how-claude-code-works
- https://code.claude.com/docs/en/sessions
- https://code.claude.com/docs/en/memory
- https://code.claude.com/docs/en/sub-agents
- https://code.claude.com/docs/en/skills
- https://code.claude.com/docs/en/best-practices
- https://claude.com/blog/building-agents-with-the-claude-agent-sdk
- https://www.anthropic.com/engineering/swe-bench-sonnet
- https://minusx.ai/blog/decoding-claude-code/
- https://kirshatrov.com/posts/claude-code-internals
- https://arxiv.org/pdf/2604.03515
