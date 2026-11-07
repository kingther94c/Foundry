# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Agent skills, as codex's (D-113), item 74: SKILL.md folders -- the user's
own and a trusted repository's `.agents/skills` -- listed to the model and
loaded with `load_skill` when a task matches one; `$name` sends one with a
message, `/skills` lists them.

## Changes

- `core/skills.py` (new): `load_skills`, `front_matter`, `skill_list`,
  `list_budget`, `SkillTool` (`load_skill`), `register`.
- `core/context.py`: `ContextManager.skills`, in the system message after the
  instructions; `core/policy/engine.py`: `load_skill` read-only.
- `cli/app.py`: skills found and registered at build, listed in the system
  prompt, what is not right said at the start; a repository with skills is
  asked about trust; `Wiring.skills`.
- `cli/repl.py`: `attach_skills` (`$name`), `/skills`.
- `tests/test_skills.py` (new, 74 tests).
- Docs: D-113; README; design; threat model; roadmap item 74.

## Decisions and open points

- The user's own win a name; a repository folder leading outside it is not
  used; the list is 2% of the window (2,000 to 40,000 characters),
  descriptions shortened to 160 first, then skills left out.
- Not done: `[[skills.config]]`, bundled/admin skills, `.agents/skills` in
  folders between the working directory and the repository's top.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3716 passed, 1 skipped (3642 before, plus 74 in
  `tests/test_skills.py`).
- Mutation check (scratch script): 52 mutants over the name rule,
  where skills come from and which wins, links, the front matter, the list's
  budget and cuts, `load_skill`'s paths, text and output, the policy, the
  system prompt, trust, the notices, `$name` and `/skills`. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-74`, from main at `c047491`.

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
| M | `src/foundry/core/context.py` |
| M | `src/foundry/core/policy/engine.py` |
| A | `src/foundry/core/skills.py` |
| A | `tests/test_skills.py` |
<!-- auto:end -->
