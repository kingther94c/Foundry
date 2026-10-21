# Perspectives built to disagree: plan, fixed before any forecast is made

Written and committed before either arm below is run. No forecast in
this run has been made when this is written.

## Why

On the 21 cases of `2026-10-smallbank-run` the seven perspectives'
probabilities correlated at 0.86 on average (v19's `agreement`), and every
rule for combining them scored the same. Seven views that move together are
one view: before any weighting can help, the views have to disagree. v20
adds an "isolated" perspective mode: each perspective judges how its factor
alone would move the stock if everything else in the report had come in as
the market expected, and the views are pooled by adding their log-odds
rather than averaged. This run asks one question: do isolated views
disagree more than holistic ones on the same documents?

## Cases and arms

- The same 21 cases (sixteen small and mid caps, five small banks; Q2 2026
  results, every event date after the models' knowledge cutoff of
  2026-06-30), each with the abstraction mask already saved for it in that
  run (`masked.json`); nothing is masked again and no masking call is made.
- Arm H: `researchos evaluate --perspective-mode holistic`; arm I: the same
  with `--perspective-mode isolated`. Both with the initial playbook (equal
  weights, no lessons), `--knowledge-cutoff 2026-06-30`, `--mask-abstract
  --mask-history-years 3`, seed 0, the code at the commit that merges v20.
  Each arm runs on its own copy of the cases (case, mask and outcome files
  only), so neither sees the other's forecasts. `evaluate` seals each
  forecast before it opens the outcome; no playbook is trained.

## Primary endpoint (uses no outcome)

The mean pairwise correlation of the seven perspectives' probabilities
across the 21 cases, as `researchos agreement` reports it, in arm I against
arm H. The difference (I minus H) gets a 95% percentile bootstrap interval:
2,000 resamples of the 21 cases with replacement (`random.Random(0)`), the
same resampled cases for both arms, each arm's mean over the pairs whose
correlation is defined in that resample.

Isolated views count as disagreeing more if the interval lies below zero.

Alongside it, so that low correlation is not mistaken for disagreement when
it is only silence: each perspective's standard deviation and how often it
leaves 0.50 (`sd_prob`, `calls`), in both arms. A perspective that never
leaves 0.50 has no correlation; it is named as uninformative, and its pairs
are left out as `agreement` leaves them out.

## Secondary (outcomes; exploratory)

These 21 outcomes were opened in the small-bank run and their aggregate
results are known; the isolated mode was designed from the correlation
alone, without looking at any case's forecast or outcome. With 21 cases
none of these can show a difference of plausible size, and they are
reported as underpowered whatever they show:

1. The judge's Brier score, I against H (`researchos compare --metric
   brier_judge`, paired bootstrap).
2. The combined forecast's Brier score: arm I's pooled forecast against arm
   H's weighted mean (`compare --metric brier_weighted`).
3. Every `blend` rule (uniform, median, hedge, leader, pool) replayed on each
   arm, and each perspective's Brier score.

Also reported, without outcomes: how much a forecast moves when the same
case is run again. Eight of the cases were evaluated with the same playbook,
masks and mode in the small-bank run; the correlation of the judge's
probabilities (and each perspective's) between that run and arm H is the
noise floor any difference above has to clear.

## Cost

About $0.22 a case per arm (seven perspective calls and a judge, the
documents cached), about $9.5 for both. Each arm is capped at $7.50; an arm
that reaches its cap stops, and only the cases it finished are reported.
