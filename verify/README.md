# verify/ -- Foundry used as a user uses it

The suite under `tests/` plays the model from recorded replies, one function
at a time. This folder holds the other half of how Foundry is checked: whole
tasks, run through the command line, against a model that answers for itself
-- an agent standing in for one offline, or a real endpoint.

Nothing here is collected by pytest or built into the wheel. The kit is
itself checked: [tests/test_verify_kit.py](../tests/test_verify_kit.py) and
[tests/test_verify_probes.py](../tests/test_verify_probes.py) are part of the
suite, and CI runs the smoke scenarios (the `verify-smoke` job).

## What is here

| File | What it is |
|---|---|
| `relay.py` | a local endpoint that speaks the three protocols Foundry has adapters for, streaming or not, and answers each request with a reply written from outside; it injects failures by request number (429, 500, quota, overloaded, a cut stream, an error event) |
| `run.py` | runs a scenario: a copy of a small repository, a fresh home, one or more `foundry` invocations, then a summary and a verdict |
| `scenarios/` | the scenarios: `smoke-*` for the relay itself, `s1` to `s22` for the rounds the roadmap calls P1 and P9 to P11 |
| `autoreply.py` | a scripted model with one thing to say (read the README, then finish): enough for the smoke scenarios |
| `live.py` | what a run against a real endpoint needs: where the key comes from, and what the run cost |
| `live_batch.py` | a set of scenarios against a real endpoint, one after another |
| `show.py` | what each scenario of a live batch did |
| `probes/` | small projects that each aim at one thing an unattended run must do, and versions of ResearchOS rebuilt from history, all with tests Foundry never sees |
| `mutate.py` | the mutation check every change's tests go through |
| `mcp_client.py`, `tracker_mcp.py`, `notify_log.py` | what some scenarios start beside Foundry: an MCP client, an MCP server over HTTP, a notify program |
| `audit_agent.py` | lists the tool calls of the agent that played the model, from its transcript |

## Offline, the model played from outside

```bash
python verify/run.py verify/scenarios/s1-chat-fix.json
```

The runner prints `READY relay dir: DIR` and waits. Whoever plays the model
-- in practice an agent told to do nothing else -- answers each request:

```bash
python verify/relay.py next DIR
python verify/relay.py reply DIR 1 reply.json
```

`next` prints only what is new in the request: the tools offered, the
settings, the latest messages. A reply is `{"content": "...", "tool_calls":
[{"name": "...", "arguments": {...}}], "reasoning": "..."}`. The player sees
what a model would see and nothing else; `audit_agent.py` shows afterwards
that it read and ran nothing but the relay. What the player never sees, and
the runner reports, is whether the opaque reasoning the relay handed out came
back, which requests were retried after an injected failure, and whether the
canary credential turned up in any file or request body.

## Offline, the smoke run

```bash
python verify/run.py --auto verify/scenarios/smoke-openai_compat.json verify/scenarios/smoke-responses.json verify/scenarios/smoke-anthropic_messages.json
```

`autoreply.py` plays the model. Each scenario takes Foundry through a task
on one protocol, past a 429, a cut stream and an error event. The exit status
is 1 if any verdict is FAIL.

## Against a real endpoint

A live run spends money on your account, so nothing starts one for you: run
these yourself, in your own terminal.

```bash
python verify/live_batch.py --set p1 --model claude-sonnet-5-5 --budget 2
```

```bash
python verify/live_batch.py --set p1 --chat --budget 2
```

The first is over Anthropic Messages; `--chat` is over Chat Completions, on
Anthropic's OpenAI-compatible endpoint. P1's ten scenarios cost about US$0.53
on claude-sonnet-5-5 when they were first run ([D-185](../docs/decision-log.md)).

The key is taken from `FOUNDRY_VERIFY_KEY` if that is set, else from what
`foundry login` stored for Anthropic, read in the process you started, else
typed at a prompt that shows nothing. It reaches Foundry in an environment
variable that its temporary home's configuration names (`backend.env_key`),
which Foundry keeps from every command it runs. It is copied to no file, and
each run's folder is searched for it afterwards: a verdict of FAIL names the
file if it is ever found.

