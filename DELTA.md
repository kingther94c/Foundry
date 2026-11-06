# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Thinking made against an old prefix is left out of later requests (D-048),
item 10 of the codex-parity order. Anthropic binds each thinking block to
everything sent before it; Foundry's own edits -- masking, a rebuilt
permissions paragraph, a resume's new system prompt -- invalidate the blocks
after them, and until now every later request sent them again for the API to
drop and the runtime to warn about, round after round. The context now
compares each projection with the last request sent and leaves those blocks
out from the first difference on; the history keeps every reply as received.

D-047's entry is corrected: it said such a request is refused with a 400, but
Foundry's requests set `drop_block` (D-032), so the blocks were dropped.

## Changes

- `core/context.py`: `unbind`; `ContextManager.mark_sent`, `_bind`, the last
  request sent and the positions left without their thinking, a cache of
  stripped replies; `restore(..., sent=)`; a compaction resets it.
- `core/runtime.py`: the conversation's own request is marked as sent.
- `core/resume.py`: `JournalState.last_sent`; the fold puts back a reply as
  received when the first request to carry it had already stripped it.
- `cli/app.py`: a resume restores the last request sent.
- `tests/test_thinking_binding.py` (new, 26 tests); `tests/test_resume.py`
  restores with the last request sent, as `build()` does.
- Docs: D-048 and the D-047 correction; design.md (masking); roadmap item 10.

## Decisions and open points

- Stripping is in the projection, not the history: the journal's requests
  are what was sent, and a resume rebuilds the same projection.
- `drop_block` stays on as the net for an edit the comparison misses.
- Anthropic's server-side context editing could replace masking on that
  protocol; deferred.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  1998 passed (1972 before, plus 26 in
  `tests/test_thinking_binding.py`).
- Mutation check (scratch script): 23 mutants -- redacted thinking,
  assistant messages only, the provider, an empty opaque block, the same
  object when nothing changes, malformed data, positions already left out,
  finding the edit, stripping from the edit and to the end, the first edit
  only, remembering the positions, the compaction reset, the cache, restore's
  last request and positions, the runtime marking its request, the fold's
  reply as received and its check, forgetting at a compaction, `last_sent`
  read and reset, and the app passing it. All are caught.
- One earlier full run stopped making progress and was killed after 40
  minutes, without output that names the test; the rerun passed in 3m36s
  and every file ran. Worth watching in CI.
- Not run against a live model: the local gateway answers 404.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/thinking-binding`, from main at `c7bcb50`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/context.py` |
| M | `src/foundry/core/resume.py` |
| M | `src/foundry/core/runtime.py` |
| M | `tests/test_resume.py` |
| A | `tests/test_thinking_binding.py` |
<!-- auto:end -->
