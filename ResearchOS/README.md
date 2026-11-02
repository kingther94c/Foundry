# ResearchOS

Trains an analyst agent on questions whose answers it cannot remember. Two
kinds:

- **Earnings.** Read a company's quarterly earnings -- the results release,
  the call transcript, what the market expected -- and judge how the stock
  reacts, **without knowing which company it is**.
- **Macro.** Read the markets and the economy as of a Friday's close -- ten
  targets (Treasury yields and the curve, the dollar, the S&P 500, oil,
  gold, the high-yield spread, breakevens, VIX), the economic series as
  they were published by then, futures positioning, the Fed's latest
  statement and minutes -- and judge whether each target is higher a week,
  a month and three months later. Nothing is hidden; every snapshot is
  dated after the model's knowledge cutoff instead.

[SPEC.md](SPEC.md) is the contract; this is the tour.

## One training step (earnings)

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

Or run it from a clone with nothing of this repository installed: install
only `anthropic` and `pytest`, and use `python run-researchos.py` in place
of `python -m researchos`. It puts this folder and `../tools` first on the
path, and pytest finds `../tools` by itself.

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
`hedge` with `--eta`, `leader`: the best perspective so far, or `pool`: their
log-odds added) walk-forward over scored records, so rules can be compared
without calling a model again:

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

## Macro

A macro case is a snapshot, `macro-<date>`: what was known at that date's
close, and a target key per target and horizon (`DGS10_1m`). Nothing is
masked, since the levels are the information. The guard against memory
is the date: every command that calls a model refuses a snapshot dated on
or before `--knowledge-cutoff`, before any call.

```
.venv\Scripts\python -m researchos fetch-series --source fred --id DGS10 --start 2025-01-01 --file series\DGS10.json
.venv\Scripts\python -m researchos fetch-series --source fred --id PAYEMS --vintage 2026-10-30 --file series\PAYEMS@2026-10-30.json
.venv\Scripts\python -m researchos fetch-series --source cftc --id 043602 --file series\COT_043602_net.json --oi-file series\COT_043602_oi.json
.venv\Scripts\python -m researchos snapshot --root cases --config macro-config.json --as-of 2026-10-30 --series-dir series
.venv\Scripts\python -m researchos forecast --root cases --playbook macro.json --ids macro-2026-10-30 --knowledge-cutoff 2026-06-30
.venv\Scripts\python -m researchos resolve --root cases --ids macro-2026-10-30 --series-dir series
.venv\Scripts\python -m researchos score --root cases --ids macro-2026-10-30 --out scored.json
```

- `fetch-series` stores a FRED series, or an ALFRED vintage (the series as
  it was published on a date), or a futures contract's Commitments of
  Traders from the CFTC. None of them needs an account or a key.
- `snapshot` builds one date's case from a configuration that serves every
  snapshot of a run: the targets and horizons, the economic series, the
  positioning, the official texts with the dates they were published, and
  the calendar. It uses only what was known on the date: every series cut
  at it (an economic series as published by then, when its vintage is
  stored), positioning only once the report was public, the latest text
  of each kind published by then (of a fetched page, only its main
  content), and the events scheduled after it.
  Building it twice gives the same bytes.
- `forecast` has ten macro perspectives and a judge each give a probability
  for every key, one call each, and seals the forecast. It opens no
  outcome, and a forecast is sealed once.
- `resolve`, once a horizon has ended, records how far each target moved
  from the level the snapshot shows. It never opens the forecast.
- `score` scores the sealed forecasts against what has resolved, one
  record per key (`macro-2026-10-30/DGS10_1m`), lists what is still
  pending and calls no model. `compare`, and `blend` and `agreement` with
  `--perspectives macro`, read its output.
- `train` walks forward by resolution date: the forecasts of a date learn
  only from keys that resolved before it, and a key is learned only once
  its own snapshot is sealed. Every key it is given must have resolved.
  `evaluate` refuses a macro case; `forecast` and `score` do its work.

## Runs

[runs/2026-10-first-real-run](runs/2026-10-first-real-run/README.md): 15
large-cap Q2 2026 reports, all after the models' knowledge cutoff. Every mask
ended identifiable, ten cases trained the playbook, and five were held out.
Read its caveats before its numbers.

[runs/2026-10-smallcap-run](runs/2026-10-smallcap-run/README.md): 16 small
and mid caps, pre-registered. Masking them exposed five leaks in the
pipeline, all fixed (v15-v18); after the fixes redaction still hid none of
eleven cases and abstraction two of eleven, so there was no training. Small
banks come close to hidden; a company with a business of its own does not.

[runs/2026-10-smallbank-run](runs/2026-10-smallbank-run/README.md): five
more banks, two hidden from two guessers in a row; then the whole loop on
all 21 post-cutoff cases (13 trained walk-forward, 8 held out). The trained
playbook scored like the untrained one (Brier 0.189 against 0.193, interval
-0.023 to +0.011): the loop runs end to end; eight cases cannot show it
learning.

[runs/2026-10-perspectives-run](runs/2026-10-perspectives-run/README.md):
the same 21 cases, holistic against isolated perspectives (v20),
pre-registered. Isolated views disagree: their mean correlation fell from
0.85 to 0.39 (interval -0.59 to -0.37 for the difference), and on them the
combination rule starts to matter, pooling log-odds beating averaging.

[runs/2026-11-macro-calibration-run](runs/2026-11-macro-calibration-run/README.md):
the first macro run, pre-registered: eighteen Friday snapshots from
2026-07-03 to 2026-10-30, holistic against isolated perspectives.
- Isolated macro views disagree as the earnings ones did: a mean
  correlation of 0.05 against 0.65 (interval -0.66 to -0.55 for the
  difference).
- On the 330 keys resolved so far, neither arm beats a coin. Eighteen
  snapshots of one summer could not show that either way.
- Two more scorings are fixed, for 2026-12-07 and 2027-02-08.

## How it was built

Foundry wrote every version from SPEC.md and FOUNDRY.md alone, against
acceptance tests it never saw; [HISTORY.md](HISTORY.md) is the record,
version by version.
