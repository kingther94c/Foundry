# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The checkup's protocol level (D-205): after the network level, `foundry
checkup` asks the model eight small questions through the adapter a session
uses -- a word streamed and once, the numbers to 40 to see whether the stream
is held back, a call of a toy `add` tool streamed and once, the turn after its
result, a reply under a ceiling of 16 tokens, a model that does not exist --
each a line with a short code where something is wrong, and a line of what it
spent. What each request received is kept, redacted, beside the report.
`--all-protocols` asks the same in the other two protocols at the same
address.

## Changes

- `src/foundry/core/protocheck.py` (new): the eight requests, each case's
  verdict and code, what was spent and received.
- `src/foundry/core/checkup.py`: `Found`, a list that shows each case as it is
  found (from netcheck, where it was `_Cases`); `src/foundry/core/netcheck.py`
  uses it.
- `src/foundry/cli/checkup.py`: the protocol level after the network's,
  skipped where a turn cannot be asked; `--all-protocols`; the answers kept in
  `checkups\checkup-DATE-TIME\`. `src/foundry/cli/app.py`: the flag.
- `tests/fakegateway.py` (new): a gateway on loopback in the three protocols,
  with a switch for each fault. `tests/test_checkup.py`: the endpoint is that
  gateway; 54 new tests; the D-204 tests with the protocol level's mark.
- Documents: decision log D-205; README (the first run, the command table);
  requirements; design's code map; roadmap (status, P15, the suite's size).

## Decisions and open points

- Each case is asked once (`max_retries` 1): a session's retries would wait
  out a failing endpoint and hide what it answered.
- When the first request fails with 401, 403, 407 or 404, or does not connect
  or answer, the rest are skipped: each would meet the same.
- The level is skipped only where a turn cannot be asked: the endpoint not
  reached, the protocol unknown, no credential, or the credential refused at
  the model list. A model list that is missing, a web page or an error still
  leaves a turn worth asking.
- The task level is the next entry.

## Verification

- Full suite as CI runs it (PYTHONDONTWRITEBYTECODE unset, -X warn_default_encoding):
  5,306 passed, 1 skipped (5,307 collected) in 20 min 57 s.
- The new tests cannot import the code before (no `protocheck`).
- 53 mutations of the new lines, each killed; three (a 403, a 404 and a 429
  each falling to the general 4xx line, under the same code) survived at
  first, and the test of each failure now holds what its line says.
- A run against the fake gateway in each protocol and with each fault, by
  hand before the tests were written, gave the codes the tests now hold.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-review-planning-a401a7`, from main at `3f77228`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/checkup.py` |
| M | `src/foundry/core/checkup.py` |
| M | `src/foundry/core/netcheck.py` |
| A | `src/foundry/core/protocheck.py` |
| A | `tests/fakegateway.py` |
| M | `tests/test_checkup.py` |
<!-- auto:end -->
