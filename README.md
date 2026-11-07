# Foundry

A local coding-agent runtime built from scratch: Python 3.12, Windows, offline
wheel install. It owns all of the code and interfaces for its agent loop,
tools, policy, providers and sessions; it does not fork, call into, or
impersonate any existing coding agent.

**Current status: M0-M4 implemented and running**, with 1436 tests passing on a
machine with no network and no credentials (including the security regressions
from six rounds of adversarial review, and 780 generated breaker-table
invariant combinations). After round 6 there was a separate clean-room check:
rebuild the wheelhouse, install into a fresh venv with `--no-index`, and run a
real task again (failing tests -> patch -> re-run green -> finish citing the
event id from its own tool output). All 14 checks passed.

## Want to understand how it works first?

`demo/` is this system's skeleton: exactly the same structure as the real
thing, with every layer shaved down to its thinnest form. No network, no API
key:

```bash
python demo/mini_foundry.py --yes
```

To run it a cell at a time, see the state in between, and change something and
re-run, use the notebook version (it imports from the `.py` rather than copying
code):

```bash
jupyter lab demo/mini_foundry.ipynb
```

To watch policy stop something that should not happen, and the finish gate see
through a false "all the tests pass":

```bash
python demo/mini_foundry.py --script destructive --yes
python demo/mini_foundry.py --script liar --yes
```

[demo/README.md](demo/README.md) maps every section onto the real module, and
explains how to point it at a real model (any OpenAI-compatible
`/v1/chat/completions` will do).

## Quick start

There are two ways to run Foundry. Both run the same code; they differ only in
how it gets onto the machine ([D-033](docs/decision-log.md)).

### From a clone (development, personal use)

Nothing is installed but the dependencies (`requirements.in`); the tests and
the CLI run from the source tree.

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.in "pytest>=8.0,<9"
.venv\Scripts\python -m pytest -q
.venv\Scripts\python run-foundry.py doctor
```

`foundry.cmd` is the same launcher as a command: put the clone on `PATH` (with
that environment's `python` ahead of others), go to the repository you want to
work in, and run `foundry doctor`, `foundry login`, `foundry`. `doctor` prints
`source`, the directory the code was imported from, so it is always plain which
checkout is running.

### Installed offline (a machine with no package index)

The distribution channel for the corporate machine is a wheel
([D-002](docs/decision-log.md)). `wheelhouse/` is not in version control, so
build it first:

```bash
python scripts/build_wheelhouse.py
```

Then install offline:

```bash
python -m pip install --no-index --find-links wheelhouse foundry
foundry doctor
foundry login
foundry
```

### Using Claude through the native API

`foundry login` and `OPENAI_API_KEY` cover OpenAI-compatible endpoints. For
Claude, put this in `%LOCALAPPDATA%\Foundry\config.toml` (or `$FOUNDRY_HOME`):

```toml
[backend]
protocol = "anthropic_messages"
# model = "claude-opus-5-5"     # the default for this protocol
# effort = "high"               # low | medium | high | xhigh | max; unset leaves the API default; /effort changes it
# reasoning_summary = "none"    # stop asking for the thinking summary shown as "thinking"
# max_context_tokens = 1000000  # the model's window; masking starts at half of it
```

and set `ANTHROPIC_API_KEY` (or run `foundry login`, which stores it apart from
any OpenAI key). The base URL defaults to `https://api.anthropic.com/v1` for
this protocol, so the key is never sent to the OpenAI default host. Rounds read
the previous round's prompt from the cache, and safety-classifier declines are
retried on Anthropic's fallback model (`fallbacks = "off"` disables that).

On any protocol, an answer whose stream is cut part-way -- a dropped
connection, a stall, an overloaded server -- is asked again, saying so, up to
`stream_max_retries` times under `[backend]` (5, as codex; 0 never); none of
the cut answer is kept.

A backend can name the variable its key is in, as codex's `model_providers`
do -- then the key comes from there only, so two backends of one kind each
send their own -- and take headers from variables and a query on every URL:

```toml
[profiles.router.backend]
base_url = "https://openrouter.ai/api/v1"
model = "anthropic/claude-sonnet-5.5"
env_key = "OPENROUTER_API_KEY"                    # this backend's key, and only this
# max_context_tokens = 1000000   # the model's window, on any protocol (codex's model_context_window)
# max_output_tokens = 8192        # the most one reply may be (codex's model_max_output_tokens)
# env_http_headers = { "X-Org-Id" = "CORP_ORG_ID" }   # values from variables; scrubbed
# query_params = { api-version = "2025-04-01-preview" }   # on every request (Azure)
```

Each directory's instructions are `AGENTS.override.md` (your own, beside the
shared file), else `FOUNDRY.md`, else `AGENTS.md`. As in codex, other names can
be read where those are missing, and the budget the repository's files share
changed (both from your own config only; a repository may only lower the
budget):

