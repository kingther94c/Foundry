# Small banks: plan, fixed before any bank is selected

Written and committed before the bank list is drawn, before any case is
built and with no outcome known, so that neither the choice of cases nor
the analysis can depend on how the stocks reacted.

## Why

In `2026-10-smallcap-run` abstraction masking brought the right small bank
down to 0.30-0.45 of the guesser's belief and hid one bank outright, while
every company with a distinctive business stayed at 0.80-0.90. Small banks
are the one pool here with many look-alikes, so they are where blind
training can work, if anywhere.

## Selection

1. Universe: the same as the small-cap run -- 8-Ks furnished under Item
   2.02 between 2026-07-21 and 2026-08-14 announcing second-quarter 2026
   results, public float $250M-$5B (`CY2025Q2I` frame), a ticker, and a
   Motley Fool transcript of the Q2 2026 call held on the release date or
   the next trading day.
2. Banks only: SEC standard industrial classification 6021, 6022, 6029,
   6035 or 6036, from the company's SEC submissions record.
3. Left out: the five banks of the small-cap run (UCB, IBCP, VLY, STBA,
   HFWA), so that this sample is new.
4. Order: by filing date, then accession number. A bank whose release or
   transcript cannot be fetched or cut is skipped and the next one taken.

## Masking

Abstraction masking on the current code (v18): `MaskConfig(abstract=True,
history_years=3)`, four rounds, through the Claude API (claude-opus-5-5),
the abstractor and masker on one client and the guesser on another, seed
`0:<case>`. A case counts as **masked** only if the round guesser fails and
a fresh guesser, shown nothing but the final mask, fails too (in the
small-cap run a fresh guesser named one of two "masked" banks again).

Cases are built without their outcome: the event date comes from the
market's trading days, and a case's prices are fetched and its outcome
computed only once it is masked. Masking never sees an outcome.

## Stopping and cost

Cases are built and masked in order until twelve are masked, the list runs
out, or the run has spent $25 on the API. Futility: if after fifteen masked
attempts fewer than three cases are masked, the masking stops (at that rate
eight masked would need more cases than the budget buys).

## Training (only if at least eight cases end masked)

The masked cases are ordered by event date. The earliest two thirds train a
playbook walk-forward (`researchos train`, event-date batches, no case
trained before an earlier one is sealed); the rest are held out and
evaluated (`researchos evaluate`) with the untrained and the trained
playbook, and the two are compared with `researchos compare`. With at most
twelve cases this is underpowered and is reported as such, whatever it
shows. If fewer than eight cases end masked, only the masking is reported.

## Amendment 1, 2026-10-18: five banks, and a training analysis on all cases

Written after the selection rule was applied and before any bank was
built or masked; no outcome opened.

Of the 610 candidates with a Motley Fool transcript only ten are banks
(Fool transcribes few small banks), and five of them were in the small-cap
run. That leaves five: GBFH, ALRS, BANC, TBBK and NEWT, in filing order.
Twelve masked, or eight, cannot be reached from five, so the masking above
runs on these five and is reported as it stands, and the training section
above cannot apply.

Training then follows the rule the first real run used: every case here
was reported after the model's knowledge cutoff (2026-06-30), so
recognizing the company gives the analyst priors but cannot tell it the
reaction. The training analysis pools the sixteen small-cap cases and the
five banks (21 cases, all Q2 2026), each with its abstraction mask (the
eleven made in the small-cap run; the five small caps that run did not
reach and the five banks are masked the same way here, without the futility
stop), and records for every case whether it was masked.

1. Order the 21 cases by event date (then case id). The earliest 14 train
   a playbook from the initial one, walk-forward in event-date batches
   (`researchos train --knowledge-cutoff 2026-06-30`); the last 7 are held
   out.
2. Evaluate the 7 with the initial and with the trained playbook
   (`researchos evaluate`, same masks, same seed) and compare them
   (`researchos compare`, Brier score of the weighted forecast, bootstrap
   interval). Report the judge's and each perspective's Brier score as
   well, and the masked and identifiable cases separately.
3. Seven held-out cases cannot show a training effect of any plausible
   size; this is a test that the loop runs end to end on blind-ish data,
   and it is reported as underpowered whatever it shows.

Cost: about $1 a case to build and mask, $0.35 a case to train, $0.20 a
case per evaluation; the $25 cap above stands for the whole run.
