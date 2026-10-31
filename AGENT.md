# AGENT.md

Working notes for anyone — person or agent — changing this repository. The
"why" behind the design lives in [docs/](docs/); this file is the short version
of how to work here.

## Layout

```text
src/foundry/core/   the runtime: runtime.py (the one loop), policy/, tools/,
                    backends/, session.py, workspace.py, winapi.py
src/foundry/cli/    the terminal UI: an event subscriber, the approval UI, report
src/foundry/prompts/ versioned system prompts
tests/              1436 tests; fixtures/ holds the golden-task sample repo
demo/               mini_foundry.py + the notebook: the architecture in one file
run-foundry.py      run the CLI from this clone, no install (foundry.cmd wraps it)
docs/               requirements, design, threat model, decision log, research
```

## Running things

Nothing needs installing but the dependencies: the tests and the CLI run from
the source tree (`pythonpath = ["src"]` in `pyproject.toml`; `run-foundry.py`,
or `foundry.cmd`, for the CLI).

```bash
python -m pip install -r requirements.in "pytest>=8.0,<9"
python -m pytest -q
python run-foundry.py doctor
```

`tools/` and `ResearchOS/` work the same way: `python -m pytest -q` in each.
ResearchOS also needs `anthropic`; its pytest config puts `../tools` on the
path, and `ResearchOS/run-researchos.py` runs its CLI.

An editable install (`pip install -e ".[dev]"`) still works, but it writes a
`.pth` file that points at one checkout. With several worktrees that used to
mean every other worktree's `python -m pytest` tested that one checkout's code,
without a word. Each worktree's own `src/` now comes first, and `foundry
doctor` prints the `source` it imported; if that is not the checkout you are
in, uninstall the editable copy.

CI runs every suite with `python -X warn_default_encoding -m pytest`, once
installed editable and once from the clone with only the dependencies
installed, and builds the wheelhouse (`python scripts/build_wheelhouse.py`) to
prove the offline `--no-index` install still works. Every job is Windows-only
by design.

## Conventions

- Everything here is in English: prose, docs, comments, identifiers and
  user-facing strings. Source files stay ASCII; a test that genuinely needs
  non-ASCII bytes writes them as escapes with a comment saying why.
- `foundry.core` imports neither `foundry.cli` nor any third-party package.
  Both are enforced by [tests/test_architecture_config.py](tests/test_architecture_config.py).
- Every `open()` passes `encoding="utf-8"` explicitly — `EncodingWarning` is an
  error under pytest.
- Tests run with no network and no credentials, and new ones must too. Model
  behaviour comes from `ReplayBackend` or a fixture, never a live call.
- A new runtime dependency needs a decision-log entry
  ([docs/decision-log.md](docs/decision-log.md)); the budget is deliberate.

## Before you finish

Run the suite. If you changed the demo's output strings, the assertions in
`tests/test_demo.py` and `tests/test_demo_notebook.py` are what check them, and
if you changed the breaker table, `tests/test_prompt_matches_breaker.py` checks
that the system prompt still tells the model the truth.
