# Perspectives built to disagree

The plan ([PLAN.md](PLAN.md)) was committed before either arm made a
forecast. Per-case data: [eval_holistic.json](eval_holistic.json),
[eval_isolated.json](eval_isolated.json); the primary endpoint and the
noise floor ([primary.json](primary.json), reproduced by
[primary.py](primary.py) from the files here); the agreement reports
([holistic](agreement_holistic.json), [isolated](agreement_isolated.json));
the Brier comparisons ([judge](compare_judge.json),
[combined forecast](compare_weighted.json)); and the combination rules
replayed on each arm ([blend.json](blend.json)).

**Result.** Asked how their factor alone would move the stock, the seven
perspectives disagree: on the same 21 cases and the same masks, their
probabilities correlate at 0.39 on average, against 0.85 when each gives its
overall view (difference -0.47, 95% interval -0.59 to -0.37). The plan's
criterion, an interval below zero, is met. The views did not go quiet to get
there: every isolated perspective left 0.50 on at least 20 of the 21 cases.
The secondary scores move in the right direction but, as planned, cannot
settle anything with 21 cases.

## Cases and arms

The 21 Q2 2026 cases of [2026-10-smallbank-run](../2026-10-smallbank-run/README.md)
(sixteen small and mid caps, five small banks, every event after the
models' knowledge cutoff), each with the abstraction mask saved for it
there; no case was masked again. Both arms ran `researchos evaluate` with
the initial playbook (equal weights, no lessons) on their own copy of the
cases: arm H with `--perspective-mode holistic` (what the analyst has always
done), arm I with `--perspective-mode isolated` (v20). Each forecast was
sealed before its outcome was opened.

## Do the views disagree?

| | holistic | isolated |
|---|---|---|
| mean pairwise correlation | 0.855 | 0.387 |
| lowest pairwise correlation | 0.67 | -0.22 |
| standard deviation of a perspective's probability | 0.064-0.092 | 0.038-0.085 |
| cases on which a perspective left 0.50 (of 21) | 16-21 | 20-21 |

On one bank (sb01) the holistic views all sat between 0.36 and 0.43: a sharp
rise in non-performing assets coloured every perspective, growth and cash
flow included. Isolated, growth read 0.57 (record revenue, up 23.5%), cash
flow 0.52, profitability 0.53, and risks 0.33 (the credit deterioration);
each factor spoke for itself, and the judge weighed them to 0.38.

Not every perspective came apart. Cash flow became nearly independent of the
rest (its correlations from -0.22 to 0.02); growth and profitability moved
partly apart (0.23-0.65 with the others, except profitability with the
expectations gap at 0.75). Guidance, expectations gap, management tone and
risks still move together (0.64-0.79), probably because on these reports
they read the same few passages: the outlook and how management talked
about it.

Is this a real difference or a rerun's noise? Eight of the cases were
evaluated holistically in the small-bank run with the same playbook and
masks. Run again here, the judge's probabilities correlate at 0.98 with
that run's (mean absolute change 0.016), and each perspective's at
0.96-0.99. A forecast barely moves when it is run again; the mode moves it.

## Do the scores change? (secondary, exploratory)

These 21 outcomes were known from the small-bank run. With 21 cases none of
the following can show a difference of plausible size.

| Brier score | holistic | isolated | difference (95% interval) |
|---|---|---|---|
| the judge | 0.227 | 0.222 | -0.005 (-0.017, +0.007) |
| combined forecast (holistic: weighted mean; isolated: log-odds pool) | 0.227 | 0.213 | -0.014 (-0.074, +0.055) |
| direction right, combined forecast | 12 of 21 | 15 of 21 | |
| direction right, the judge | 12 of 21 | 13 of 21 | |

The combination rules replayed on each arm (`researchos blend`, nothing
tuned):

| | uniform | median | hedge | leader | pool |
|---|---|---|---|---|---|
| holistic | 0.227 | 0.222 | 0.227 | 0.232 | 0.224 |
| isolated | 0.235 | 0.231 | 0.236 | 0.248 | 0.213 |

On the holistic views every rule scores alike, as before: they are one view.
On the isolated views the rule matters, and in the direction theory says:
views of separate factors should be added, not averaged, and pooling them
(0.213) beats averaging them (0.235). Alone, six of the seven isolated
perspectives score worse than their holistic twins (0.225-0.252 against
0.221-0.244; the expectations gap is the exception, 0.225 against 0.226), as
one factor should tell less than a whole report; together they do at least
as well.

## What this means

Diversity can be built in, and once it is, how the views are combined starts
to matter -- which is what training the weights needs. Two things follow,
each for a new plan:

- The cluster that remains (guidance, expectations gap, tone, risks) reads
  the same evidence. Giving each perspective only its part of the documents
  would test whether that, too, can be pulled apart.
- Whether learned weights help can now be asked properly: train a playbook
  in isolated mode walk-forward and evaluate it on cases whose outcomes
  nobody has seen. These 21 are no longer such cases; Q3 2026 reports, now
  coming in, would be.

## Cost

API spend: $3.37 for the holistic arm and $3.33 for the isolated arm, about
$0.16 a case; $6.70 in all, inside the plan's $15. Building tools v5 and v20
with Foundry cost $0.32 and $0.94 more.
