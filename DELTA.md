# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

P10, the fourth round with the model played offline (D-176): its plan and
its result, and the status that follows.

## Changes

- Docs: D-176; the roadmap's P10 and status rows; the README's status.

## Decisions and open points

- What is left waits on others: P4 on the corporate side, P1's rerun and
  P5 on a model endpoint. The write fence stays research.

## Verification

- 4,879 tests, the count after D-172 to D-175.
- Docs only. The full suite as CI runs it, on D-175's tree, which this
  changes no code of: 4879 passed, 1 skipped; here the docs' own tests
  (`test_delta_notes`, `test_file_links`, `test_project_doc_settings`)
  pass (93).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
