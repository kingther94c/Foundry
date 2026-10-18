# Small and mid caps: can masking hide them?

The fifteen megacaps of `2026-10-first-real-run` stayed identifiable under
every kind of masking. This run asks whether companies whose structure is
less unique can be hidden well enough to train blind. The selection rule,
the analysis and three amendments were committed before the steps they
govern ([PLAN.md](PLAN.md)); no outcome of any case was opened, so nothing
here could depend on how the stocks reacted. Per-case numbers are in
[masking_results.json](masking_results.json).

**Result.** Neither arm masked the eight cases training needs, so, as
planned, there is no training. Redaction hid none of eleven cases. Abstraction
hid two of eleven, and a fresh guesser named one of those two again. What
gives a company away is now its business, not leftover names or figures:
under abstraction the guesser's confidence in the right small bank fell to
0.30-0.45, while the six companies with a distinctive business (a card
issuer, a casino operator, a convenience-center REIT, a powersports maker,
an electrical contractor, an IT services firm) stayed at 0.80-0.90.

## Cases

Item 2.02 8-Ks filed 2026-07-21 to 08-14, public float $250M-$5B, a Motley
Fool transcript of the second-quarter call, taken in filing order: UCB,
IBCP, VLY, STBA, BFH, BYD, HFWA, CURB, PII, MYRG, UIS, AXGN, MTH, MGPI,
WNC, BHC (sc01-sc17). Five are banks (UCB, IBCP, VLY, STBA, HFWA). AMR (sc08) was built and then
excluded before masking: its 8-K is a preliminary release of 07-27 and its
call was on 08-07, two different events. From then on a release and a
call were paired only if the call was on the release date or the next
trading day, and the next company in order (BHC) took its place.
Documents: the EX-99.1 release from sec.gov and the transcript page,
fetched by address (v14). The contact User-Agent SEC asks for went to
sec.gov only; other sites got a generic one.

## Redaction, as it was (v14)

The first eight cases were masked as planned: four rounds, the masker and
the guesser played by different subagents. All eight stayed identifiable,
the true company's confidence after four rounds 0.30-0.80. The maskers'
reports, checked against the masked text, showed the pipeline was leaking:

| Defect | Measured | Fix |
|---|---|---|
| Masker terms were matched against the original text, not the scaled and shifted text the masker sees | 16 of 146 terms did nothing (sc05) | v15 |
| Table figures were never scaled: `$` sits in its own cell, most rows have none | 877 of 911 comma-grouped figures verbatim (sc07), book value per share quoted by the guesser | tools v3, v16 |
| A year long past (a founding year, the 1995 reform act) gives away the year shift | guessers undid it in 5 of 8 cases | `history_years` (v16) |
| Other layouts: a cell per line, negatives split as `(22` and `)`, slide text | 404 of 6,194 figures still verbatim after v16 | tools v4, v17 |
| A term wrapping around an earlier replacement matched nothing | 158 of 1,668 terms (136 for this reason) | v18 |

Each fix was built by Foundry in an isolated workspace, reviewed, and
merged (see the ResearchOS and tools READMEs). After v17, 29 of the 6,194
comma-grouped figures survive pre-masking, most of them coincidences.

## Redaction, fixed (v17)

All sixteen were masked again on v17 with `history_years=3` and the same
seeds (amendment 1). Eleven finished, all identifiable:

| | UCB | IBCP | VLY | STBA | BFH | BYD | HFWA | CURB | PII | MYRG | UIS |
|---|---|---|---|---|---|---|---|---|---|---|---|
| round 1 | 0.85 | 0.85 | 0.92 | 0.88 | 0.94 | 0.93 | 0.85 | 0.90 | 0.96 | 0.93 | 0.95 |
| round 4 | 0.50 | 0.60 | 0.60 | 0.42 | 0.82 | 0.80 | 0.55 | 0.60 | 0.85 | 0.83 | 0.85 |

(The guesser's confidence in the true company.) The other five were cut
off by a usage limit after two or three rounds, all still named at
0.78-0.95. With eleven identifiable, eight masked was out of reach, so
the arm stopped there (amendment 3). The guessers' last clues were no longer
figures but the shape of the documents: table layouts, line-item names
("State and local taxes", "Card revenue"), the sections a company always
reports, and the quarter's own events (a subsidiary sale, a spin-off's
services agreement), which no redaction can remove without removing what
the analyst needs. The fixes barely moved the guesser (the eight cases
masked both ways average 0.64 after four rounds before them, 0.61 after),
and they did not hide a single case.

## Abstraction (v18)

Abstraction rewrites each document as a generic brief in rates and
changes, which removes layouts by construction (amendment 2). It ran
through the Claude API on v18, four rounds, the masker and abstractor and
the guesser as separate clients, in case order, with a stop once nine
cases were identifiable (amendment 3):

| | final top guess | fresh guesser |
|---|---|---|
| UCB | **masked** after 2 rounds (PEBO 0.06) | named it (UCB 0.30) |
| HFWA | **masked** after 1 round (CZNC 0.08) | did not (GABC 0.20) |
| IBCP | IBCP 0.45 | |
| VLY | VLY 0.30 | |
| STBA | STBA 0.35 | |
| BFH | BFH 0.85 | |
| BYD | BYD 0.85 | |
| CURB | CURB 0.80 | |
| PII | PII 0.82 | |
| MYRG | MYRG 0.85 | |
| UIS | UIS 0.90 | |

Nine identifiable after eleven cases, so the last five were not run. The
banks are nearly hidden: the right bank is still the top guess, but at a
third of the guesser's belief, with similar banks close behind. One guesser
missing a bank is not enough either: the fresh guesser named UCB from the
same mask the round guesser had missed.

## What this means for blind training

Blind training needs a pool of companies that look alike, and in this
universe only the small banks come close. A pool drawn from small banks
alone (there are hundreds), masked with abstraction, is the next thing to
try; a company with a business of its own will not be hidden by any
masking that keeps what an analyst needs.

## Cost and checks

API spend for this run: abstraction arm $6.94 for eleven cases (about
$0.63 a case) and $0.24 for the fresh guessers; a live end-to-end
`evaluate` of one megacap case on v18 cost $0.51. The redaction masks were
made by subagents behind a local relay. The tool calls of all 48 masking
subagents were audited: one guesser ran Grep on its own request file
instead of Read (nothing outside its inputs), in a case that was later
cut off and is not counted.
