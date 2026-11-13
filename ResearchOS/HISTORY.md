# How ResearchOS was built

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

Review of the first version removed one thing: the loop called `load_outcome` through a helper
that checked two module globals so that its own test could patch either one.
That was production code bent around a test; the test now patches the name
the loop calls.

`researchos.sources` and `build` came second, the same way: Foundry got
the merged package plus a new SPEC.md section, in a fresh workspace.

| | Foundry | Hand-written reference |
|---|---|---|
| Acceptance tests (new + v1 regression) | 60 / 60 | 60 / 60 |
| Own tests | 23 | -- |
| Agent rounds / cost | 16 / ~$1.01 | -- |

The two agree on 3,000 random inputs to each of `event_date_for`,
`outcome_from_closes` and `select_span`. Foundry's version is again the more
careful one: it validates the case spec field by field, refuses empty span
markers (which would match anywhere), and does not ask for expectations when
no page was fetched, so the agent is never invited to supply figures from
memory. The run also surfaced a Foundry fix: the model ended an Add File
patch with `+*** End Patch`, and Foundry now accepts that end line.

Real pages then reshaped `select_span` twice. It first had the model quote
the document's first and last words and found them by exact search; a
model's copy of a page never reproduces its newlines, non-breaking spaces and
table pipes, so a release and a transcript that were on the page came back
missing. Matching on letters and digits only fixed those (Foundry made the
change in 6 rounds, ~$0.21, handling characters such as `İ` whose lowercase
is two characters, where the hand-written version crashed) -- but on nine of
the next sixteen cases the model could not quote the end of an 80,000-
character page at all, and on a release it stopped at the end of the
highlights. Now the page is shown with numbered lines and the model gives
line numbers, which it reads off rather than recalls; and since an investor-
relations page is often a stub linking to a PDF, every fetched page is cut
and the longest span kept. Foundry made that change too (11 rounds, ~$0.56)
and removed the marker matching it replaced; on 20,000 random inputs it
agrees with the hand-written version, except that it also rejects a boolean
where a line number should be.

The first real masks then showed that large companies cannot be masked:
after four rounds and some 200 redactions the guesser still named JPMorgan
and Apple at 0.97 and 0.99 -- segment structure and ratios survive any
renaming -- so every such case would have been skipped. Skipping exists so
the analyst cannot recall how the stock reacted; for an event after the
models' knowledge cutoff there is nothing to recall. `--knowledge-cutoff`
keeps identifiable cases whose event date is after it, and every scored
record says whether masking held. Foundry made the change in 13 rounds
(~$0.48).

Masking then turned out to be the most expensive step, and for large
companies a futile one, so it can now give up early: `--mask-give-up CONF`
ends masking, as identifiable, once the guesser names the company with at
least that confidence. The API key had run out of credit by then, so for this
change Foundry's model was a subagent behind a local relay that stands in for
the Messages API: Foundry's requests are written to disk, a fresh subagent
that sees only those requests writes the replies, and an audit of its tool
calls confirms it touched nothing else. Foundry made the change in 7 rounds,
passed all 76 acceptance tests, and agrees with the hand-written version on
4,000 scripted masking runs.

Comparing an untrained with a trained playbook on five held-out cases gave a
0.004 difference in Brier score, which says nothing without a sense of its
noise, so `compare` now pairs two reports case by case and bootstraps the
mean difference. Foundry made it through the relay in 10 rounds, passed all
92 acceptance tests, and agrees with the hand-written version on 9,000 random
report pairs -- except that it skips a NaN or infinite score instead of
letting it poison the means, which is the better choice.

The model playing Foundry pointed out, while building `compare`, that its
reports would be saved on Windows with PowerShell 5.1, whose `>` writes
UTF-16 and whose `Out-File -Encoding utf8` adds a byte order mark -- files
`compare` refused. Now every JSON file goes through `researchos.jsonio`,
which decodes by the byte order mark, and the commands take `--out FILE`.
Foundry made the change through the relay in 19 rounds, passed all 112
acceptance tests, agrees with the hand-written version on 6,000 fuzzed
files, and went one step further than it: the four places that each wrote
JSON their own way now share `write_json`. It also rewrote the one existing
test the new rule contradicted, instead of deleting it.

Comparing walk-forward training with the untrained playbook then took a
script: `compare` read one `evaluate` report a side, and walk-forward
training writes one `train` output per case. `compare` now reads training
output and merges several files a side (`-a`/`-b`). Foundry made it in 11
rounds, passed all 135 acceptance tests and agrees with the hand-written
version on 5,000 random report sets. Its review of the argument parsing
caught what the hand-written version broke: with a variable-length
positional list, `compare A.json --seed 5 B.json` -- valid until then --
became an error. Foundry kept it working.