```toml
[runtime]
project_doc_fallback_filenames = ["CLAUDE.md", ".cursorrules"]
project_doc_max_bytes = 32768      # characters; 0 reads none of the repository's
```

## Commands

| Command | What it does |
|---|---|
| `foundry` / `foundry run [task]` | interactive session (the default); a task given here is the first message and the session goes on after it, as `codex "task"` does (not at a terminal, the run ends with it); `/help` lists its commands -- `/status`, `/diff`, `/plan`, `/approve` (plan mode), `/mode [NAME]`, `/model [NAME]` (alone, the endpoint's models), `/effort [LEVEL]`, `/mcp`, `/undo`, `/compact [focus]`, `/rewind [N]`, `/image [PATH]`, `/review [--base REV | --commit REV] [focus]` (read-only: the uncommitted changes, a branch's since it left REV, or one commit's), `/init`, `/new`, `/exit` (codex's `/approvals`, `/mention` and `/logout` are told theirs here) -- and your custom prompts (`~/.foundry/prompts/<name>.md`, used as `/<name> args`: `$1`..`$9`, `$ARGUMENTS`, and `$FILE` filled from `FILE=value`). The model reads your own `FOUNDRY.md` (or `AGENTS.md`) from the Foundry folder, then the repository's from its top down to the working directory |
| `foundry exec <task> [--json] [-o FILE] [--output-schema FILE]` | `-` as the task reads it from stdin; unattended execution, which first names its session (the `session_started` event with `--json`, a line on stderr without) for `exec --resume`, and what it runs with -- workspace, model, protocol, profile, mode, effort -- as codex's exec does; every ASK becomes DENY (fail-closed). `--mode accept_edits` lets it patch the workspace (commands still need a rule); `-o` writes its final summary or reply to FILE; `--output-schema` asks for a result matching a JSON Schema, checked by Foundry and printed as the only output |
| `foundry resume [session] [task]` | continue a recorded session (a task given is its next message): with no id, pick from this workspace's recent ones (Enter is the newest; not at a terminal, `last`); refused if HEAD moved. As codex spells it: `--last [task]` for the newest, `--all` to pick from every workspace's (each continued where it was recorded) |
| `foundry exec --resume <session> [task]` | the same, unattended; the task defaults to carrying on where it stopped. `foundry exec resume [--last \| session] [task]`, as codex spells it, is the same |
| `foundry mcp-server [-m MODEL] [--mode MODE] [-c KEY=VALUE] [-p PROFILE]` | Foundry as an MCP server over stdio, as `codex mcp-server`: another agent or an editor gets `foundry` (`prompt`, and `cwd`, `model`, `profile`, `mode`, `config`), which runs the task as `exec` does and answers with its last word and `sessionId`, and `foundry-reply` (`sessionId`, `prompt`), which gives that session another task. Nothing is approved on your behalf: what the mode would ask about is asked of a client that offers elicitation -- its user answers, and `mode` may then be `default` -- and refused otherwise. A cancel stops the task; one task at a time in a repository |
| `foundry undo [session] [--last] [--dry-run] [--yes]` | revert the files a session patched (default `last`); a file edited since is left alone. In a session, `/undo` reverts its newest patch |
| `foundry record <task> --output f.json` | record a session as a replay fixture |
| `foundry sessions [id]` | list sessions with each one's first message (and which are in use), or show one |
| `foundry report [--json]` | patch first-try success rate, command failures, denials, model retries, token stats |
| `foundry login [--with-api-key]` / `login status` / `logout` | credential management (DPAPI encrypted): `--with-api-key` reads the key from a pipe, as `printenv OPENAI_API_KEY \| foundry login --with-api-key`; `status` says where it comes from, never what it is |
| `foundry mcp list [--json]` / `get NAME [--json]` / `add NAME -- COMMAND [ARGS...]` / `add NAME --url URL` / `remove NAME` | the MCP servers in your config.toml, as `codex mcp`: `get` shows one in full -- its arguments, environment and address, a value a credential's name marks hidden; `add` takes `--env KEY=VALUE`, `--env-var NAME` (a secret, passed through by name), `--bearer-token-env-var VAR` and `--read-only`; the file is edited in place, comments kept |
| `foundry doctor` | environment self-check |
| `foundry completion [powershell\|bash]` | a Tab-completion script for the shell, as `codex completion`: add `foundry completion powershell \| Out-String \| Invoke-Expression` to your `$PROFILE`, or `eval "$(foundry completion bash)"` to `~/.bashrc` (Git Bash); subcommands, flags and `--mode`'s values complete, paths are left to the shell |

Exit codes: `completed=0 partial=10 blocked=11 failed=12 cancelled=13
interrupted=14`

