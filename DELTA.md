# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The wheelhouse's hash-pinning tooling (D-164, P8; OQ-20 answered in part):
`build_wheelhouse.py --lock` writes `requirements.lock` from the wheels
downloaded, and with a lock the download, `--verify` and the install are
checked against it. The lock itself waits on the user allowing the download.

## Changes

- `scripts/build_wheelhouse.py`: `--lock`; with a lock, the download uses
  `--require-hashes -r requirements.lock` and `--verify` checks every wheel
  against it; without one, it says the wheelhouse is not pinned.
- `.github/workflows/ci.yml`: with a lock, the offline install is by hash,
  foundry's own wheel after with `--no-deps`; each step's exit code is
  checked.
- README: building the lock, and installing with it.
- Tests: `tests/test_wheelhouse_lock.py`, with made-up wheels; nothing is
  downloaded.
- Docs: D-164; OQ-20; requirements §7; roadmap (P8).

## Decisions and open points

- The lock is written from the downloaded wheels, not by pip-compile.
- Open: the lock itself, once the user allows the download from PyPI.

## Verification

- `tests/test_wheelhouse_lock.py`: 14 passed. Nothing downloads: the
  wheels are made-up files.
- Mutation check: 10 mutants -- names not normalized, foundry pinned,
  changed bytes, an unpinned wheel, a pin without a wheel, a pin without a
  hash, a continuation not joined, two versions, the download not checking
  hashes, `--verify` not reading the lock -- all killed.
- Full suite: 4860 passed, 1 skipped, stacked on D-163 with D-165 on it
  (21 min 50 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/wheelhouse-lock`, from main at `72b0543`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `.github/workflows/ci.yml` |
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/open-questions.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `scripts/build_wheelhouse.py` |
| A | `tests/test_wheelhouse_lock.py` |
<!-- auto:end -->
