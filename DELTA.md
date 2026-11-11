# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

D-171's check names its encoding: CI runs the suite with
`-X warn_default_encoding`, where an `EncodingWarning` is an error, and the
check's `subprocess.run(..., text=True)` failed there. Tests only.

## Changes

- `tests/test_mcp_robust.py`: the check reads its child's output with
  `encoding="ascii"`.
- Docs: D-171 says so.

## Decisions and open points

- The full suite is run here as CI runs it, `python -X warn_default_encoding
  -m pytest tests -q`; the runs for D-170 and D-171 left the flag out.

## Verification

- Before: `python -X warn_default_encoding -m pytest tests/test_mcp_robust.py`
  failed the check with "EncodingWarning: 'encoding' argument not
  specified", as CI did on 8d0e397 and 278705f (whose logs need a sign-in:
  the failure was reproduced here, not read there).
- After: the same command, 22 passed.
- Full suite as CI runs it: 4869 passed, 1 skipped (20 min 51 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
