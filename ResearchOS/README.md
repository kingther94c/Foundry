# ResearchOS

Trains an analyst agent to read a company's quarterly earnings -- the results
release, the call transcript, what the market expected -- and judge how the
stock reacts, **without knowing which company it is**. [SPEC.md](SPEC.md) is
the contract; this is the tour.

## One training step

1. **Mask.** Company names become "the Company", the ticker "TICKER", money is
   rescaled by a secret factor and years are shifted. A masker agent then
   proposes more redactions (products, people, places, distinctive figures,
   remarks about the share price) and a guesser agent tries to name the
   company; they repeat until the guesser fails or the round limit is hit.
   A case the guesser still recognizes is skipped: an analysis of it could
   lean on remembering the company rather than reading the documents.
2. **Analyze.** Seven perspectives -- growth, profitability, guidance, cash
   flow, management tone, expectations gap, risks -- each read the masked
   documents and give a probability that the stock beats the market on the
   first session that can react. The documents are one cached prefix shared by
   all seven calls. A judge weighs the views and gives its own probability.
3. **Seal.** The prediction is written to disk before the outcome is opened.
4. **Reveal and score.** Brier score for the learned-weight blend, the judge
   and every perspective.
5. **Reflect.** A reflector sees the prediction, the outcome and the masked
   documents, explains why the market moved as it did, and writes lessons that
   should generalize. Perspective weights move by multiplicative weights on
   each perspective's loss. Lessons that name the company are rejected.

`evaluate` runs steps 1-4 on held-out cases with the playbook frozen and
reports Brier scores for the learned blend, the judge, an equal-weight blend
and a coin flip.

## Use

```
python -m venv .venv
.venv\Scripts\python -m pip install -e ..\tools -e ".[dev]"
.venv\Scripts\python -m pytest -q

set ANTHROPIC_API_KEY=...
.venv\Scripts\python -m researchos train --root cases --playbook playbook.json --ids a,b,c
.venv\Scripts\python -m researchos evaluate --root cases --playbook playbook.json --ids d,e --out untrained.json
```

Every command prints its result as JSON, or writes it to a file with
`--out`. Every JSON file ResearchOS reads may be plain UTF-8, UTF-8 with a
byte order mark, or UTF-16 with one -- what Windows PowerShell 5.1 writes
with `>` and `Out-File`.

`compare` pairs two evaluation reports case by case and puts a bootstrap
interval on the difference in Brier score, since a handful of cases settles
little on its own (negative `mean_diff`: the second report did better):

```
.venv\Scripts\python -m researchos compare untrained.json trained.json --metric brier_weighted
.venv\Scripts\python -m researchos compare -a base1.json -a base2.json -b train1.json -b train2.json
```

Either side can be several files (`-a`/`-b`), each what `evaluate` or `train`
printed; a side's files are merged in the order given.

`blend` replays a rule for combining the perspectives (`uniform`, `median`,
`hedge` with `--eta`, or `leader`: the best perspective so far) walk-forward
over scored records, so rules can be compared without calling a model again:

```
.venv\Scripts\python -m researchos blend train.json eval.json --rule leader --out leader.json
```

