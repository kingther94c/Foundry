# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

P1's rerun against Claude (D-185): its plan and result, and the status
that follows.

## Changes

- Docs: D-185; the roadmap's P1 and status rows; the README's status.

## Decisions and open points

- What is left: P4 waits on the corporate side, the rerun on Chat
  Completions and Responses on an endpoint for them, and P5 on work to do
  with Foundry. The write fence stays research.

## Verification

- 4,925 tests, the count after D-182 to D-184.
- Docs only. Full suite as CI runs it, on D-184's tree, which this
  changes no code of: 4925 passed, 1 skipped (21 min 26 s); the docs' own
  tests (`test_delta_notes`, `test_file_links`, `test_project_doc_settings`)
  pass (93).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