The cost printed is an estimate from the tokens Foundry's journals recorded
and the prices in `live.py`. It leaves out the premium on tokens written to
the cache, so the bill is somewhat higher.

## The probes

```bash
python verify/probes/run_auto.py baseline --model claude-haiku-5-5 --repeat 3 --budget 3
```

Against a local agent gateway instead -- OpenClaw's, say, whose agents
answer on Chat Completions or on Responses -- `--gateway` names its base URL
and `--model` one of its agents:

```bash
python verify/probes/run_auto.py gw --gateway http://127.0.0.1:18789/v1 --model openclaw/foundry --protocol openai_compat
```

The token is `FOUNDRY_VERIFY_KEY` or typed at the prompt, never the Anthropic
key `foundry login` stored; each run says how many requests it made rather
than what it cost ([D-208](../docs/decision-log.md)).

Each project is a scaffold, a task, the commands a rule allows, and hidden
tests. `foundry exec` gets the first three and is asked nothing; the hidden
tests then judge what it left. `make_projects.py` writes the first set (a
package from a spec, two functions of forty changed, a bug beside a decoy, a
function moved across modules, files with a byte order mark and CRLF, five
steps under a small compaction limit) and `make_projects_b.py` the second
(the whole of a checklist, scope, a vague report to reproduce, a request
nobody can clarify, a project whose plain `pytest` cannot run).
`check_b.py` shows that the second set's hidden tests fail on the scaffold
and pass on a reference fix, and exits 1 if either is not so. All eleven cost
about US$0.79 on claude-sonnet-5-5 ([D-192](../docs/decision-log.md)).

```bash
python verify/probes/make_projects_c.py verify/probes/projects-c
```

```bash
python verify/probes/run_auto.py replay --projects verify/probes/projects-c --model claude-haiku-5-5 --repeat 3 --budget 3
```

The third set is rebuilt from this repository's history
([D-201](../docs/decision-log.md)): three versions of ResearchOS that Foundry
built -- v24, v30 and v32 -- each the tree before it with its SPEC section
appended, `tools` beside it, and the acceptance suite that judged it hidden.
The rounds and cost each took then are in
[foundry-acceptance.md](../docs/foundry-acceptance.md). It needs a clone
with that history, and the Anthropic SDK installed, since one of
ResearchOS's own tests imports it: `run_auto.py` skips a project whose
`requires.txt` names a module that is not there. `check_c.py` shows that
each suite fails on the tree before the version and passes on the tree it
landed in.

```bash
python verify/probes/make_projects_d.py verify/probes/projects-d
```

The fourth set is harder than the first two, which a GPT model behind a
gateway nearly always passed ([D-211](../docs/decision-log.md)): money moved
from floats to whole cents across ten modules, with old files still loading;
thirteen rules in a spec whose visible tests pass long before it is done; a
stale build whose cause is a fingerprint that loses boundaries, next to a
known failure that is someone else's; and seven steps against a format
document under a small compaction limit. Each project carries a reference
solution, `reference/`, and is judged by its hidden tests alone, one of which
runs the project's own tests. `check_c.py` takes this set too.

A change is compared with what came before it by running the same projects
on both: `VERIFY_FOUNDRY` names another checkout -- a `git archive` of the
commit before -- whose `src/` is run instead of this one's.

## The mutation check

```bash
python verify/mutate.py mutations.json tests/test_feature.py
```

`mutations.json` lists small breakages of the change, each `{"name", "file",
"old", "new"}`. A mutation whose tests still pass survived, which means no
test checks what it broke. The tests must pass before any mutation, and each
file is put back byte for byte.

## What is never committed

Run output, logs, results, generated projects: `verify/out/`,
`verify/probes/projects/`, `verify/probes/projects-c/` and `verify/probes/projects-d/` are ignored. Nothing recorded from a real endpoint
goes into the repository unless it has been read through first for anything
shaped like a credential, and `tests/test_verify_kit.py` looks again.