Real cases come from `build`: it asks the API's server-side web search and
fetch to find the company's results release, the full call transcript and
pre-report consensus estimates, has an agent cut each document out of its
page, and computes the outcome from daily closes you supply (the stock's and
the market's, `[["2026-07-16", 1234.5], ...]`):

```
.venv\Scripts\python -m researchos build --root cases --spec case01.json --closes AAPL.json --market-closes SPY.json
```

where `case01.json` holds `case_id`, `company`, `ticker`, `aliases`,
`fiscal_period`, `report_date` and `timing` (`bmo` or `amc`).

A case is a directory `cases/<id>/` with `case.json` (the documents and the
company's identity terms) and `outcome.json` (the abnormal return), kept apart
so that loading a case never touches its answer. Training writes `masked.json`
(what the analyst sees), `mask_key.json` (the mapping, factor and year
offset), and `prediction.json` next to them.

Only `researchos/llm.py` talks to the API, and only through one method,
`json(system, prompt, schema, *, prefix=None)`; every other module takes its
agents as arguments, so the whole loop runs offline against fakes.

## Runs

[runs/2026-10-first-real-run](runs/2026-10-first-real-run/README.md): 15
large-cap Q2 2026 reports, all after the models' knowledge cutoff. Every mask
ended identifiable, ten cases trained the playbook, and five were held out.
Read its caveats before its numbers.

## How it was built

Foundry wrote the package from SPEC.md and FOUNDRY.md alone, in a fresh
workspace with a fresh `FOUNDRY_HOME`, with `tools` installed as a package
rather than visible as source. `tests/test_acceptance.py` was written
separately from the spec and kept outside the workspace; a reference
implementation was written by hand from the same spec.

| | Foundry | Hand-written reference |
|---|---|---|
| Acceptance tests | 38 / 38 | 38 / 38 |
| Own tests | 62 | -- |
| Agent rounds / cost | 22 / ~$1.87 | -- |

Run side by side on the same seeded inputs and scripted agents, the two agree
on scoring, weight updates, lesson filtering, name matching and the whole
train/evaluate loop. Where they differ, Foundry's choice was the better one:
it wraps API errors as `LLMError` (so the CLI exits 1 instead of crashing),
re-applies the identity guard before every guesser round rather than only at
the end, treats masker terms that differ only in case as duplicates, strips
whitespace around identity terms, and drops any earlier lesson that happens
to name the company being analyzed.

`researchos.sources` and `build` came second, the same way: Foundry got
the merged package plus a new SPEC.md section, in a fresh workspace.

| | Foundry | Hand-written reference |
|---|---|---|
| Acceptance tests (new + v1 regression) | 60 / 60 | 60 / 60 |
| Own tests | 23 | -- |
| Agent rounds / cost | 16 / ~$1.01 | -- |

The two agree on 3,000 random inputs to each of `event_date_for`,
`outcome_from_closes` and `select_span`. Foundry's version is again the more
careful one: it validates the case spec field by field, refuses empty span
markers (which would match anywhere), and does not ask for expectations when
no page was fetched, so the agent is never invited to supply figures from
memory. The run also surfaced a Foundry fix: the model ended an Add File
patch with `+*** End Patch`, and Foundry now accepts that end line.

Real pages then reshaped `select_span` twice. It first had the model quote
the document's first and last words and found them by exact search; a
model's copy of a page never reproduces its newlines, non-breaking spaces and
table pipes, so a release and a transcript that were on the page came back
missing. Matching on letters and digits only fixed those (Foundry made the
change in 6 rounds, ~$0.21, handling characters such as `İ` whose lowercase
is two characters, where the hand-written version crashed) -- but on nine of
the next sixteen cases the model could not quote the end of an 80,000-
character page at all, and on a release it stopped at the end of the
highlights. Now the page is shown with numbered lines and the model gives
line numbers, which it reads off rather than recalls; and since an investor-
relations page is often a stub linking to a PDF, every fetched page is cut
and the longest span kept. Foundry made that change too (11 rounds, ~$0.56)
and removed the marker matching it replaced; on 20,000 random inputs it
agrees with the hand-written version, except that it also rejects a boolean
where a line number should be.

The first real masks then showed that large companies cannot be masked:
after four rounds and some 200 redactions the guesser still named JPMorgan
and Apple at 0.97 and 0.99 -- segment structure and ratios survive any
renaming -- so every such case would have been skipped. Skipping exists so
the analyst cannot recall how the stock reacted; for an event after the
models' knowledge cutoff there is nothing to recall. `--knowledge-cutoff`
keeps identifiable cases whose event date is after it, and every scored
record says whether masking held. Foundry made the change in 13 rounds
(~$0.48).

Masking then turned out to be the most expensive step, and for large
companies a futile one, so it can now give up early: `--mask-give-up CONF`
ends masking, as identifiable, once the guesser names the company with at
least that confidence. The API key had run out of credit by then, so for this
change Foundry's model was a subagent behind a local relay that stands in for
the Messages API: Foundry's requests are written to disk, a fresh subagent
that sees only those requests writes the replies, and an audit of its tool
calls confirms it touched nothing else. Foundry made the change in 7 rounds,
passed all 76 acceptance tests, and agrees with the hand-written version on
4,000 scripted masking runs.

Comparing an untrained with a trained playbook on five held-out cases gave a
0.004 difference in Brier score, which says nothing without a sense of its
noise, so `compare` now pairs two reports case by case and bootstraps the
mean difference. Foundry made it through the relay in 10 rounds, passed all
92 acceptance tests, and agrees with the hand-written version on 9,000 random
report pairs -- except that it skips a NaN or infinite score instead of
letting it poison the means, which is the better choice.

The model playing Foundry pointed out, while building `compare`, that its
reports would be saved on Windows with PowerShell 5.1, whose `>` writes
UTF-16 and whose `Out-File -Encoding utf8` adds a byte order mark -- files
`compare` refused. Now every JSON file goes through `researchos.jsonio`,
which decodes by the byte order mark, and the commands take `--out FILE`.
Foundry made the change through the relay in 19 rounds, passed all 112
acceptance tests, agrees with the hand-written version on 6,000 fuzzed
files, and went one step further than it: the four places that each wrote
JSON their own way now share `write_json`. It also rewrote the one existing
test the new rule contradicted, instead of deleting it.

Comparing walk-forward training with the untrained playbook then took a
script: `compare` read one `evaluate` report a side, and walk-forward
training writes one `train` output per case. `compare` now reads training
output and merges several files a side (`-a`/`-b`). Foundry made it in 11
rounds, passed all 135 acceptance tests and agrees with the hand-written
version on 5,000 random report sets. Its review of the argument parsing
caught what the hand-written version broke: with a variable-length
positional list, `compare A.json --seed 5 B.json` -- valid until then --
became an error. Foundry kept it working.

That comparison then exposed a look-ahead in training itself: `train` took
cases in the order given, so a forecast could use a lesson drawn from a
stock that reacted the same day or later. Now `train` walks forward in
event-date order, forecasts every case of one event date before opening any
of their outcomes, and `train` and `evaluate` refuse a case dated on or
before the last reaction the playbook learned from (`learned_through`), or
one without an ISO event date. Foundry made it in 18 rounds and passed all
146 acceptance tests. It found the two existing acceptance tests the new
rule broke (each evaluated a case dated the same day as the one it trained
on) and fixed their data rather than the rule. On 400 random training and
evaluation sequences it agrees with the hand-written version -- once that
version was fixed: it accepted an event date with stray spaces when
training and then could not read it back from the playbook, which
Foundry's version handled.

On real cases the learned weights barely moved, which raised the question
of how the perspectives should be combined at all. `researchos.blend`
replays combination rules walk-forward over records already scored, and
records now keep each perspective's probability. Foundry made it in 15
rounds, passed all 166 acceptance tests and agrees with the hand-written
version on 4,000 random record sets; it also checks more than that
version does (an `actual_up` that is not a boolean, a probability outside
[0, 1]).

Five versions had left private copies of two checks -- is this a finite
number, is this an ISO date -- in five places, each slightly different.
They now live in `tools` (`stats.is_number`, `dates.parse_iso_date`, built
by Foundry first) and ResearchOS uses them: Foundry made the change in 14
rounds, 25 lines fewer, with all 169 acceptance tests passing and the same
results as before on 13,400 fuzzed calls.

Review of the first version removed one thing: the loop called `load_outcome` through a helper
that checked two module globals so that its own test could patch either one.
That was production code bent around a test; the test now patches the name
the loop calls.
