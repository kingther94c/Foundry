# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A provider's count of input tokens sizes the context only where the request
could hold it (D-215), at the user's pick of the compaction loop D-214 named.
The local OpenClaw gateway reports what its whole run spent behind a request,
every call its agent made: 229 of 532 requests in the probe runs were counted
at 1.8 to 6 times the most they could hold, and 51 of the 66 compactions came
while the history was under the 12,000-token limit -- all 34 of the five-step
build D-209 was written for among them. codex, Gemini CLI and opencode trust
the provider's count; OpenHands counts locally; none weighs a count against
its request.

## Changes

- `src/foundry/core/context.py`: `_most_tokens` (three characters to the
  token, one a character outside ASCII, four a message, images, reasoning),
  `schema_chars`, `COUNT_MARGIN`; `measured` sets aside a count more than
  half again past the most its request could hold beyond what the provider
  adds to every request, takes that most plus what it adds instead, and says
  so; the first count stands.
- `src/foundry/core/runtime.py`: the tools a request offers are weighed with
  it; the session is told once; the journal's token record keeps `sized_as`.
- Tests: `tests/test_provider_overcount.py` (new, 11); four tests of D-190
  and D-209 now give the context what their counts grew by.
- Documents: decision log D-215, roadmap, design, README.

## Decisions and open points

- A count past its request is taken as the most the request could hold, a
  generous bound (the gateway's honest counts are 0.86 to 1.0 of it): safe
  for the overflow check, a little early for compaction.
- What a gateway adds to every request stays counted: it is in the window.
- Not here: what survives a compaction. Foundry keeps the user's messages and
  the summary, as codex; Gemini CLI keeps the last 30% of the history,
  opencode the newest 15,000 tokens, OpenHands a tail of events, Claude Code
  re-reads up to five recent files. The first live run still compacted eight
  times under r-long's 12,000-token limit, every one with the history past
  it, the model reading back 6,000 to 7,000 tokens after each.

## Verification

- Full suite as CI runs it (PYTHONDONTWRITEBYTECODE unset, -X warn_default_encoding),
  with the documents written: 5,386 passed, 1 skipped in 22 min 35 s. The suite collects 5,387 tests.
- Mutations of the new lines: 21, each killed (run on a copy of the tree while
  the gateway runs used this one).
- The rule replayed over the 33 earlier gateway probe runs: of 466 conversation
  requests, 191 counts set aside (1.82 to 6.16 times the most they could hold),
  275 stood -- each run's first count, and five more before an honest one.
- On the gateway (openclaw/foundry, r-long, two runs, 84 requests, plus 2 to see
  the gateway answered): both completed and passed their hidden tests, in 47 and
  28 rounds (55 and 29 requests; eight compactions and one); 34 of the 84 counts
  set aside; none of the nine compactions with the history under the limit.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-review-planning-a401a7`, from main at `4c1495a`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/core/context.py` |
| M | `src/foundry/core/runtime.py` |
| M | `tests/test_compaction_floor.py` |
| M | `tests/test_measured_context.py` |
| A | `tests/test_provider_overcount.py` |
<!-- auto:end -->