Every command that works in a repository takes `-C DIR` (or `--cd DIR`,
`--workspace DIR`; the default is the current directory), as codex does.
codex's flags that mean something else here -- `--full-auto`, `-a`, `-s`,
`--yolo`, `--search`, `--oss`, `--add-dir` and the like -- are refused with
what Foundry takes instead (`-s read-only` is `--mode plan`).
`run`, `resume`, `exec` and `record` take `-m MODEL`, `--mode MODE`,
`-c key=value` (repeatable, such as `-c runtime.max_tool_rounds=60`; never a
secret) and `-p NAME` (`--profile NAME`), a `[profiles.NAME]` table in your `config.toml`
holding `backend`, `runtime` and `permissions` like the file itself:

MCP servers go in the same file -- `foundry mcp add docs --env-var DOCS_TOKEN --
uvx our-docs-mcp` writes the first table below -- and their tools reach the
model as `mcp__<server>__<tool>`; each call asks first unless a rule allows it
(`tool = "mcp__docs__*"`). A server that publishes resources also gives the
model codex's `list_mcp_resources`, `list_mcp_resource_templates` and
`read_mcp_resource`; listing is not asked about, a read is, unless a rule
allows it (`tool = "read_mcp_resource"`, `pattern = "docs *"`):

```toml
[mcp_servers.docs]
command = "uvx"
args = ["our-docs-mcp"]
env_vars = ["DOCS_TOKEN"]   # passed through by name; secrets never go in the file
# read_only = true          # you vouch it changes nothing: offered in plan mode too
# enabled_tools = ["search", "fetch"]   # only these of its tools (by the server's names)
# disabled_tools = ["delete_page"]      # never these

[mcp_servers.tickets]
url = "https://mcp.example.com/mcp"     # a server already running, over streamable HTTP
bearer_token_env_var = "TICKETS_TOKEN"  # the variable holding its token, never the token
```

```toml
profile = "work"          # the default; --profile or FOUNDRY_PROFILE choose another

[profiles.work.backend]
protocol = "anthropic_messages"
model = "claude-opus-5-5"
```

A key Foundry does not read stops the load rather than being skipped, with
where it belongs: `model` at the top of the file is told it goes under
`[backend]`, a flat `[profiles.fast]` table that it needs
`[profiles.fast.backend]`, and codex's names Foundry's own
(`model_reasoning_effort` is `backend.effort`, `approval_policy` is
`runtime.mode`). `-c` is told the same: `-c model_reasoning_effort=high`
names `backend.effort`, and `-c permissions.tool=x` sends the table to the
file.

To hear when long work stops, name a program at the top of `config.toml`; it
is started when a turn ends, with a JSON description of the turn as its last
argument (`type`, `session-id`, `cwd`, `status`, `input-messages`,
`last-assistant-message`, as codex's `notify` sends), and when an approval
waits for you (`"type": "approval-requested"`, with the `tool`, what it would
do as `display`, and the `reason` it asks):

```toml
notify = ["python", "C:/tools/notify.py"]   # not a .bat or .cmd: cmd.exe would read the payload
```

Commands get only what a build needs from your environment (PATH, the proxy
and certificate variables, toolchain homes and the like), never a variable
whose name looks like a credential. To hand them more, as codex's
`[shell_environment_policy]` does:

```toml
[shell_environment_policy]
include = ["UV_*", "JAVA_TOOL_OPTIONS", "NPM_TOKEN"]  # a secret only by its exact name; then scrubbed
exclude = ["CONDA_*"]
set = { CI = "1" }                                     # plain values, never a secret
# inherit = "core"   # the default; "all" (still no secrets) or "none"
# include_only = ["PATH", "SYSTEMROOT", "UV_*"]       # keep only these
```

A long task does not simply run out of context: old tool output is masked
first, and when that is not enough the model summarises the work so far and the
conversation continues from the summary (`/compact` does it on request;
`auto_compact = false` under `[runtime]` in `config.toml` turns it off). It
starts once the context passes 80% of the window, or sooner with
`auto_compact_token_limit = 200000` under `[runtime]`, as codex's
`model_auto_compact_token_limit`.

What the model shows of its reasoning appears dim under "thinking" before it
acts: Claude's thinking summaries, the reasoning DeepSeek-style servers stream,
and -- with `reasoning_summary = "auto"` under `[backend]` -- a Responses
model's summaries (Chat Completions has none to ask for, so it refuses the
setting). `show_reasoning = false` under `[runtime]` hides it. A
Responses model's reasoning is also carried from one round to the next, as
codex carries it: asked for encrypted and sent back with its turn, since
nothing is stored on the server.
How hard the model reasons is `effort` under `[backend]`, on every protocol:
`none | minimal | low | medium | high | xhigh` for the OpenAI shapes (sent as
`reasoning.effort` or `reasoning_effort`), the levels above for Claude. Unset
sends none, and `/effort LEVEL` changes it mid-session. How long a GPT-5
model's answers are is `verbosity = "low" | "medium" | "high"` under
`[backend]`, as codex's `model_verbosity` (`-c verbosity=low` for one run);
Claude has no such setting, so it is refused there.

