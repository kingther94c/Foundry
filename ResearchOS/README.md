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
.venv\Scripts\python -m researchos evaluate --root cases --playbook playbook.json --ids d,e
```

A case is a directory `cases/<id>/` with `case.json` (the documents and the
company's identity terms) and `outcome.json` (the abnormal return), kept apart
so that loading a case never touches its answer. Training writes `masked.json`
(what the analyst sees), `mask_key.json` (the mapping, factor and year
offset), and `prediction.json` next to them.

Only `researchos/llm.py` talks to the API, and only through one method,
`json(system, prompt, schema, *, prefix=None)`; every other module takes its
agents as arguments, so the whole loop runs offline against fakes.

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

Review removed one thing: the loop called `load_outcome` through a helper
that checked two module globals so that its own test could patch either one.
That was production code bent around a test; the test now patches the name
the loop calls.
