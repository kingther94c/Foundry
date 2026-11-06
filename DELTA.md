# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A program the user names is told when a turn ends (D-050), item 11 of the
codex-parity order: `notify = ["python", "notify.py"]` in the user's
`config.toml`, started with the turn's JSON as its last argument -- codex's
`agent-turn-complete` -- so long work left unattended says when it stops.
Only the user's own config may name it; it runs without a shell, with the
command environment, and nothing waits for it. On Windows a batch file is
refused: cmd.exe would read the model's words in the payload as commands.

## Changes

- `core/notify.py` (new): `payload` (scrubbed, each text capped at 2,000
  characters) and `Notifier.send` (found on PATH, no shell, its own process
  group, no stdin, output discarded; a batch file or `cmd.exe` refused).
- `core/config.py`: top-level `notify`, refused from a repository's config.
- `core/runtime.py`: `run_turn` tells the notifier when the turn ends, unless
  the user stopped it; a `notified` record for each start or failure, one
  warning for a program that cannot start.
- `core/session.py`: `EventType.NOTIFIED`.
- `cli/app.py`: the session's notifier from the config, scrubbed by the
  default redactor.
- `tests/test_notify.py` (new, 24 tests).
- Docs: D-050; design.md; threat-model.md (a trust row and a threat row);
  README (the setting); roadmap item 11.

## Decisions and open points

- Codex's argument convention, so a script written for codex's `notify`
  works; `session-id` stands for codex's thread id, and `status` is added.
- Not told when the user stopped the turn with Ctrl+C: they are there.
- A program that never exits is not ended; deferred, with a payload for an
  approval that waits on the user.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2043 passed, 1 skipped (2019 before, plus 24 in
  `tests/test_notify.py`).
- A real child process receives the payload verbatim -- quotes, backslashes,
  `&`, `%PATH%`, newlines, non-ASCII -- in the workspace, without a
  credential-shaped variable, and prints nowhere.
- Mutation check (scratch script): 24 mutants -- the repository
  refusal, the list and word checks, provenance, scrubbing and clipping, the
  command environment, discarded output, collecting finished programs, the
  batch-file and `cmd.exe` refusals, the payload argument, the workspace
  directory, the PATH lookup, silence on a cancelled turn, the single
  warning, both journal records, the call itself, the status, and the app's
  wiring and scrubbing. All are caught.
- Not run against a live model: the local gateway answers 404.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/notify`, from main at `e9ff69e`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/config.py` |
| A | `src/foundry/core/notify.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/session.py` |
| A | `tests/test_notify.py` |
<!-- auto:end -->
