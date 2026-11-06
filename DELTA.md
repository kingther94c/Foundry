# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The answer as Markdown (D-070), item 31: on a terminal the model's text is
rendered a line at a time -- headings, emphasis, inline code, links, quotes,
and fenced code highlighted once the fence closes -- as codex renders it.
Off a terminal it is printed exactly as written; the escaping that showed
`list[int]` as `list\[int]` is gone.

## Changes

- `cli/markdown.py` (new): `MarkdownLines` (a line at a time, fences),
  `inline()` (emphasis, code, links).
- `cli/render.py`: `_text` / `_end_text`; `settle()` prints held text and
  takes the working line down; `cli/repl.py` `run_task` and `cli/app.py`
  `_close` call it.
- `tests/test_markdown.py` (new, 32 tests).
- Docs: D-070; design (the file tree, the UI notes); threat model (the
  terminal injection row); roadmap item 31.

## Decisions and open points

- A small fixed subset, not rich's Markdown (it centres headings in boxes,
  makes links terminal hyperlinks, and needs whole blocks).
- Each whole line is stripped of control sequences, so one split across
  two chunks is caught; output is `Text`, never markup; links show their
  address and are never hyperlinks.
- Seen through a forced terminal in tests, not by eye on a real console.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2473 passed, 1 skipped (2441 before, plus 32 in
  `tests/test_markdown.py`).
- Mutation check (scratch script): 20 mutants -- tilde fences, loose
  headings, closing hashes, heading levels, inline code, loose italics and
  bold, the link's address, not sanitized, any fence closing, an open fence
  or the last line lost, rules, quotes, the language, Markdown into a pipe,
  escaping twice, and `settle`, the event order and `run_task` keeping the
  text. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/markdown`, from main at `f5a3ab1`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| A | `src/foundry/cli/markdown.py` |
| M | `src/foundry/cli/render.py` |
| M | `src/foundry/cli/repl.py` |
| A | `tests/test_markdown.py` |
<!-- auto:end -->
