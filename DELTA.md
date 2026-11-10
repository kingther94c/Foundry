# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A restricted-token sandbox researched (D-165, P7): a probe measured three
token configurations on Windows 11. Not adopted, and D-003 stands; an opt-in
low-integrity write fence is the V2 candidate it leaves.

## Changes

- New `scripts/sandbox_probe.py`: runs a command under a write-restricted
  token, a low-integrity one, or both, and reports what it could do.
- New `docs/research/windows-sandbox.md`: the measurements and what they
  mean.
- Tests: `tests/test_sandbox_probe.py`, the findings the decision rests on.
- Docs: D-165; roadmap (P7, the V2 candidates, the status table, the test
  count); threat model (section 6); AGENT.md (the layout); README (the
  status).

## Decisions and open points

- A write-restricted token lets a command delete the user's files and
  breaks pytest's `tmp_path`: not carried over.
- Low integrity fences writes and deletes outside the workspace and keeps
  the tools working; it leaves reading, the network and DPAPI open, so it
  could only ever be called a write fence.
- Open, for a V2 decision: the label left on the workspace, interprocess
  paths, a non-elevated session.

## Verification

- `tests/test_sandbox_probe.py`: 6 passed, on Windows 11 in an elevated
  session.
- Mutation check: 5 mutants of the probe -- the workspace not labelled
  low, Everyone not restricting, writes not restricted, the token not
  lowered, its folder left behind -- all killed.
- Full suite: 4860 passed, 1 skipped, on D-163 to D-165 stacked
  (21 min 50 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/sandbox-research`, from main at `959379a`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `AGENT.md` |
| M | `README.md` |
| M | `docs/decision-log.md` |
| A | `docs/research/windows-sandbox.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| A | `scripts/sandbox_probe.py` |
| A | `tests/test_sandbox_probe.py` |
<!-- auto:end -->