That comparison then exposed a look-ahead in training itself: `train` took
cases in the order given, so a forecast could use a lesson drawn from a
stock that reacted the same day or later. Now `train` walks forward in
event-date order, forecasts every case of one event date before opening any
of their outcomes, and `train` and `evaluate` refuse a case dated on or
before the last reaction the playbook learned from (`learned_through`), or
one without an ISO event date. Foundry made it in 18 rounds and passed all
146 acceptance tests. It found the two existing acceptance tests the new
rule broke (each evaluated a case dated the same day as the one it trained
on) and fixed their data rather than the rule. On 400 random training and
evaluation sequences it agrees with the hand-written version -- once that
version was fixed: it accepted an event date with stray spaces when
training and then could not read it back from the playbook, which
Foundry's version handled.

On real cases the learned weights barely moved, which raised the question
of how the perspectives should be combined at all. `researchos.blend`
replays combination rules walk-forward over records already scored, and
records now keep each perspective's probability. Foundry made it in 15
rounds, passed all 166 acceptance tests and agrees with the hand-written
version on 4,000 random record sets; it also checks more than that
version does (an `actual_up` that is not a boolean, a probability outside
[0, 1]).

Five versions had left private copies of two checks -- is this a finite
number, is this an ISO date -- in five places, each slightly different.
They now live in `tools` (`stats.is_number`, `dates.parse_iso_date`, built
by Foundry first) and ResearchOS uses them: Foundry made the change in 14
rounds, 25 lines fewer, with all 169 acceptance tests passing and the same
results as before on 13,400 fuzzed calls.

Redaction could not hide a megacap, so masking can now abstract first
(`--mask-abstract`): an abstractor agent rewrites each pre-masked document
as a generic brief -- rates, ratios and changes, segments as "segment A",
no names -- and the masker and guesser rounds run on the briefs. Foundry
made it in 11 rounds and passed all 179 acceptance tests; on 1,131 fuzzed
runs with valid briefs it agrees with the hand-written version, and where
the abstractor returns no brief it raises an `LLMError` instead of masking
an empty document, which the hand-written version did.

`build` can now fetch documents straight from their addresses
(`--release-url`, `--transcript-url`, `--expectations-url`, with
`--user-agent` or `RESEARCHOS_USER_AGENT`; no contact address is built in),
without the API's web tools. Foundry made it in 13 rounds and passed all 192
acceptance tests. On a real page its HTML-to-text agrees with the
hand-written version line for line, and gets the title right where the
hand-written one swallowed the tooltips of the page's SVG icons.

The small-cap run turned up leaks in masking itself, each fixed by Foundry
from a spec section, behind hidden acceptance tests: masker terms are
matched against the text the masker saw (v15, 10 rounds; on review the
rewriting of earlier replacements was extended to abstraction mode); table
numbers are scaled and years long past dropped (`--mask-history-years N`,
v16, 14 rounds); amounts are scaled in any layout (v17, 12 rounds, on
tools v4); and each round's redactions apply, in order, to the text the
earlier rounds left (v18, 20 rounds, the first of these built on the real
API, for about $0.89). Every attempt passed all of its hidden tests.

`agreement` (v19) measures whether the perspectives disagree at all -- each
one's spread and hit rate and the 21 pairwise correlations of their
probabilities -- because on the 21 small-cap and bank cases every
combination rule scored the same: the perspectives correlate at 0.86.
Foundry made it in 11 rounds on the real API ($0.50), and it agrees with
the hand-written version on 2,000 random record sets and on the real cases.
[docs/foundry-acceptance.md](../docs/foundry-acceptance.md) scores this
and the changes before it against fixed acceptance criteria.

`--perspective-mode isolated` (v20) asks each perspective a narrower
question -- how its factor alone would move the stock if everything else in
the report had come in as expected -- and adds the views' evidence in
log-odds (`tools.stats.pool_log_odds`, tools v5) instead of averaging it;
the judge is told the views add up. The default, `holistic`, is what the
analyst did before. Foundry made it in 24 rounds on the real API ($0.94);
it passed all 275 hidden tests, and on 3,000 random analyses it made the
same calls with the same prompts and returned the same forecasts as the
hand-written version.

Macro cases (v21) are the first step of the macro pivot: a snapshot of the
markets as of a date, whose targets (the 10-year yield, the S&P 500, ...)
are each forecast over horizons and resolve on their own dates, so the
outcome is written later, target by target, and a resolution manifest that
holds only dates says which are known. A macro case is not masked, and it
may only be forecast after the model's knowledge cutoff. Foundry made it in
9 rounds through the relay; it passed all 448 hidden tests and kept every
earnings file byte for byte, and it also kept `build`'s output unchanged,
which the hand-written version did not.

`fetch-series` (v22) stores the series a snapshot reads, all from free
sources that need no account: FRED's CSV for current values, ALFRED's for a
series as it was known on a date (`--vintage`), and the CFTC's Commitments
of Traders for speculative positioning. `load_series` reads a series back as
it was known on a date, never past it. Foundry made it in 6 rounds through
the relay; it passed all 636 hidden tests and agreed with the hand-written
version on 17,000 fuzzed calls.

