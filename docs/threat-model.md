# Foundry threat model (V1)

> This document states what Foundry **protects**, what it **does not**, and
> where each guarantee is enforced.
> The principle: better to write down plainly what cannot be done than to make
> a promise that cannot be accepted against.

## 1. The position in one sentence

**V1 is a trusted host with no sandbox.** What approval (policy) reduces is
*mistakes*, not *malice*. Every command you approve runs with your full user
privileges: it can read and write all of your files (credentials included),
access the network, and read environment variables.

That disclosure is printed on first run and at the start of every session (the
`DISCLOSURE` in [cli/render.py](../src/foundry/cli/render.py)).

## 2. Trust boundaries

| Source | Trust level | Notes |
|---|---|---|
| the task the user types in the terminal | trusted | the only source of instructions |
| user configuration under `~/.foundry/` | trusted | machine-local, written by the user themselves |
| managed policy (ProgramData, ACL-protected) | trusted and takes precedence | can only tighten |
| **repository file contents** | **untrusted** | may contain injected instructions aimed at the model |
| **tool output** (command stdout, git diff, read_file results) | **untrusted** | same as above |
| **in-repository `.foundry/config.toml` and `FOUNDRY.md`** (from the repository's top down to the working directory, D-041) | **untrusted** | require a one-time trust confirmation; can only tighten policy |
| command-line settings (`-m`, `--mode`, `-c`, `--profile`, D-042) | trusted | typed by the user; `-c` refuses `api_key` and `headers`, since a command line is readable by other processes, and profiles come only from the user's own file |
| images: `-i` and `/image` (the user's), `view_image` and an MCP tool's (D-045) | **untrusted content** | an image is read like a file: text in a screenshot is not an instruction; `view_image` is bound to the workspace |
| MCP servers the user configured (D-043) | trusted to run, **untrusted output** | started only from the user's own files; their tool results, descriptions and schemas are content like any tool output, and their read-only hints are not believed |
| the user's own `FOUNDRY.md` / `AGENTS.md` in the Foundry folder | trusted | written by the user; capped like the repository's, and still guidance, not authority over approvals |
| **model output** | **untrusted** | it is a request awaiting judgement, not an authorisation |

The realistic definition of a "trusted repository": **one whose every file you
are willing to have both executed and read as instructions.**

## 3. Threats that are defended, and where

| Threat | Defence | Enforcement point |
|---|---|---|
| the model edits files outside the workspace by mistake | paths must be workspace-relative; realpath+normcase+commonpath compared on both sides; reparse points refused component by component; ADS / device names / UNC / drive-relative paths refused | [workspace.py](../src/foundry/core/workspace.py) |
| a prefix allowlist bypassed by a chained command | commands are segmented on `;` `\|` `&&` `\|\|` and CR/LF; **an ALLOW only holds if it covers every segment**, while a DENY/ASK takes effect on any segment or on the whole line; aliases are normalised (rm/del -> Remove-Item); anything containing structural characters (`#` `<` `>` `$`, a backtick, `^`, or a bare `&`) or whose command head does not look like an ordinary executable name can never be auto-approved | [policy/segmenter.py](../src/foundry/core/policy/segmenter.py), [policy/engine.py](../src/foundry/core/policy/engine.py) `Rule.matches` |
| **lexical traps making a dangerous command invisible to the breaker table** | the breaker table additionally scans a **deliberately wrong reading** (`paranoid_segments`): it ignores quotes, comments and grouping, splits on separators, and strips quotes, brackets, `&` and `.` from the edges. It can only add denials, never allowances, so it cannot be fooled by any unanticipated spelling (a comment swallowing a newline, an infix `<#`, grouping and script blocks are all covered) | segmenter.py `paranoid_segments` |
| the breaker table bypassed by argument shapes | the command head is normalised (`git.exe` -> `git`, aliases expanded); `effective_argv` **scans to the first real git subcommand** rather than skipping an enumerated list of global options (`--attr-source HEAD` once pushed the subcommand out of the inspected position); `git checkout` is refused outright with a pointer to `git switch`, and `git switch --discard-changes` is refused too | policy/engine.py `check_breaker`, segmenter.py `effective_argv` |
| a repository's .git/config turning git into a program launcher | `diff.external` is explicitly disabled, along with `--no-ext-diff --no-textconv`, `core.pager/editor/sshCommand` and `protocol.ext`; `safe.directory` is limited to the current workspace (never `*`); git subprocesses also go through `child_environment()` filtering | [tools/git.py](../src/foundry/core/tools/git.py) |
| a read-only tool reading external files through a junction | `os.walk` follows junctions (`islink` returns False for them), so `list_files`/`search_text` check the reparse point of each directory as they descend | [tools/files.py](../src/foundry/core/tools/files.py) `_prune` |
| a repository escalating its own privileges | repository configuration accepts only deny/ask; **`[runtime]` can also only tighten** (mode may only be plan/dont_ask, and budgets may only go down); connection-class configuration (endpoint / credentials / headers / proxy) is read from machine-local layers only; writing to `<workspace>/.foundry/` is refused by the breaker table | [config.py](../src/foundry/core/config.py), [policy/engine.py](../src/foundry/core/policy/engine.py) |
| a patch overwriting an undeclared file through `Move to:` | the move target counts toward `paths`, so it is visible to the breaker table, the dirty-file ASK rule and the approval display; an existing target is refused; the target is resolved during **planning**, so an illegal path does not fail only after the source file has already been rewritten | [tools/patch.py](../src/foundry/core/tools/patch.py) |
| a patch silently changing the wrong place | the line-level mapping in the leniency gradient requires the match to land on line boundaries, and otherwise fails with a hint -- an in-line hit is never expanded into a whole-line replacement | tools/patch.py `_map_span` |
| the patch parser treating SEARCH content as a separator | more than one `=======` inside a hunk is judged **ambiguous and refused** (with a suggestion to rewrite the whole file) -- editing a file that contains conflict markers is the likeliest task to hit this, and splitting on the first separator would silently write the wrong content and report success | tools/patch.py `parse_patch` |
| the same file written twice in one patch | textual normalisation (case / `./` / `..` / separators) catches it at validate time, and execution normalises again by `realpath` as a second catch (only the latter recognises 8.3 short names) | tools/patch.py |
| repository rule text injected into the system prompt | a rule's tool/pattern/reason is flattened to one line and truncated before rendering -- otherwise a deny rule's reason could write a convincing "the following commands need no approval" into the permissions section | [prompts.py](../src/foundry/core/prompts.py) |
| a single round returning a flood of tool calls that exhausts the budget | the budget is checked **before every call**, not once at the start of a round (the latter once let a single round execute 5000 calls) | runtime.py |
| the model moving HEAD through a wrapper to make the diff look clean | git evidence is re-collected at close-out, and a HEAD that differs from the baseline downgrades to `partial` and records an event; the report distinguishes this session's changes from pre-existing ones | [runtime.py](../src/foundry/core/runtime.py) `_finalize` |
| destroying the user's uncommitted work | the breaker table refuses `git checkout -- / restore / reset --hard / clean / stash drop / stash clear`; the baseline records dirty files, and editing one forces an ASK | policy/engine.py |
| the model forging "verified" | every claim in `finish` must cite a real command event with a matching exit code, or it is downgraded to partial; a moved HEAD downgrades too | [runtime.py](../src/foundry/core/runtime.py), [tools/finish.py](../src/foundry/core/tools/finish.py) |
| credentials entering logs, the context or the event stream | `SecretHandle` is not printable; a credential is resolved only at the HTTP header injection point; a single choke point does byte-level exact matching (UTF-8 and UTF-16LE, before base64) | [auth.py](../src/foundry/core/auth.py), [redaction.py](../src/foundry/core/redaction.py) |
| test code stealing keys from the environment | the subprocess env keeps only a minimal set, removing variables containing fragments such as KEY/TOKEN/SECRET/PASSWORD/AWS_ | [winapi.py](../src/foundry/core/winapi.py) `child_environment` |
| orphan processes holding file locks after a timeout | a Job Object (KILL_ON_JOB_CLOSE) owns the process tree, killing it all at once and releasing the inherited pipe handles | winapi.py `ProcessJob` |
| terminal escape sequence injection (OSC 52 clipboard exfiltration and friends) | ANSI/OSC/control characters are stripped and rich markup escaped before rendering | [cli/render.py](../src/foundry/cli/render.py) |
| a non-ASCII filename bypassing the dirty-file guard | git escapes non-ASCII paths as `\303\251...` by default, so a file with an accented or CJK name never matches the dirty set; the hardened arguments add `core.quotepath=false` | tools/git.py `_HARDENING` |
| a crashed session mistaken for a successful one | a journal with no termination event is always judged `interrupted`; a truncated final line is tolerated; a resumed run that has not ended reads as `interrupted`, not as the earlier run's outcome | [session.py](../src/foundry/core/session.py) |
| a resumed session trading on a workspace that has changed | a moved HEAD refuses the resume; commands from before it cannot back a finish claim, files are read again before a patch, and approvals start over; files changed while the session was stopped join the dirty-file guard and are named to the model; the restored conversation is the journal's redacted copy; one process writes a journal at a time (an OS lock), and a refused resume writes nothing | [resume.py](../src/foundry/core/resume.py), [cli/app.py](../src/foundry/cli/app.py) `build` |
| an undo discarding work that is not the session's | a file is put back only while it holds exactly what the session wrote, checked again just before the write; an edit since, or between two of its patches, stops undo there and is named; content holding a known credential is never kept, so a redacted copy is never written back; writes go through workspace containment and a move is reverted whole; afterwards the model is told, reads the files again, and earlier commands no longer count as evidence | [undo.py](../src/foundry/core/undo.py), [cli/app.py](../src/foundry/cli/app.py) `cmd_undo` |
| plan mode, or a review, changing something anyway | plan mode and a read-only task refuse every mutator at step 2, with the DENY rules, before any ASK rule could put the change to the user (they used to sit at step 4, after the dirty-file and unparseable-command questions); `/review` sets the read-only reason for its turn in every mode and clears it even when the turn fails | [policy/engine.py](../src/foundry/core/policy/engine.py) `evaluate`, [cli/repl.py](../src/foundry/cli/repl.py) `run_task` |
| a repository's custom prompt steering the session | `.foundry/prompts/` is read only in a workspace the user trusts (a repository with that folder is asked about), the user's own prompts and the built-ins take precedence, a prompt is marked as the repository's when used and capped at 24 KB; commands come only from the user's typed line, and the model cannot write under `.foundry/` | [core/commands.py](../src/foundry/core/commands.py), [cli/app.py](../src/foundry/cli/app.py) `_confirm_trust` |
| the model leaving plan mode, or changing its mode or model, on its own | only `/approve` and `/mode` (and `/model`, D-047), typed by the user at the session's prompt, change them; nothing the model writes or a tool prints reaches the REPL's commands, and no tool changes the mode; approval moves only to `default` or `accept_edits`, the system prompt is rebuilt to match, the change is journaled with every later decision's mode, and a resume starts in the configured mode again | [cli/repl.py](../src/foundry/cli/repl.py) `approve_plan`, [prompts.py](../src/foundry/core/prompts.py) `system_prompt` |
| an MCP server as a way around the policy | a repository cannot name a server (its config is refused); every MCP tool is a mutator -- refused in plan mode and /review, asked about otherwise, denied unattended -- unless a user rule allows it or the user's own entry says `read_only = true`; the server's `readOnlyHint` is ignored, since a "read-only" fetch is how data leaves; the server gets the scrubbed command environment and secrets only by name through `env_vars`; it runs under a job object that ends it and its children with the session; a hung call times out and Ctrl+C reaches it | [core/mcp.py](../src/foundry/core/mcp.py), [config.py](../src/foundry/core/config.py) `_apply` |
| `repo_map` turned against Foundry: code that runs when parsed, a file built to exhaust the parser, a tree too large, a junction out of the workspace | `ast.parse` compiles nothing and runs nothing; a parse failure, `RecursionError` or `MemoryError` is the file's error, named in the result; files over 1 MB are not read, a call reads at most 2,000 files in 10 s and says when it stopped; the path resolves in the workspace and the walk prunes reparse points like `list_files`; the outline goes through the same redaction as all tool output | [tools/repomap.py](../src/foundry/core/tools/repomap.py) |
| an image carrying a secret, or swamping the journal or the context | an image cannot be scrubbed, so one whose bytes hold a credential Foundry knows, or bytes shaped like one, is not kept in the artifacts (the model is still shown what the user or the workspace provided); every other image is kept once, by digest, and journaled by id, never inline; it is checked before it is sent (type from its bytes, size, pixels) and counted as a provider counts it; `view_image` resolves inside the workspace | [images.py](../src/foundry/core/images.py), [runtime.py](../src/foundry/core/runtime.py) `_store_image`, [tools/image.py](../src/foundry/core/tools/image.py) |
| the model's reasoning leaking a secret or forging the screen | it arrives whole and passes the redactor as one string on the screen, in `--json` and in the journal; it is escaped and stripped of control sequences like other model text; it is never sent back to the model or rebuilt by a resume | [runtime.py](../src/foundry/core/runtime.py) `_sample`, [render.py](../src/foundry/cli/render.py) `_reasoning` |
| a compaction losing work, leaking a secret, or vouching for a stale read | the summary is scrubbed before it enters the history; every file read before it must be read again before a patch; a failed or empty summary leaves the conversation unchanged and stops automatic attempts until the next task; the new history is journaled (synced) so a resume rebuilds exactly what the model saw, and a crash before that record resumes from before the compaction | [runtime.py](../src/foundry/core/runtime.py) `compact`, [context.py](../src/foundry/core/context.py) `compacted_history`, [resume.py](../src/foundry/core/resume.py) `_fold_requests` |
| a model-written plan forging terminal output or posing as evidence | each step is reduced to one line of plain text when the call is validated (line breaks, control and format characters removed) and escaped when drawn; every string an event carries is scrubbed, nested ones included; `finish` lists the steps not marked completed and never changes a status over them | [tools/plan.py](../src/foundry/core/tools/plan.py), [events.py](../src/foundry/core/events.py) `EventSink`, [runtime.py](../src/foundry/core/runtime.py) `_finalize` |
| the model retrying forever by rewording | failure fingerprints count by "normalised operation + error class", so a change of wording does not reset the counter | runtime.py `FailureTracker` |

## 3.5 Three principles learned the hard way

**Four rounds of adversarial review, and every one of them broke the command
segmenter** -- that fact matters more than any individual hole.

### (a) When denying, do not trust your own lexer

Round one patched `&'foo'`; round two brought `(git reset --hard)`,
`&{git ...}` and `cmd /c git ...`; round three wrote comment stripping, and
round four found it **wrong in both directions** -- the comment after `'x'#`
was not recognised (stripped too little), while the `<#` inside
`a<# ; git reset --hard #>` was taken for a block comment (stripped too much,
erasing a statement PowerShell really would execute). Four rounds, four
breaks, each with a spelling the previous round had not imagined. Continuing
to patch would only buy a fifth.

**The approach: the breaker table scans two independent readings, and either
one hitting means refusal.**

`paranoid_segments()` rescans the command with a **deliberately crude**
reading -- ignoring quotes, comments and grouping, splitting only on
separators and brackets, and stripping quotes, brackets, `&`, `.` and commas
from token edges. It is used only by the breaker table: **it can only add
denials, never allow anything**. Every breaker rule anchors on `argv[0]`, so a
string mention like `echo "git reset --hard"` is not a false positive.

**⚠️ An overclaim that used to stand here, and round five's correction**: this
section once said "denial no longer depends on parsing PowerShell correctly".
**That was wrong**, and round five falsified it directly: `paranoid_segments`
is not "no lexer", it is **a second lexer**, with blind spots of its own.
`git reset ,--hard` fooled both at once (PowerShell's comma array operator
hands `--hard` to git, while both readings see only the literal token
`,--hard`), and in practice it was allowed and destroyed uncommitted work.

The honest statement is: **defence in depth, not a guarantee**. Two
independent readings mean an attack has to fool both, which is stronger than
one parser and weaker than a guarantee. Commas are now normalised and infix
brackets are now split, but the next PowerShell syntax feature may fool both
again. The real boundary is still the sentence in §1: there is no sandbox.

On the auto-approval side, the syntax was tightened to a small verifiable one:
**a command containing any character that can move a statement boundary or
hide text (`#`, `<`, `>`, `$`, a backtick, `^`, or a bare `&`) can never be
auto-approved**. Parentheses and braces are deliberately not on that list --
they can group but cannot hide a boundary, and `python -c "print(1)"` is far
too common; grouped forms are handled by the command-head check and the
paranoid reading.

`effective_argv` follows the same logic: instead of counting "how many global
options to skip", it **scans to the first real git subcommand** --
`--attr-source HEAD` once pushed the subcommand out of the position every
breaker rule inspects (demonstrably destructive).

**The cost**: a command containing `#` (even inside quotes) now needs one
approval. Two rounds went wrong on comment syntax, so we accept that cost. The
command still runs; it just takes a yes.

### (b) The fix is itself a source of new holes, and tends to trade a hard guarantee for a soft one

All 5 new defects in round two were caused by round one's fixes; round three
caught 2 caused by round two; **both of round four's criticals were inside the
comment stripper round three wrote**; round five falsified round four's claim
of convergence. **The same class of mistake was made twice**: returning early
to mark something "unparseable" threw away the segments already parsed, so
`git reset --hard; (foo)` fell from the breaker table's **unapprovable DENY**
to an **approvable ASK** -- while the system prompt was still telling the model
that such an operation "cannot possibly be approved".

So there is now a structural invariant test
([tests/test_breaker_invariant.py](../tests/test_breaker_invariant.py)): **no
decoration, mode, session grant or hook rewrite can make a command the breaker
table forbids approvable** -- 28 forbidden commands x 17 decorations = 476
combinations, plus each command once on its own, each mode, session grants and
hook rewrites, plus two new axes, **argument position** and **variable
spelling**, for **780 generated cases** in total. It defends against a whole
class of mistake rather than the few spellings someone thought of; adding
round four's two criticals to the decoration table afterwards reproduced them
in a second.

The `GIT_CONFIG_NOSYSTEM` critical taught the same lesson from the other side:
**hardening must ask "what legitimate behaviour did this turn off along the
way?"**, and verification has to run in the environment the user actually has
(fixtures build repositories with ordinary git, not Foundry's own hardened
path).

### (b2) Round six: the hole was not inside a layer, it was on the seam between two

The first five rounds all attacked the segmenter. What round six found has a
completely different shape: **every layer is right on its own, and the
combination is wrong**.

- The patch tool knows `AVERYL~1.PY` and the long name are the same file; the
  policy layer does a string comparison, so an 8.3 alias walked past the
  dirty-file guard -- the one rule whose entire reason to exist is overriding
  `accept_edits`.
- `decode_output` picks an encoding by "which produced fewer replacement
  characters", and the fallback encoding is a single-byte code page mapping all
  256 bytes, which **can never produce a replacement character**. So one bad
  byte turned the whole output into mojibake; and `_drain` cuts at the capacity
  ceiling by byte, which naturally splits multi-byte characters.
- The system prompt told the model "a merge is always refused", `git pull` does
  exactly that merge, and it walked straight past the breaker table.
- `command_timeout_s` had type checking, provenance, and "the repository can
  only tighten" protection -- and **no code read it at all**.

The sharpest one was caught by the clean-room check rather than by the test
suite: redacting events field by field is correct, but the model's text
arrives **streamed in chunks**, a credential fell across two deltas, neither
fragment matched anything, and the renderer reassembled it onto the screen.
The event stream was clean and the terminal leaked.

The lesson, written down: **"correct everywhere" does not imply "correct
together"**. Hence
[tests/test_prompt_matches_breaker.py](../tests/test_prompt_matches_breaker.py)
(the promise and the table aligned in both directions),
`categorical_denials()` (the prompt is generated from the breaker table's
constants rather than written by hand), and a real clean-room end-to-end
(rebuild the wheelhouse -> install into a fresh venv with `--no-index` -> run a
real task -> check the canary is in neither the events, the rendering, stdout,
nor the logs). **A cross-layer property has to be verified across layers.**

### (b3) Round seven: the worst defect in the project, on the one path no test ever touched

The user provided a local endpoint
([research/live-endpoint.md](research/live-endpoint.md)). It cannot verify tool
calls itself, but it forced me to read the **proxy** path -- and then:

**The API key leaves this machine in plaintext.**

When choosing a connection class for an `https://` target, `_connect` **looks
at the proxy's scheme, not the target's**. An ordinary
`HTTP_PROXY=http://proxy.corp:8080` therefore takes the `else` branch, builds a
plain `HTTPConnection`, and calls `set_tunnel(host, 443)` on it. But
`HTTPConnection.connect()` stops once it has sent CONNECT -- only
`HTTPSConnection.connect()` performs the `wrap_socket` that must follow a
tunnel.

Reproduced against a real socket: after the proxy answers
`200 Connection established`, the first byte into the tunnel is `'P'`, not
`0x16`:

```
POST /v1/chat/completions HTTP/1.1
Host: api.openai.com:443
Authorization: Bearer sk-SECRET-TOKEN-...
```

The proxy, and every hop after it, can read the key, the prompt and the entire
conversation. The default `base_url` is `https://api.openai.com/v1`, so this is
**the default path on any machine with a proxy configured** -- and in the
environment this project targets, that is every machine. More ironically,
`_build_ssl_context` is reachable only on the rare https-proxy branch, so the
module docstring's rationale about "trusting the Windows certificate store, so
a corporate MITM proxy needs no configuration" was dead code under exactly the
configuration it was written for.

**Why six rounds of adversarial review missed it**: no test in `tests/` had
ever set a proxy environment variable while exercising `HttpClient`. All six
rounds read code and constructed inputs, and this branch is only reached when
"a proxy is set" and "the target is https" hold **at the same time**; a unit
test's default environment has neither.

The rule written down: **every path a credential travels needs a test that
actually captures the bytes and looks at them**. Now
`test_live_endpoint_findings.py` starts a fake proxy, asserts the first byte
into the tunnel is `0x16`, and asserts the canary is not in the plaintext.
Asserting "it connected to the proxy host" is not enough -- the old code
satisfied that assertion too.

### (c) Say plainly what it cannot solve

`python -c "subprocess.run(['git','reset','--hard'])"` is just as destructive,
and no parsing catches it. So interpreters (python/node/...) are
**deliberately kept approvable** -- refusing `python -m pytest` costs enormously
and buys nothing. "An approved command can do anything that program can do" is
a property of the trusted-host model (see §4), and the segmenter does not
pretend to solve it.

The segmenter guarantees exactly one thing: **a dangerous command written out
directly in the text will not bypass approval by being spelled differently.**

## 4. Explicitly not defended (V1 non-goals)

1. **Malicious repository content / the consequences of prompt injection.**
   Files and tool output can try to instruct the model. The only real defence
   is PolicyEngine gating every side effect independently of the model's
   intent -- but once you approve a command, the injection has already won that
   step.
2. **A local administrator bypassing managed policy.** The user can edit
   site-packages, delete the configuration, or switch environments. The honest
   position for a managed DENY is: *within an untampered installation*, no
   runtime route can relax it. The real boundary is on the Gateway server
   (model allowlisting, request logging, DLP).
3. **The workspace constraint on `run_command`.** The workspace boundary
   constrains only the file tools. A subprocess is inherently unconstrained --
   the gate is policy, not a path check.
4. **TOCTOU and hard links.** The race where a path is replaced with a junction
   after the check and before the open cannot be eliminated without a sandbox;
   hard links cannot be detected by a path check.
5. **General secret detection.** Only the literal byte sequences of credentials
   Foundry itself holds are guaranteed to be removed. Corporate tokens in
   unknown formats, connection strings and cookies will slip through -- pattern
   scanning is labelled best-effort.
6. **NTLM/Kerberos proxies.** The stdlib does not support them; a 407 Negotiate
   is reported as an explicit error rather than failing silently.
7. **The millisecond window on the Job assignment.** The assignment can only
   happen after `Popen` returns, so in theory there is a very brief window in
   which the child could spawn a grandchild that escapes the job. The stdlib
   cannot close that window.
8. **Foundry working on its own source.** Run from a clone, or from an editable
   install, whose repository is also the session's workspace, and a patch the
   session applies changes the policy code the next run loads; in
   `accept_edits` mode such a patch applies without asking. The breaker
   protects `.git` and `.foundry`, not `src/`. A wheel install is not exposed
   (site-packages is outside the workspace). To work on Foundry with Foundry,
   run it from one clone and point it at another.

## 5. How this is accepted against

These are not statements, they are tests:

- **The breaker table invariant**: 28 forbidden commands x 17 decorations
  (chaining, comments, CR separation, an unparseable neighbouring segment,
  shell wrappers, grouping, script blocks, dot-sourcing) = 476 combinations;
  plus the 28 single-command baselines, 24 mode cases, 6 session grants and 6
  hook rewrites, plus the two axes added in round seven -- **argument position**
  (a global option's value is itself a subcommand name; an option's value looks
  like a flag) and **variable spelling** (`$X` / `${X}` / both inside double
  quotes) -- for 780 generated cases in total, every one of which must DENY at
  step 0 (`test_breaker_invariant.py`).
- **The canary leak suite**: run the whole flow with a canary credential and
  assert it appears in neither the journal, artifacts, the audit log, the event
  stream, nor the console; **and send it chunked at 1/2/3/5/13/64 bytes** to
  verify a credential split across deltas is removed too (`test_cli_e2e.py`,
  `test_session.py`, `test_round6_fixes.py`).
- **The prompt and the breaker table aligned in both directions**: every family
  the table refuses must be named in the prompt, and every git subcommand the
  prompt claims is "always refused" must really be refused
  (`test_prompt_matches_breaker.py`). When that paragraph was written by hand it
  promised that merges were refused, and `git pull` walked past.
- **Consistent spelling across layers**: a path the tool layer has resolved
  (8.3 short names, CRLF, case) must reach policy in the same spelling
  (`test_drift_fixes.py`).
- **The credential is ciphertext inside the proxy tunnel**: start a fake proxy
  and assert the first byte into the tunnel is `0x16` (a TLS ClientHello)
  rather than a plaintext `POST`, and that the canary is not in the bytes
  (`test_live_endpoint_findings.py`). Asserting "it connected to the proxy
  host" does not count -- the version that leaked satisfied that too.
- **Real wire-format fixtures**: byte-level captures of real gateway responses,
  including "usage in the same frame as an empty `choices`" and the `event:`
  lines of Responses SSE (`tests/fixtures/live_gateway/`, marked `-text` in
  `.gitattributes` so line endings are never normalised).
- **The path escape table**: junctions, ADS, `..`, device names, UNC and
  drive-relative paths are all refused (`test_workspace.py`).
- **The segmenter attack table**: chained commands, command substitution,
  redirection, the call operator, aliases, CR separation, PowerShell comments,
  and wrapper forms (`test_segmenter.py`, `test_security_regressions.py`,
  `test_security_round3.py`).
- **The policy decision table**: deny-wins, the breaker table is not
  overridable, a dirty file still ASKs under accept_edits (across every path
  spelling), dont_ask is fail-closed, a hook rewrite re-enters the breaker
  (`test_policy.py`).
- **A real git environment**: fixtures build repositories with **ordinary git**
  (not Foundry's hardened path), covering CRLF, paths with spaces, and renames
  (`test_crlf_repo.py`, `test_security_round3.py`).
- **Process tree cleanup**: cancelling a command that spawned a grandchild
  leaves nothing behind and does not hang; when a grandchild holds the pipe it
  is reported as incomplete rather than exit 0 (`test_tools_command_git.py`,
  `test_resource_bounds.py`).
- **Resource ceilings**: 400MB of command output peaks at a measured 27MB; an
  over-limit file is refused with a usable alternative offered
  (`test_resource_bounds.py`).
- **The evidence chain**: a forged claim downgrades completed to partial; a
  moved HEAD downgrades it too; and **citing a green run from before the
  change** downgrades it as well -- checking the exit code without checking the
  order would let a session that verified nothing report completed
  (`test_runtime.py`, `test_golden_tasks.py`, `test_subsystem_audit.py`).
- **Crash recovery**: a truncated journal is judged interrupted
  (`test_session.py`).

## 6. V2 directions

A restricted-token sandbox (Codex's Windows sandbox uses a restricted token + a
dedicated local account + a WFP firewall + an elevation helper service, which
is several quarters of engineering), checkpoints of the whole workspace (undo
of a session's own patches landed, D-036), and an actual distribution
mechanism for managed policy.
Until then, honest disclosure + strong ASK + a complete audit trail is V1's
position, not an oversight.
