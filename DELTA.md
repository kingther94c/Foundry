# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

What six small auto-mode projects against Claude turned up (D-188 to
D-191): `exec` patches without asking by default; `apply_patch` reads
codex's `@@` hunks and the other forms Claude wrote unasked; the context
is sized by the provider's own count, so a compaction limit fires; and
`read_file` names line endings and a byte order mark.

## Changes

- `cli/unattended.py`, `cli/app.py`, `cli/codex_flags.py`: `exec`'s default
  mode is `accept_edits`; a review keeps `dont_ask` (D-188).
- `core/tools/patch.py`: `@@` hunks, a bare patch's missing end line, an
  added file's bare blank lines; an update with no hunk is told the format
  (D-189).
- `core/context.py`, `core/runtime.py`: `measured()`, and the estimate
  anchored on it for masking, compaction and the overflow check (D-190).
- `core/tools/files.py`: `read_file`'s header notes (D-191).
- Tests: `test_patch_leniency.py`, `test_measured_context.py`,
  `test_read_file_bytes.py`; updates to the exec, codex-flag, review, patch
  and mention tests (two mention fixtures now write LF on every platform).
- Docs: D-188 to D-191, an update to D-010; design, README, roadmap.

## Decisions and open points

- The user chose both: `exec` defaults to `accept_edits`, and `@@` hunks are
  read but not advertised -- the prompt still describes SEARCH/REPLACE.

## Verification

- Mutation check: 29 mutations of the four changes; 27 killed at first, and
  the other two (the count taken after the reply joins the history, a zero
  count kept) once two tests were added.
- The six probes again against Claude (about US$0.43): all completed and
  passed their hidden checks; the five-step build compacted twice.
- Full suite as CI runs it: 4964 passed, 1 skipped (22 min 37 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