A snapshot's first documents are four tables (v23, `researchos.snapshot`):
each target's level with its changes over a week, a month, three months
and a year and where it stands in its one-year range; the economic series
with the previous value and a year earlier, marked as published on the
date or as current values; speculative positioning from each report as
it was public on the date; and the events scheduled after it. Foundry
made it in 6 rounds; it passed all 715 hidden tests and agreed with the
hand-written version on 21,000 fuzzed calls.

`snapshot` (v24) builds the macro case of one date from a configuration
that serves every snapshot of a run (`macro-config.json`: the targets and
horizons, the economic series, the positioning, the official texts with
the dates they were published, and the calendar). It uses only what was
known on the date: the stored series cut at it, of each kind of text the
latest published by then, and the events scheduled after it. It never
rebuilds a case whose forecast is sealed or whose outcome is written.
Foundry made it in 10 rounds; it passed all 790 hidden tests, and where
it differed from the hand-written version it was the better of the two.

`resolve` (v25) records, once a horizon has ended, how far each target of
a snapshot moved: from the level the case recorded to the last
observation on or before the horizon's end, once the series has one on or
after it (a horizon that ends on a holiday waits for the next
observation). An outcome once written is never changed, and the forecast
is never opened. Foundry made it in 7 rounds; it passed all 834 hidden
tests and agreed with the hand-written version on 3,000 fuzzed calls.

`researchos.targets` (v26) analyzes a snapshot the way an earnings report
is analyzed, through ten macro perspectives (growth, inflation, labor,
monetary policy, fiscal policy and supply, liquidity, positioning, the
rest of the world, commodities, politics) and a judge. Each call covers
every target and horizon at once, under a strict schema built from the
snapshot's targets, so a snapshot of ten targets costs eleven calls.
Foundry made it in 7 rounds; it passed all 893 hidden tests and, on 3,000
random analyses, made the same calls with the same prompts and forecasts
as the hand-written version.

`score_targets` and `reflect_targets` (v27) score and reflect on a
snapshot once some of its targets have resolved. A record is written per
target key, with the case id `<snapshot>/<key>` and the keys an earnings
record has, so `compare`, `blend` and `agreement` read it as they read
an earnings record. The reflector is asked for lessons that hold on other
dates, never a rule of a particular date, level or event. Foundry made it
in 8 rounds; it passed all 920 hidden tests and agreed with the
hand-written version on 9,000 fuzzed calls.

`forecast` and `score` (v28) split a macro run in two. `forecast` seals
a forecast for each snapshot dated after the model's knowledge cutoff
and opens no outcome; a forecast is sealed once. `score` scores the
sealed forecasts against whatever has resolved since, lists what is
still pending, and calls no model. `train` and `evaluate` refuse a macro
case for now, since they open an outcome as soon as the forecast is
sealed. Foundry made it in 7 rounds; it passed all 978 hidden tests and
agreed with the hand-written version on 1,200 fuzzed calls. Review
changed one of its tests, which started a Python process that could
find `tools` only where it was installed; FOUNDRY.md now says so.

`blend` and `agreement` (v29) read the ten macro perspectives with
`--perspectives macro`, so a macro run's scored keys can be replayed and
their agreement measured as an earnings run's are. `replay` now learns
from a record only once it has resolved: a one-month view of a snapshot
teaches nothing to the next week's forecasts. Without the option or a
resolution date every result is what it was. Foundry made it in 8
rounds; it passed all 1014 hidden tests and agreed with the hand-written
version on 16,000 fuzzed calls.

`train` (v30) walks a macro run forward by resolution date. The
forecasts of a date use exactly the target keys that resolved before
it; a key that resolves on the date is learned from once that date's
forecasts are sealed, as an earnings case of the date is. A snapshot's
outcome is opened only when its first key is learned from. Each key
moves the weights once, and each snapshot and horizon gets one
reflection. Foundry made it in 12 rounds; it passed all 1067 hidden
tests, after one that asked for more than the spec was corrected, and
agreed with the hand-written version on 2,400 fuzzed runs.

`snapshot` (v31) keeps only a fetched page's main content: the text of
its one `main` element, or its one element with `role="main"`. A page
with none, with several or with an empty one gives its whole text, and
`build` still reads whole pages. Each Fed page had brought about 8,300
characters of site navigation. Rebuilt with v31, the 2026-10-30
snapshot's documents shrink from 58,004 characters to 41,366, the
tables unchanged. Foundry made it in 9 rounds; it passed all 1130
hidden tests and agreed with the hand-written version on 40,000 fuzzed
pages, on every one of which the whole text was what it had been.

`compare --resample cases` (v32) resamples whole cases: every record of a
case goes into a bootstrap resample together, or not at all. A macro
snapshot's keys move together, and resampling them one at a time made the
macro run's interval narrower than it should be. For an earnings report,
where a case is one record, the two agree, and without the option every
result is what it was. `--resample` is now an option of its own, so it no
longer abbreviates `--resamples`. Foundry made it in 15 rounds, the first
version built on the real API since v20 (about $0.27, most of the input
read from the prompt cache); it passed all 1179 hidden tests and agreed
with the hand-written version on 20,000 fuzzed calls.
