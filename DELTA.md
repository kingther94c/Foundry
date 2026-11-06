# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A Python repo map (D-046), the eighth step of the codex-parity
order. The new read-only `repo_map` tool outlines Python code with the
standard library's `ast`: a file's classes, fields, methods, signatures and
constants with the line each starts on, or, for a directory, a brief outline
with the files most imported by the others first, fitted to about 3,000
tokens in three tiers (outlines, a line of names, paths) so that nothing left
out goes unnamed. Parsing runs nothing in the file.

## Changes

- `core/tools/repomap.py` (new): `RepoMap`, `parse`, the import ranking
  (`_importers`), the brief and full outlines, the three-tier budget, the walk
  (pruned like `list_files`, and of hidden directories), the per-call file and
  time caps, and a cache by path, mtime and size.
- `core/tools/registry.py`: `repo_map` registered. `core/policy/engine.py`:
  a built-in read-only tool, allowed in every mode.
- `tests/test_repo_map.py` (new, 45 tests).
- Docs: D-046; design.md section 7 and the module tree; a threat-model row;
  README; roadmap (item 8 struck, items 9-11 named, the V2 candidates).

## Decisions and open points

- A tool, not a block in every request: a map nobody needs costs nothing, and
  the system prompt stays the same bytes across a resume.
- Ranked by imports, not by symbol references (aider's PageRank); the report
  can show whether more is needed.
- Python only: other languages need tree-sitter, which core does not take.
- With item 8 the codex-parity list is done. The roadmap now names the next
  gaps a codex user would notice: `/mode` and `/model` inside a session, a
  `notify` program at the end of a turn, and `exec --output-schema`.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  1947 passed (1902 before, plus 45 in `tests/test_repo_map.py`).
- Mutation check (scratch script): 51 mutants -- the if/try/with
  descent, type aliases, private constants, `__init__` kept, brief
  decorators, the decorator line, privacy inherited by members, the order and
  breaking of constant runs, constants in the one-line form, empty field
  lines, warnings silenced, the recursion guard, the summary paragraph,
  parsing bytes, relative import levels and the top, from-import submodules,
  package `__init__`, ambiguous names, self-imports, the most specific match,
  tests last, most imported first, the reserve, empty outlines, the line
  budget, path order, hidden, pruned, `site-packages` and `egg-info`
  directories, the size cap, the cache key and bound, the file and time caps,
  cancel, Python only, a file that does not parse, the workspace bound, both
  output caps, the row width, the empty path, the registry and the policy.
  Three survived the first run (a public class inside a private one, a name
  two files answer to, tests ranked last); tests were added or sharpened and
  all are caught.
- Not run against a live model: the local gateway answers 404.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/repo-map`, from main at `601650e`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/core/policy/engine.py` |
| M | `src/foundry/core/tools/registry.py` |
| A | `src/foundry/core/tools/repomap.py` |
| A | `tests/test_repo_map.py` |
<!-- auto:end -->
