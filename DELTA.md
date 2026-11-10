# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`core/runtime.py` split into the loop and the mixins it calls out to (D-159,
P3): the user's hooks, history edits, the goal and notifier, and how a task
ends each move, as they were, to a mixin `AgentRuntime` is made of.

## Changes

- `core/runtime.py` (1,739 lines -> about 1,070): the loop -- sampling, a
  call's dispatch and execution, replies, approvals, the audit -- and
  `Budget`, `FailureTracker`.
- New mixins, each `__slots__ = ()`: `core/runtime_hooks.py` (`HookCalls`),
  `core/runtime_history.py` (`HistoryEditing`: compaction, rewind, a side
  question), `core/runtime_goal.py` (`GoalAndNotices`),
  `core/runtime_ending.py` (`TaskEnding`, and `TurnOutcome`).
- `core/errors.py`: `explain(exc)`, which was `runtime._explain`.
- Tests: `test_transport_audit` imports `explain` from `core/errors`.
- Docs: D-159; design (the package tree); roadmap (P3); the threat model's
  links to moved methods.

## Decisions and open points

- Mixins rather than objects of their own: one object, one set of fields,
  nothing to keep in step; `AgentRuntime` keeps its slots (checked: an
  instance has no `__dict__`).
- The section markers of the class that no longer head anything are gone;
  the one over replies, approvals and the audit says so.

## Verification

- `AgentRuntime`'s fields, methods and MRO checked; an instance has no
  `__dict__`.
- Runtime, hook, compaction, rewind, side, goal and notify tests: 658 passed.
- Mutation check: 11 mutants of the moved code -- a PreToolUse block,
  omitted hook fields, background hooks closed, rewind's reads, an aside's
  plan, compaction charged to the goal, goal spending and cancelling, a
  cancel spent, a degraded journal said, a provider's detail -- all killed
  (the degraded journal by `tests/test_patch_robustness.py`).
- Full suite: 4808 passed, 1 skipped (22 min 38 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
