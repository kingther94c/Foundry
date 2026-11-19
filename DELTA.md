# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A fourth probe set, harder than the first two (D-211), run on the user's
local OpenClaw gateway at their word, and what it found (D-212). Over three
runs each, the migration, the spec and the root cause passed every time; the
seven-step build ran out of its 40 rounds once, spending them on patches
Foundry refused, and passed once those were taken. The calls a GPT model sent
and Foundry refused are taken: a context-only @@ hunk as an anchor, finish's
claims under the names the model gave them, repo_map's depth, search_text's
max_results and text that is no regular expression.

## Changes

- D-211: `verify/probes/make_projects_d.py` (new): four projects with a
  scaffold, hidden tests and a reference solution each; `check_c.py` takes
  the set; `verify/.gitignore` and `verify/README.md`.
- D-212: `src/foundry/core/tools/patch.py` (an @@ hunk of context alone is an
  anchor; `locate(first=...)`); `tools/finish.py` (`claim`, `event_id`);
  `tools/repomap.py` (`depth`); `tools/files.py` (`search_text`
  `max_results`, a query that is no regex searched as text).
- Tests: `tests/test_verify_kit.py` (the fourth set is checked, generated
  sets left out of the kit's own checks), `tests/test_gateway_tool_shapes.py`
  (eight new), `tests/test_repo_map.py` and `tests/test_tools_files.py`
  (each now takes what it refused).
- Documents: decision log D-211 and D-212; roadmap (status, P13, the suite's
  size).

## Decisions and open points

- codex's apply_patch was read first: it seeks each chunk from a cursor the
  chunk before moved, so a context-only chunk says where the next one is.
  Foundry keeps its rule that a hunk matches once in the whole file, except
  after an anchor.
- ripgrep refuses a query that is no regex; here the text is searched and
  the result says so, since a refusal cost a round and the text was meant.
- Not changed: the default of 40 rounds; D-209's floor, a single sample that
  a gateway's noisy count can set low.
- The subscription's usage limit was reached at the last run: further gateway
  runs wait for the quota.

## Verification

- Full suite as CI runs it (PYTHONDONTWRITEBYTECODE unset, -X warn_default_encoding),
  with the documents written: 5,350 passed, 1 skipped in 22 min 16 s. The suite collects 5,351 tests.
- The fourth set's own check: every hidden suite fails on its scaffold and
  passes on its reference (`check_c.py`, "OK: 4 versions").
- Mutations of the new lines: D-211 7, D-212 27, each killed.
- On the gateway (openclaw/foundry, 170 requests counted in the two batches
  that count): before D-212, three of four passed and r-long ended partial
  with its 40 rounds spent (46 requests); after it, o-money, p-spec and
  q-rootcause passed twice more each and r-long passed once in 37 requests;
  r-long's second run stopped at the subscription's usage limit and counts
  as no run.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-review-planning-a401a7`, from main at `4ff070d`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/core/tools/files.py` |
| M | `src/foundry/core/tools/finish.py` |
| M | `src/foundry/core/tools/patch.py` |
| M | `src/foundry/core/tools/repomap.py` |
| M | `tests/test_gateway_tool_shapes.py` |
| M | `tests/test_repo_map.py` |
| M | `tests/test_tools_files.py` |
| M | `tests/test_verify_kit.py` |
| M | `verify/.gitignore` |
| M | `verify/README.md` |
| M | `verify/probes/check_c.py` |
| A | `verify/probes/make_projects_d.py` |
<!-- auto:end -->
