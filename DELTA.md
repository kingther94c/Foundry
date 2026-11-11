# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The wheelhouse pinned (D-170, P8; OQ-20 answered): `requirements.lock`,
written from the wheels downloaded from PyPI with the user's leave, each
hash the one PyPI publishes.

## Changes

- New `requirements.lock`: rich 14.3.4, markdown-it-py 4.3.0, mdurl 0.1.2,
  Pygments 2.21.0, each by the sha256 of its wheel.
- Docs: D-170 (and D-164's status); OQ-20; roadmap (P8, P4, the status
  table, the test count); README (the status, the lock).

## Decisions and open points

- The lock pins the versions pip chose that day; the development
  environment had markdown-it-py 4.2.0 and Pygments 2.20.0, and the suite
  ran again against the locked ones, in a virtual environment. There one
  test failed every time: it measured the virtual environment's launcher,
  not the versions, and D-171 (landed first) has it start the interpreter.
- README's status now names D-146 to D-171 and the test count with D-171's
  new test.

## Verification

- Each pinned sha256 compared with the one PyPI publishes: all four match.
- The wheelhouse downloaded again from the lock with `--require-hashes`;
  `--verify`: "every dependency pinned by hash".
- Installed offline into a fresh virtual environment -- the lock with
  `--require-hashes`, then foundry with `--no-deps` -- and `foundry
  --version`, `foundry doctor` ran.
- Full suite against the locked versions, in a virtual environment: on
  D-171, since this change adds only the lock and docs, which no test
  reads: 4869 passed, 1 skipped (19 min 50 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
