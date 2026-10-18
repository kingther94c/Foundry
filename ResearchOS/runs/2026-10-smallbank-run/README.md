# Small banks, and the training loop run end to end

The plan and its amendment ([PLAN.md](PLAN.md)) were committed before any
bank was selected, built or masked; no outcome was opened before the
training step read it. Per-case data: [masking_results.json](masking_results.json),
[train.json](train.json), [eval_untrained.json](eval_untrained.json),
[eval_trained.json](eval_trained.json), the comparisons
([weighted](compare_weighted.json), [judge](compare_judge.json)) and the
trained [playbook](playbook_trained.json).

**Result.** Two of the five new banks were hidden from two guessers in a
row; with HFWA from the small-cap run, three of the 21 Q2 2026 cases are
masked. The training loop then ran end to end on all 21 (masked briefs,
seven perspectives, the judge, sealed forecasts, reveal, reflection,
weight updates): 13 cases trained a playbook walk-forward and 8 were held
out. The trained playbook was no better on the held-out cases: Brier score
of the weighted forecast 0.193 untrained, 0.189 trained, difference
-0.004 with a 95% interval of [-0.023, +0.011]; the judge 0.184 against
0.183, [-0.018, +0.014]. Eight cases cannot show an effect of any plausible
size; this run shows the loop works on blind or near-blind documents, not
that it learns.

## Banks

Of the 610 candidates in the small-cap run's universe that have a Motley
Fool transcript only ten are banks, and five were already used. The other
five, in filing order: GBFH, ALRS, BANC, TBBK, NEWT (sb01-sb05). Fool's
URLs carry the date it published a transcript (2026-08-07 for four of
them); the pairing rule is about the call, so the call date was read from
each page: every call was on the release date or the next trading day.
The cases were built without outcomes (the event date from the market's
trading days, the documents cut by the API), $1.13 for all five.

## Masking

Abstraction on v18, four rounds, the abstractor and masker on one client
and the guesser on another, then a fresh guesser on each mask the round
guesser missed:

| | round guesser | fresh guesser |
|---|---|---|
| GBFH | **missed** after 1 round (top guess FINW 0.08) | **missed** (MVLY 0.12) |
| ALRS | ALRS 0.25 after 4 rounds | |
| BANC | **missed** after 2 rounds (MSBI 0.25) | **missed** (AMTB 0.22) |
| TBBK | TBBK 0.88 | |
| NEWT | NEWT 0.88 | |

The three regional banks were hidden or nearly so; TBBK (a payments and
fintech-partner bank) and NEWT (a small-business lender) have businesses
of their own and stayed at 0.88, as the distinctive small caps did. The
five small caps the small-cap run's abstraction arm had not reached were
masked the same way: all five identifiable (AXGN 0.72, MTH 0.55, MGPI
0.85, WNC 0.60, BHC 0.88).

## Training

Every case reported after the model's knowledge cutoff, so recognizing a
company gives priors, not the reaction (amendment 1). The 21 cases in
event-date order; training must not cut through a date (a held-out case
must come after everything the playbook learned from), so the split fell
at 2026-07-29: 13 trained (through 07-29), 8 held out (07-30 to 08-07).

- Training: forecasts sealed walk-forward, batch by batch, before each
  batch's outcomes were read. Five of 13 had the direction right (mean
  Brier 0.258, worse than a coin's 0.25): these were hard cases. The
  playbook gained 80 lessons, none naming a company, many about banks ("for
  banks, guidance that lowers next-quarter net interest margin tends to
  dominate the reaction, even when the cause is mechanical"); the
  perspective weights barely moved (0.137-0.150).
- Held out: both playbooks beat a coin (0.19 against 0.25). The trained
  one got the direction right on 8 of 8 against 6 of 8, and its management
  tone and profitability perspectives improved most (0.191 to 0.176, 0.196
  to 0.185), cash flow got worse (0.196 to 0.203). The one masked held-out
  case, GBFH, scored a little worse after training (0.162 to 0.174).

## Which perspectives, and how to combine them (exploratory)

Not in the plan, and computed from the records already scored, with no
model call: the 21 cases' forecasts (13 walk-forward training forecasts,
8 held-out forecasts with the initial playbook) replayed with each fixed
combination rule of `researchos blend`, nothing tuned.

| | Brier score |
|---|---|
| uniform mean | 0.233 |
| median | 0.233 |
| hedge (eta 0.5) | 0.233 |
| leader (best so far) | 0.235 |
| the judge | 0.227 |
| a coin | 0.250 |

The rule does not matter, because the seven perspectives say nearly the
same thing: across the cases their probabilities correlate at 0.86 on
average (0.69 at the least), and each stays within a narrow band around
one half (standard deviation 0.07-0.10). Seven views that move together
are one view; weighting them differently cannot add what is not there.
Guidance was the best single perspective (0.227, the direction right on
13 of 20), as the expectations gap was for the megacaps. The lever is
then not the weights but the perspectives themselves: views built to
disagree -- each reading only its part of the documents, or arguing one
side -- before any combination rule can help.

## Cost

API spend for the run: $1.13 to build, $6.27 to mask (ten cases), $0.12
for the fresh guessers, $4.00 to train, $3.50 for the two evaluations;
$15.02 in all, inside the $25 cap.
