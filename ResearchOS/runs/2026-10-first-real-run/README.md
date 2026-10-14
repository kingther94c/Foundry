# First real run: Q2 2026 earnings, July to September 2026

Seventeen large-cap reports, all published after the models' knowledge
cutoff (June 2026), so no model in the loop can have seen how any of these
stocks reacted. Built with `researchos.sources`, trained and scored with
`researchos.loop`, every role played by the package's default model.

What is here: `cases.json` (each case's identity, dates, document sources and
sizes, the outcome, how masking went, and the sealed prediction),
`playbook.json` (the trained playbook: weights, 62 lessons, history),
`train.json` and `eval_baseline.json` (the loop's output and token usage),
`eval_subagent_*.json` (the held-out comparison, see Results),
`walkforward_*.json` (the 15-case comparisons, see Walk-forward).
The documents themselves are not checked in: they are third-party pages.

## Data

- **Documents**: for each report, the results release (mostly the 8-K
  exhibit on sec.gov), a full call transcript, and pre-report consensus
  extracted from earnings previews -- found and fetched through the API's
  server-side web search and fetch, and cut out of their pages by line
  number.
- **Outcomes**: daily closes for each stock and for SPY; the abnormal return
  is the stock's return minus SPY's on the first session that could react
  (the next session for after-close reports).
- Intel and Micron were dropped: no full call transcript could be fetched.
  That leaves 15 cases: the ten earliest for training (July 14-29), the five
  latest held out (July 30 to September 24).

## Masking

Every one of the 15 masks ended identifiable. After four masker rounds and
200 to 400 redactions per case, the guesser still named the company at 0.97
or above. Renaming does not hide a megacap: its segment structure and its
ratios identify it. These cases were used anyway (`--knowledge-cutoff
2026-06-30`). Recognizing the company gives the analyst priors but not the
answer, because the reactions are after its cutoff. Blind training proper
needs smaller, less-followed companies.

## Results

Each training forecast was sealed before its outcome was opened, using only
the lessons from cases trained before it. **But the cases were trained in
case-id order, which is not quite event order**, so four forecasts used
lessons from reactions that had not happened yet or happened the same day:
case03 (reacted July 16) learned from case02 (July 17), case05 and case10
each from a case that reacted the same day, and case11 (July 29) from two
cases that reacted July 30. The lessons are general and name no company,
but that is still a look-ahead, and those four cases scored best: Brier
(judge) 0.111 and 4 / 4 right, against 0.200 and 5 / 6 for the other six.
The table below keeps them; read its training rows as optimistic.
`researchos` now refuses this (see Walk-forward).

| | n | up | Brier (weighted) | Brier (judge) | Brier (coin) | Accuracy (judge) |
|---|---|---|---|---|---|---|
| Training, in order | 10 | 5 | 0.174 | 0.164 | 0.250 | 9 / 10 |
| -- first five | 5 | 2 | 0.180 | 0.192 | 0.250 | 4 / 5 |
| -- last five | 5 | 3 | 0.168 | 0.137 | 0.250 | 5 / 5 |
| Held out, untrained playbook | 5 | 3 | 0.260 | 0.254 | 0.250 | 2 / 5 |

**Do not read a trend into this.** The first five training cases were
forecast with an almost empty playbook and still scored 4 / 5, and the
untrained analyst scored 2 / 5 on the held-out cases. With five cases a
side, case-to-case variance swamps any effect of training.

The comparison that isolates the playbook -- the same five held-out cases,
the same masks, untrained against trained -- ran after the API key had run
out of credit, so both arms used a stand-in for the model: one fresh subagent
per case per arm behind a local relay that answers in place of the Messages
API, seeing only the requests (an audit of every subagent's tool calls found
nothing touched outside its own relay directory). Both arms ran the same
way, so they compare with each other, not with the API rows above.

| Held out, subagent model | Brier (judge) | Brier (weighted) | Accuracy |
|---|---|---|---|
| Untrained playbook | 0.239 | 0.237 | 3 / 5 |
| Trained playbook | 0.235 | 0.236 | 3 / 5 |

The trained playbook is better on two cases and worse on three; the
difference (0.004) is noise, and both arms sit near a coin flip (0.25). On
this evidence training neither helps nor hurts. Settling it needs many more
cases -- and smaller companies, which masking can actually hide.
`eval_subagent_untrained.json` and `eval_subagent_trained.json` hold the
per-case scores.

## Walk-forward

Fifteen paired cases instead of five: the ten training cases were also
forecast with the untrained playbook (`evaluate`, one case at a time) and
trained again with the playbook carried forward, the subagent model in both
arms, and paired with the five held-out cases. The first training run kept
the case-id order, look-ahead included (`walkforward_trained.json`):

```
python -m researchos compare -a walkforward_untrained.json -a eval_subagent_untrained.json -b walkforward_trained.json -b eval_subagent_trained.json
```

| Cases | n | Brier (judge), untrained -> trained | difference, 95% CI | trained better / worse / tied |
|---|---|---|---|---|
| All | 15 | 0.200 -> 0.189 | -0.011 [-0.024, +0.002] | 9 / 4 / 2 |
| Without the four look-ahead cases | 11 | 0.220 -> 0.212 | -0.008 [-0.024, +0.008] | 6 / 4 / 1 |

Then, with the look-ahead fixed, the training arm was run again in event
order (`walkforward_ordered_trained.json`): one `train` call per event date
-- seven batches, the two-case dates forecast in full before either outcome
was opened -- and a fresh subagent per batch, so no subagent saw an outcome
before it had forecast. This is the clean comparison:

```
python -m researchos compare -a walkforward_untrained.json -a eval_subagent_untrained.json -b walkforward_ordered_trained.json -b eval_subagent_trained.json
```

| Cases, event-ordered training | n | Brier (judge), untrained -> trained | difference, 95% CI | trained better / worse / tied |
|---|---|---|---|---|
| All | 15 | 0.200 -> 0.192 | -0.008 [-0.021, +0.006] | 9 / 5 / 1 |
| Training cases only | 10 | 0.180 -> 0.171 | -0.009 [-0.021, +0.005] | 7 / 2 / 1 |

The look-ahead had flattered exactly where it was expected to: forecast
without the later lessons, case03 scores 0.102 instead of 0.073 and case05
0.160 instead of 0.137.

With the learned blend (`--metric brier_weighted`) the difference is
-0.001 [-0.009, +0.008] on the clean 15 (-0.002 in the first run):
the weights barely moved from uniform, so they change little. The judge,
which reads the lessons, does a little better, but three of the four
look-ahead cases are among its larger gains (case05 -0.039, case03 -0.029),
and without them the interval is centered near zero; the clean re-run
confirms it. **On this evidence training has no measurable effect**: the
judge's point estimate leans slightly toward the trained playbook, and the
interval includes zero. The intervals come from resampling cases
(10,000 bootstrap resamples); they do not count the noise of the model itself,
which on case01 -- the same initial playbook in both arms -- moved the judge's
Brier by 0.009.

The fix, now in the package: `train` takes cases in event order,
forecasts every case of the same event date before opening any of their
outcomes, and `train` and `evaluate` refuse a case dated on or before the
last reaction the playbook learned from. `walkforward_playbook.json` and
`walkforward_ordered_playbook.json` are the playbooks the two walk-forward
runs ended with.

What did come out of training: weights moved little from uniform
(expectations_gap 0.156 and management_tone 0.152 at the top, growth 0.133 at
the bottom). The 62 lessons are concrete and general -- for example, "when
the same facts drive the bearish case in most lenses, treat them as one piece
of evidence, not several", or "if the Street already modeled an EPS decline,
a small beat with a soft guide is a met lowered bar, not a fresh positive".
None names a company or product.

## Cost

At Opus-class list prices: masking about $28 (15 cases, about $1.84 each),
training about $11 (10 cases), and the baseline evaluation about $3.
Building the cases took 9.1 million Sonnet-class input tokens, 172 searches
and 273 fetches across two passes; the first pass was lost to the cutter that
the line-number change replaced.

Masking is the expensive step, because each round re-sends documents that
differ from the last round's, so nothing is cached. On megacaps it bought
nothing: every mask ended identifiable. Analysis is cheap by comparison,
because the seven perspectives share one cached copy of the documents.
