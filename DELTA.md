# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The Chat Completions and Responses adapters read what a real gateway sends
(D-200), the first step of P13. Six replies the local gateway gave in
2026-09, the first tool calls Foundry received from a real model, join the
suite byte for byte, and the faults a review of the adapters against that
traffic found are fixed: null or object arguments, a call listed only in the
terminal event, a finished item that drops what was streamed, a cut or
refused answer taken for a finished one, an empty completion taken for an
answer, and a tool call's index quoted or missing.

## Changes

- `core/backends/base.py`: `wire_arguments`, a call's arguments as JSON text.
- `core/backends/responses.py`: `_stop_reason` for both paths; the stream
  keeps arguments given on the added item, takes the whole of them from
  `function_call_arguments.done`, fills a finished item from what was
  streamed, takes from the terminal event's list what the stream did not
  carry, and treats a completion with nothing in it as a dropped stream.
  `_parse_output` skips what is not an object and reads null as empty.
- `core/backends/openai_compat.py`: `_slot_of`, which call a streamed
  fragment belongs to; calls in the order of their numbers; object arguments;
  what is not an object is skipped.
- `tests/fixtures/live_gateway/foundry_*.raw`: the six recordings, from the
  archived round, byte for byte.
- Tests: `tests/test_gateway_wire_shapes.py` (52, new);
  `tests/test_verify_kit.py`, one more: the recorded fixtures hold nothing
  shaped like a credential or a machine; `tests/test_reasoning.py`, one stand-in
  reply that was an empty completion now streams a word.
- `docs/decision-log.md`: D-200. `docs/roadmap.md`: P13's first step landed,
  the suite's size. `docs/design.md`: an empty stream is a dropped one.

## Decisions and open points

- A status of `incomplete` with no reason given is a tool-use turn when the
  turn has calls, because the local gateway marks every tool-call turn that
  way; with no calls it is a cut answer.
- The runtime still only records the stop reason. Acting on it -- going on
  after a cut, refusing to run a cut call, stopping on a refusal -- is P13's
  third step.
- Of the shapes handled, three are in the recordings; the rest come from the
  review and the protocols' documented events, and the tests build them.
- Open: nothing landed here has met a real Chat Completions or Responses
  endpoint. The Chat Completions batch the user starts is the first that
  will.

## Verification

- Full suite as CI runs it (`python -X warn_default_encoding -m pytest tests -q`),
  on main at 6f74e7e: 5175 passed, 1 skipped, of 5,176; 21 min 34 s on
  Windows. Moved onto d97460c (CI's annotations), the tree collected
  5,182, and the whole suite passed on that stack (D-201's notes). Moved
  again onto 8a81793 (mutate.py's own bytecode cache), it collects 5,183,
  the figure the roadmap names; the whole suite ran on the new stack's
  tip (D-203's notes).
- Before the change: the new tests run against the adapters of 6f74e7e
  (with only `wire_arguments` added to `base.py`, so that the file imports)
  gave 23 failed, 29 passed. The six recordings passed, as did the cases
  the old code already read right. What failed: null and object arguments;
  a call listed after streamed text, one the stream did not carry, one
  begun and not finished there; a finished item without arguments, the
  whole of the arguments in place of their pieces, arguments on the added
  item; every cut or refusal; the empty completion; and quoted, missing or
  non-number indexes.
- Mutation check, `verify/mutate.py` against a copy of the tree, judged by
  `tests/test_gateway_wire_shapes.py` alone: 45 breakages of `base.py`,
  `responses.py` and `openai_compat.py` -- each rule of the stop reason, each
  fallback of the stream, the empty-completion guard, each branch of
  `_slot_of`, the sort -- all killed, none survived.
- The six recordings are byte for byte what the archived round holds
  (sha256 compared), and git keeps them as they are (`-text`). Read through
  for anything shaped like a credential, a path of one machine, an address
  or a name: the gateway's agent name and the replies' own ids, nothing
  else; `tests/test_verify_kit.py` now looks again.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-review-planning-a401a7`, from main at `8a81793`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/core/backends/base.py` |
| M | `src/foundry/core/backends/openai_compat.py` |
| M | `src/foundry/core/backends/responses.py` |
| A | `tests/fixtures/live_gateway/foundry_chat_followup_stream.raw` |
| A | `tests/fixtures/live_gateway/foundry_chat_toolcall_nonstream.raw` |
| A | `tests/fixtures/live_gateway/foundry_chat_toolcall_stream.raw` |
| A | `tests/fixtures/live_gateway/foundry_responses_followup_stream.raw` |
| A | `tests/fixtures/live_gateway/foundry_responses_toolcall_nonstream.raw` |
| A | `tests/fixtures/live_gateway/foundry_responses_toolcall_stream.raw` |
| A | `tests/test_gateway_wire_shapes.py` |
| M | `tests/test_reasoning.py` |
| M | `tests/test_verify_kit.py` |
<!-- auto:end -->