At the prompt, a pasted block -- a stack trace, a log, some code -- is sent as
one message, and so is what you type between two lines of three double quotes
(Ctrl+C inside discards it). Once the conversation has begun, a dim line above
the prompt says how much of the context window is left (yellow under 20%).
`!git log --oneline -5` runs a command in the workspace without asking -- it is
yours -- and the model is told what it printed with your next message.
`@src/app.py` (or `@"a path"`) in a message attaches that file, as `read_file`
would show it. Ctrl+C while the model answers or a tool runs stops that turn
and brings the prompt back, the conversation kept; at the prompt, Ctrl+C twice
in a row (or `/exit`) ends the session. A task that ends -- `finish`, a budget -- says how, and the prompt
comes back too: the session ends when you end it (exit code 13). When the session ends it says the tokens it used and the
`foundry resume` command that continues it.

Images: `foundry run -i shot.png "why is this button cut off?"` (or `exec -i`)
attaches a screenshot to the task, `/image PATH` attaches one to your next
message -- `/image` alone, the one on the clipboard (Win+Shift+S puts it there)
-- and the model can look at an image in the workspace itself with
`view_image`. PNG, JPEG, GIF and WebP up to 3.75 MB are sent as they are; for a
text-only model, set `images = false` under `[backend]`.

In a Python repository the model can ask for a map before it reads:
`repo_map` outlines the classes, functions and signatures of a file, or of a
directory with the most-imported files first, within about 3,000 tokens.

## Design points

- **One loop**: `while the model returns tool calls: policy -> execute -> feed
  back -> resample`. A provider adapter only translates between protocols; it
  does not own the loop, touch policy, or call tools.
- **A six-step policy pipeline**: breaker table -> pre_tool callback -> DENY ->
  ASK -> mode baseline -> ALLOW -> interactive approval (no answer means DENY).
  A DENY at any layer cannot be flipped by any ALLOW.
- **The evidence chain**: every verification claim in `finish` must cite a real
  command event whose exit code matches, or `completed` is downgraded to
  `partial`. An empty claim is valid disclosure; an invented one is not.
- **No sandbox, honest disclosure**: approval reduces mistakes, not malice. The
  boundaries and non-goals are in [docs/threat-model.md](docs/threat-model.md).
- **A dependency budget**: the runtime has only `rich` (5 pure-Python wheels in
  total). HTTP/SSE, DPAPI and Job Objects all go through the stdlib and ctypes
  -- `ssl.create_default_context()` trusts the Windows system certificate
  store, so a corporate MITM proxy needs no configuration.

## Code map

```text
src/foundry/core/     runtime (the one loop), policy, tools, backends, session, workspace, winapi
src/foundry/cli/      the terminal UI: event subscriber + approval UI + report
src/foundry/prompts/  versioned system prompts
tests/                1436 tests: golden scenarios, attack tables, breaker-table invariants
```

`foundry.core` imports neither `foundry.cli` nor any third-party package --
both are enforced by tests
([tests/test_architecture_config.py](tests/test_architecture_config.py)).

## Documentation

| Document | Contents |
|---|---|
| [docs/requirements.md](docs/requirements.md) | requirements v0.2 |
| [docs/design.md](docs/design.md) | the design: package structure, core interfaces, key mechanisms |
| [docs/threat-model.md](docs/threat-model.md) | what is protected, what is not, and where enforcement happens |
| [docs/roadmap.md](docs/roadmap.md) | milestones and status |
| [docs/decision-log.md](docs/decision-log.md) | 34 numbered decisions (including the overturned ones) |
| [docs/open-questions.md](docs/open-questions.md) | open questions |
| [docs/research/](docs/research/) | research notes, including the [first live endpoint check](docs/research/live-endpoint.md) |
| [demo/](demo/) | the skeleton in one runnable file, plus a notebook |
| [AGENT.md](AGENT.md) | how to work in this repository |
| [DELTA.md](DELTA.md) | how the current branch differs from main (on main, an empty template) |

## Open

The personal path is an OpenAI API key (ChatGPT login is confirmed blocked; the
evidence is in [docs/research/auth.md](docs/research/auth.md)). The corporate
Gateway's `responses` adapter is implemented and has contract tests, but **its
real protocol behaviour is unverified** -- the entry gate for M3 is obtaining a
tool-call streaming redaction fixture from the Gateway
([OQ-6](docs/open-questions.md)).
