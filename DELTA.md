# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A security fix (D-049): every program Foundry starts is found on PATH and
started by its absolute path, never looked for in the current directory.
On Windows, a program named without a path is looked for in the current
directory before PATH -- CreateProcess and `shutil.which` both do -- and
Foundry is usually started in the repository it works on. A `git.exe` at the
top of a clone ran in git's place when a session began, before anything was
asked; a `powershell.exe` there ran every command. Found while building
roadmap item 11 (`notify`), which waits on this branch.

## Changes

- `core/winapi.py`: `find_program(name, env)` -- PATH only, relative entries
  skipped, PATHEXT as cmd.exe applies it, a name with a directory must be
  absolute; `taskkill_tree` uses it.
- `core/tools/git.py` (`run_git`), `core/tools/command.py` (`run_process`'s
  PowerShell), `core/mcp.py` (a server's command), `cli/app.py` (the doctor's
  PowerShell check): started by the absolute path it finds.
- `tests/test_program_search.py` (new, 22 tests): the resolver, and a
  program planted in the current directory -- a copy of `whoami.exe` named
  `git.exe`, `powershell.exe`, `taskkill.exe`, an MCP server's name -- not run
  by any of them.
- Docs: D-049; design.md (the command section); threat-model.md (a new row).

## Decisions and open points

- An absolute path at each launch site, rather than setting
  `NoDefaultCurrentDirectoryInExePath` for the whole process: that switch is
  invisible at the call it protects and inherited by every child.
- DLL loading is not covered: Windows looks in a program's own folder and the
  system folders before the current directory, which covers git, PowerShell
  and Python as installed.
- This test harness sets `NoDefaultCurrentDirectoryInExePath`, which hid the
  hole; the tests remove it to see what Windows does by default.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2019 passed, 1 skipped (1998 before; the skipped test is the
  POSIX execute-bit check).
- Mutation check (scratch script): 13 mutants -- git, PowerShell, an
  MCP server, `taskkill` and the doctor started by bare name again; relative
  PATH entries, a relative name with a directory, quoted entries, the PATH
  variable's case, the process environment as the default, PATHEXT taken
  as is, a folder taken for a program, and git's missing-program error. All
  are caught.
- Reproduced before the fix, with `NoDefaultCurrentDirectoryInExePath`
  unset: `subprocess.run(["git", "--version"])` in a folder holding a planted
  `git.exe` ran it, and `shutil.which("uvx", path=PATH)` returned `.\uvx.EXE`.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/program-search`, from main at `cb2f54b`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/mcp.py` |
| M | `src/foundry/core/tools/command.py` |
| M | `src/foundry/core/tools/git.py` |
| M | `src/foundry/core/winapi.py` |
| A | `tests/test_program_search.py` |
<!-- auto:end -->
