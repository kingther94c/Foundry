# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A repository's skills stay in it, and sub-agents spend the task's budget
(D-157, from P2): a repository's skill must resolve inside the repository
wherever `.agents` leads; a skill's listing follows no junction and is
bounded; a sub-agent's calls and tokens are what the task had left, and
count as the task's; a writer stopped by Ctrl+C clears the session's reads.

## Changes

- `core/skills.py`: `load_skills` bounds a repository's skills by
  `repository_top(workspace)` resolved (`_resolved`), not by the folder they
  were found in; `_files` walks with `os.scandir`, skipping links, junctions
  and hidden entries, at most `MAX_WALKED` (5,000) entries.
- `cli/agents.py`: the child's `Budget` has the task's remaining calls and,
  under a limit, its remaining tokens; its calls are added to the task's in
  `finally`, where a writer's clearing of the read tracker now is too.
- `tests/test_skills_agents_contained.py` (13 tests).
- Docs: D-157; design (`spawn_agent`); threat model (the skills and
  sub-agent rows); roadmap (P2 done, with what it fixed).

## Decisions and open points

- Sub-agents still start unasked when read-only, and the tool is offered
  whenever they are on: the budget is the bound, and the threat model now
  says so instead of "offered only when the user asks".
- A sub-agent's rounds stay its own; each round is a call at least, so the
  task's calls bound them.

## Verification

- `tests/test_skills_agents_contained.py` (13): `.agents` or
  `.agents/skills` linked out of the repository is not used, a link to a
  folder inside it is; a repository reached through a junction keeps its
  skills, from a folder in it too; one that cannot be resolved offers none;
  a skill's listing follows no junction, leaves hidden files out and is
  ordered as before; the walk is bounded; a sub-agent stops at the task's
  remaining calls and the session then stops without asking the model again;
  five spawns in one turn with three calls start two; a sub-agent's token
  limit is the task's remainder, and none without one; a writer stopped
  part-way clears the session's reads and its calls are the task's; a reader
  stopped part-way leaves them.
- Skill and sub-agent tests: 148 passed.
- Mutation check: 17 mutants. One survived as equivalent: without the
  walk's bound at the folder level, `islice` already reads nothing more once
  the bound is reached, so no folder is added. One survived until a
  repository that cannot be resolved was tested; then killed.
- Full suite: 4808 passed, 1 skipped (21 min 36 s), with D-154 to D-156.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
