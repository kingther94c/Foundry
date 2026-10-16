# Small and mid caps: plan, fixed before any candidate was looked at

Written and committed before the candidate list was fetched, so that neither
the choice of companies nor the analysis can depend on how the stocks
reacted.

## Why

Every one of the fifteen megacap cases in `2026-10-first-real-run` stayed
identifiable under redaction and under abstraction: a megacap's structure
names it. Blind training needs companies whose structure is not unique.

## Selection

1. Universe: 8-K filings furnished under Item 2.02 (results of operations)
   between 2026-07-21 and 2026-08-14 that announce second-quarter 2026
   results, found with EDGAR full-text search.
2. Size: public float (`dei:EntityPublicFloat` in SEC's XBRL frame
   `CY2025Q2I`, the figure on the last 10-K cover) between $250 million
   and $5 billion. Companies without a figure in that frame are left out.
3. A ticker in SEC's `company_tickers.json`, a Motley Fool transcript of the
   second-quarter 2026 call, and daily closes from Interactive Brokers.
4. Order: by filing date, then accession number. The first 16 companies
   that pass every check and whose case builds (release and transcript
   found and cut) are the cases; nobody looks at prices, news or reactions
   before then.

Documents: the release is the 8-K exhibit (EX-99.1) on sec.gov, the
transcript the Motley Fool page. No consensus source is reachable from
this environment, so these cases have no expectations document; the
analysts judge from the release and the call.

## Analysis

1. Masking (primary): mask every case with redaction, four rounds, the
   masker and the guesser played by different subagents; show each final
   mask to a fresh guesser. Report how many are masked against 0 of 15
   megacaps.
2. Training (only if at least 8 cases end masked): split the masked cases
   by event date, the earliest two thirds to train walk-forward in
   event-date batches, the rest held out; evaluate the held-out cases with
   the untrained and the trained playbook and compare them with
   `researchos compare`. Report it as underpowered whatever it shows.

## Amendment, 2026-10-16: re-mask on a fixed pipeline

Written after the first eight cases were masked and before any of the
sixteen is masked again. No price, return or outcome file of any case has
been opened; the masking runs read only `case.json`.

The first eight cases (sc01-sc07, sc09) were masked as planned, with the
code as it stood (v14). All eight stayed identifiable, but the maskers'
reports, checked against the masked text, showed three defects in the
pipeline rather than in the companies:

1. The masker wrote its terms as they appeared to it (amounts scaled,
   years shifted, earlier redactions in place) and each round applied them
   to the original text, where such terms do not occur: on sc05, 16 of the
   masker's 146 terms changed nothing.
2. Real figures survived. Tables print `$` in a cell of its own, or not at
   all, so only prose amounts were scaled: in sc07, 877 of 911 comma-grouped
   numbers came through as reported (book value per share $27.13 among
   them, which the guesser quoted).
3. Years long past undid the year shift: guessers read it off a founding
   year or the 1995 securities reform act and then mapped every other year
   back.

Fixes, each built by Foundry: terms are matched against the text the
masker saw (v15, merged); numbers in `" | "` table rows are scaled and
years more than three years before the event are replaced by "an earlier
year" (tools v3 and ResearchOS v16, merged; `history_years=3`). That still
leaves 404 of the sixteen cases' 6,194 comma-grouped real figures after
pre-masking, all in other layouts (cells one per line, negatives split
across cells, slide text); tools v4 and ResearchOS v17, being built when
this was written, scale amounts in any layout (29 of 6,194 left with the
hand-written reference). No case is masked again until they are merged.

So the masking step is run again on all sixteen cases with the fixed
pipeline, `history_years=3`, the same seeds, four rounds, masker and
guesser played by different subagents, and a fresh guesser on each final
mask. The eight masks made with v14 are reported as well, as the result
before the fixes. Everything else in the plan stands, including the rule
that training runs only if at least eight cases end masked.

## Amendment 2, 2026-10-16: an abstraction arm

Written while the redaction re-run (v17) was in progress, with seven of
the sixteen cases finished, all seven still identifiable (the true
company's confidence after four rounds 0.50-0.82, against 0.30-0.80 before
the fixes). No outcome of any case has been opened.

The guessers' last clues are no longer stray figures but the shape of the
documents: table layouts and line-item names ("State and local taxes",
"Card revenue"), the sections a company always reports, and the quarter's
own events. Redaction cannot remove a layout. Abstraction (v13) rewrites
each pre-masked document as a generic brief in rates and changes, which
removes layouts and line-item names by construction; on the megacaps it
failed because a megacap's business itself is unique, which need not hold
here.

So a second masking arm runs on all sixteen cases: abstraction masking
(`MaskConfig(abstract=True, history_years=3)`), four rounds, the
abstractor and masker played by one subagent and the guesser by another
that never sees the documents, a fresh guesser on each final mask, the
same seeds. Both arms are reported. Training follows the plan's rule on
whichever arm masks at least eight cases (abstraction if both do, since its
briefs carry less of the company); if neither does, there is no training.
