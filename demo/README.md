# demo -- understand Foundry from one file

`mini_foundry.py` is Foundry's **skeleton**: exactly the same structure as the
real thing, with every layer shaved down to its thinnest form. No network, no
API key -- just run it.

```bash
python demo/mini_foundry.py --yes
```

Two ways in, depending on how you like to read:

| | Good for |
|---|---|
| **`mini_foundry.py`** | running it start to finish for the whole picture; reading the code top to bottom |
| **`mini_foundry.ipynb`** | running a cell at a time, seeing the state in between, changing something and re-running. The notebook **imports** from the `.py` rather than copying code, so the two can never disagree |

```bash
jupyter lab demo/mini_foundry.ipynb      # or just open it in VS Code
```

## Using a real model (optional)

Both entry points can talk to an **OpenAI-compatible
`/v1/chat/completions`**:

```bash
python demo/mini_foundry.py --endpoint http://127.0.0.1:1234/v1 --model your-model --yes
```

In the notebook, set `USE_API` to `True` in section 5.

| Source | ENDPOINT | key |
|---|---|---|
| LM Studio | `http://127.0.0.1:1234/v1` | anything |
| Ollama | `http://127.0.0.1:11434/v1` | anything |
| local OpenClaw gateway | `http://127.0.0.1:18789/v1` | `gateway.auth.token` in `~/.openclaw/openclaw.json` |
| OpenAI | `https://api.openai.com/v1` | your key |

**Two things to know first**: a real agent round can take tens of seconds to
several minutes; and if that model does not emit `tool_calls` (the
trade-advisor family behind the local OpenClaw gateway does not), the loop ends
on the first round -- you see a real HTTP round trip, but no multi-round tool
calling. For the full loop, use script mode, or switch to a model that supports
function calling.

## Hold on to this one sentence

The entire system is one sentence:

```
while the model keeps asking for tools:
    policy decides -> execute -> feed the result back -> ask again
```

**Every other line of code exists to put a guard around one word in that
sentence.** If you get lost reading `src/foundry/`, come back to it and ask
"which word is the part I'm reading protecting?"

## Four scripts, four things

The model is hard-coded into a script (a real backend sends HTTP at that spot).
That makes the results deterministic and free -- and **it is exactly why
Foundry's 1436 tests can run on a machine with no network and no credentials**.

| Command | What it shows |
|---|---|
| `--yes` | a full run: run the tests -> read the code -> fix -> re-run -> finish with evidence. Exit code 0 |
| `--script destructive --yes` | the breaker table denies at step 0, and `--yes` cannot approve it |
| `--script liar --yes` | the model claims "all tests pass"; the gate finds the command it cited exited 1, and downgrades to partial |
| `--mode plan --no` | mode baseline: in plan mode every change is denied |

`liar` is worth a second look: the model did not forge the citation -- the
command it cited **really ran**, it just failed. What stops it is not "lie
detection"; it is **requiring it to point at evidence, and then going and
checking that evidence ourselves**.

## Where the six parts map to

| Section in the demo | Real Foundry | What the real one adds |
|---|---|---|
| 1. IR | `core/conversation.py` | Usage accounting, capability negotiation. `arguments` is likewise **deliberately not parsed early** -- models emit broken JSON, and parsing early would make "report that this call was malformed" impossible |
| 2. Tools | `core/tools/` | Nine tools. `read_file` keeps a digest so "read before you edit" can be enforced; `apply_patch` uses anchored search/replace and is **atomic per file**; `run_command` uses a Job Object so the whole child process tree can be killed, and filters the environment through an allowlist |
| 3. Policy | `core/policy/` | The six-step pipeline plus the command segmenter. The breaker table has to cope with every spelling of the same command |
| 4. Session | `core/session.py` | Content-addressed artifacts (large output never enters the conversation), credential redaction, and a degraded path so a failed ledger write cannot take the turn down |
| 5. Backend | `core/backends/` | Two adapters, Chat Completions and Responses, over a stdlib HTTP/SSE client |
| 6. Loop | `core/runtime.py` | Budget ceilings, cancellation, re-fetching expired credentials, an error taxonomy, context window management. The **shape is identical** |

## Three design choices worth understanding on their own

### Why validate must run before policy

If a malformed call pops the approval prompt first, the user approves and only
then does anyone discover the arguments were wrong -- one wasted question. So
the order is: validate, then decide, then execute.

### Why "what is shown" and "what is run" must be the same object

`Operation` is what policy judges, what the approval prompt displays, and what
the executor runs. All three hold the same object. If what is shown and what is
run could differ, the user did not approve what actually happened -- and the
approval was theatre.

### Why the breaker table is step 0 rather than "a rule with very high priority"

The rule table is configurable. The repository's `.foundry/config.toml`, the
user's own config, and hooks can all add to it. The breaker cannot be one of
those entries, because **anything configurable is something that can be
configured around**. It has to sit outside the pipeline, before everything
else.

The real Foundry has a structural-invariant test for exactly this: 780
generated combinations, asserting that no decoration, mode, session grant or
hook rewrite can make a forbidden command approvable. That test exists because
**four rounds of adversarial review each broke the command segmenter**, and
twice the break quietly downgraded an unapprovable DENY into an approvable ASK.

## Two real potholes left in the demo on purpose

Reading the code you will find two comments explaining "why the extra lines".
Both are real:

1. **`sys.stdout.reconfigure(encoding="utf-8")`** -- the Windows console is not
   UTF-8 by default, so printing one box character raises
   `UnicodeEncodeError`. Platform detail is not busywork for a tool like this
   -- it is the job.

2. **`PYTHONDONTWRITEBYTECODE=1`** -- when tests are re-run right after a file
   is edited, if the new and old file have the same size and their mtimes land
   in the same clock tick, Python decides the `.pyc` in `__pycache__` is still
   valid and **runs the pre-edit code**. The patch was right, the tests are
   still red -- and the agent falls into an edit-and-edit-again loop. This one
   was hit for real while writing this demo.

## What the demo does **not** have

These are where Foundry actually spends its effort; they were cut so the
skeleton stays visible:

- streaming output (the demo waits for the model to finish; the real one prints
  token by token, and has to redact a credential split across two chunks)
- path safety (the demo only compares prefixes; the real one handles 8.3 short
  names, reparse points, device names, UNC, case)
- command segmentation (the demo uses `in` for a substring match, which is
  **nowhere near enough in the real world**)
- context window management, budget ceilings, cancellation, re-fetching expired
  credentials
- an error taxonomy (what to retry, what to stop on, how to read `Retry-After`)
- anchored patch matching and atomicity (the demo overwrites whole files)

One last note: the `git reset --hard` in `--script destructive` is stopped by
the substring match `"git reset --hard" in target`. In the real world it can be
written `git reset ,--hard`, hidden inside a `<# ... #>` comment, or wrapped in
the `&` call operator -- each of which walks straight past a substring match.
**That is the entire reason those 443 lines of segmenter exist.**
