# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

dont_ask asks nobody, and a hook is told what policy said (D-152, from P2):
in dont_ask an `ask` rule, the dirty-file guard and an unparseable command
are refused at step 3 instead of reaching the user or a PermissionRequest
hook; a PermissionRequest hook reads policy's reason from
`permission_reason`, which no call's arguments can write; and a patch hook
sees the paths the workspace resolved, not the model's spelling.

## Changes

- `core/policy/engine.py`: `evaluate`'s `finish` turns an ASK into a DENY in
  dont_ask, with the rule's reason and "(dont_ask: nobody is asked, so it is
  refused)".
- `core/runtime.py`: `_permission_request` passes `permission_reason`, and
  sets `tool_input.description` only for Foundry's own tools (none takes a
  description); an MCP call's own `description` is left alone.
- `core/hooks.py`: `hook_tool_input` gives a patch's `paths` beside codex's
  `command`.
- Docs: D-152, design (the policy steps), threat model (the hooks row).

## Decisions and open points

- `permission_reason` is Foundry's own field; codex's hooks read the
  reason, if at all, from `tool_input.description`, which Foundry's own
  tools still carry.
- `paths` are the runtime's resolved paths (`_canonicalize_paths`, the
  filesystem's spelling, 8.3 aliases resolved), not `normalize_path`'s
  lowercase: a hook comparing case-sensitively against a real name should
  see the real name.
- A rewrite still reads only `command`; the paths of the rewritten patch are
  resolved again.

## Verification

- `tests/test_dont_ask_never_asks.py` (6): an ask rule, the dirty-file guard
  and an unparseable command refused in dont_ask and asked otherwise; no
  hook, no user and no approval request at the REPL in dont_ask; a forged
  `description` in an MCP call kept as the call's and policy's reason beside
  it; a patch hook given `paths`, and the resolved spelling of `./A.PY`.
- `tests/test_hooks.py`, `tests/test_permission_hooks.py`: the exact
  payloads now include `paths` and `permission_reason`.
- Hook, policy and dont_ask tests (43 files): 2291 passed.
- Mutation check: 8 mutants, all killed (one, giving hooks the model's
  spelling, first survived against a lowercasing version that the runtime's
  own resolution made redundant; that version was dropped).
- Full suite (rebased on D-151): 4746 passed, 1 skipped.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
