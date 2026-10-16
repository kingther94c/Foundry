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
