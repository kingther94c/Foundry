# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Images (D-045), the seventh step of the codex-parity order. The model can be
shown an image: `-i/--image` attaches one to the first task of `run`, `resume`
or `exec`, `/image PATH` to the next message in a session, the new read-only
`view_image` tool looks at one in the workspace, and an MCP tool's image
content now rides on its result. Each image is checked before it is sent
(type from its bytes, 3.75 MB, 8000 px), counted as 1,600 tokens, kept once
as an artifact and journaled by id, and rebuilt by a resume.
`backend.images = false` turns it all off for a text-only model.

## Changes

- `core/conversation.py`: `ImageBlock`; `ToolResultBlock.images` (additive).
- `core/images.py` (new): sniffing PNG/JPEG/GIF/WebP and their pixel size
  from the header, the size and side limits, `load`, `from_base64`,
  `data_url`. `core/errors.py`: `ImageRejected`.
- `core/tools/image.py` (new): `view_image`; registered unless
  `backend.images` is false, and a built-in read-only tool in the policy.
- `core/context.py`: an image counts as `IMAGE_TOKEN_ESTIMATE`; masking drops
  an old result's images; `append_user` and `append_tool_result` take images;
  `restore` keeps them.
- `core/runtime.py`: `run_turn(text, images)`; a tool's `ToolOutput.images`
  reach the result; `_block_payload` journals an image by artifact id
  (`_store_image`, once per image, `put_exact` so a known secret is not kept);
  `user_input` and `tool_reply` records carry `images` when there are any.
- `core/resume.py`: `image_loader` reads images back from the artifacts;
  `decode_block` rebuilds image blocks and result images; a missing one
  becomes a note.
- Adapters: Anthropic puts images in the user message and inside
  `tool_result`; Chat Completions and Responses send a run of results'
  images as the user message after them.
- `core/mcp.py`: image content that passes the checks is passed on (at most
  eight), when `ToolContext.images`.
- `core/config.py`: `backend.images` (bool). `cli/app.py`: `-i/--image` on
  run, resume and exec; wiring. `cli/repl.py`: `/image`, `attach`,
  `take_images`.
- `tests/test_images.py` (new, 40 tests); `tests/test_mcp.py` runs the mixed
  content test with images off.
- Docs: D-045; design.md sections 1, 2, 7, 8 and 9; threat-model rows;
  README; roadmap.

## Decisions and open points

- Nothing is resized: core takes no imaging library. An image over the
  limits is refused with the reason.
- OpenAI-family tool results take text only, so images follow the results as
  a user message (codex's form); Responses' newer image parts in
  `function_call_output` are not used until the gateway is seen to take them.
- A compaction keeps the user's words, not their images.
- Deferred: pasting an image into the terminal, PDFs, resizing.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  1902 passed (1862 before, plus 40 in `tests/test_images.py`).
- Mutation check (scratch script): 55 mutants -- the header parsers (JPEG fill
  bytes and end marker, PNG IHDR, WebP variants), the byte, side, zero-size
  and base64 checks, the token estimate, masking, restore, the order of a
  user's images, the runtime's journal records and artifact ids, a secret
  image not kept, the resume loader and notes, each adapter's placement and
  flushes, the workspace bound, the registry and policy, MCP's collection,
  cap and return, `/image` and `-i` on run and exec, the config check and the
  wiring. Five survived the first run (a JPEG end marker, PNG without IHDR,
  the base64 length precheck, the workspace bound, an MCP image returned);
  tests were sharpened or added and all 55 are caught.
- Not run against a live model: the local gateway answers 404.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/images`, from main at `c03827d`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/backends/anthropic.py` |
| M | `src/foundry/core/backends/openai_compat.py` |
| M | `src/foundry/core/backends/responses.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/context.py` |
| M | `src/foundry/core/conversation.py` |
| M | `src/foundry/core/errors.py` |
| A | `src/foundry/core/images.py` |
| M | `src/foundry/core/mcp.py` |
| M | `src/foundry/core/policy/engine.py` |
| M | `src/foundry/core/resume.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/tools/base.py` |
| A | `src/foundry/core/tools/image.py` |
| M | `src/foundry/core/tools/registry.py` |
| A | `tests/test_images.py` |
| M | `tests/test_mcp.py` |
<!-- auto:end -->
