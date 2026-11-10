# A restricted-token sandbox on Windows, measured (2026-11-10)

[D-003](../decision-log.md) says V1 is a trusted host and claims no sandbox.
The roadmap kept one V2 candidate open: research into a restricted-token
sandbox, the kind codex's unelevated Windows sandbox level is built on. This
is that research ([D-165](../decision-log.md)): a prototype, what it measured,
and what it would and would not stop.

## The question

Could Foundry run the model's commands so that they write only inside the
workspace, without an administrator, an extra service or a local account --
and would git, Python, pytest and npm still work there?

codex's Windows sandbox has three levels. The two strong ones need an
administrator once: dedicated local users (one with the network blocked by
Windows Filtering Platform filters), ACLs written per workspace, an elevated
helper. The unelevated level is a restricted token ([codex.md](codex.md)).
The probe builds the kind that fits the question, a **write-restricted
token**: the user's own token, privileges dropped, with restricting SIDs --
not codex's exact token, whose flags this research did not read. Windows
checks every write
twice -- once against the user, once against the restricting SIDs alone -- and
a folder is writable only if an ACE grants one of those SIDs. Reads are
checked only against the user.

## The prototype

[`scripts/sandbox_probe.py`](../../scripts/sandbox_probe.py), stdlib and
ctypes only. It makes a temporary folder with a `work` and a `temp` folder
(the command's workspace and its `TEMP`), an `outside` folder of the user's,
and a folder whose ACL lets Everyone write; starts the command with
`CreateProcessAsUserW` under one of three tokens; and runs a child that
attempts each check and reports what happened. Everything it changes is under
that temporary folder, plus a registry key and a file named for the run, all
removed after.

The three configurations:

| | token | the workspace and its `TEMP` |
|---|---|---|
| **write-restricted** | `WRITE_RESTRICTED`, `DISABLE_MAX_PRIVILEGE`, `LUA_TOKEN`; restricting SIDs: a SID made up for the run, Everyone, the logon SID | an inheritable ACE grants the run's SID full control |
| **write-restricted, low integrity** | the same, at low integrity | the same, and labelled low integrity |
| **low integrity** | `DISABLE_MAX_PRIVILEGE`, `LUA_TOKEN`, at low integrity; no restricting SIDs | labelled low integrity |

Measured on Windows 11 Pro 10.0.26200, Python 3.12.10, Git for Windows,
Node 24 / npm 11, from an elevated session of a local administrator.

```
python scripts/sandbox_probe.py                                   # the checks, each configuration
python scripts/sandbox_probe.py --mode "low integrity" -- python -m pytest
```

## What a command could do

| check | write-restricted | + low integrity | low integrity |
|---|---|---|---|
| write the workspace | allowed | allowed | allowed |
| write its `TEMP` | allowed | allowed | allowed |
| write a folder it made private, as `tempfile.mkdtemp` does | **denied** | **denied** | allowed |
| write a folder of the user's, not granted | denied | denied | denied |
| write a folder whose ACL lets Everyone write | **allowed** | denied | denied |
| write the user's own temp folder | denied | denied | denied |
| **delete** a file of the user's, not granted | **allowed** | denied | denied |
| rename a file of the user's, not granted | denied | denied | denied |
| create a key under `HKCU\Software` | denied | denied | denied |
| a process it starts writes a folder not granted | denied | denied | denied |
| read a file of the user's, not granted | allowed | allowed | allowed |
| connect to a port on this machine | allowed | allowed | allowed |
| decrypt a secret DPAPI protects for the user | allowed | allowed | allowed |

And the tools, each in the workspace:

| | write-restricted | + low integrity | low integrity |
|---|---|---|---|
| `git init`, `commit`, `add`, `log` | works | works | works |
| Python writing `__pycache__` | works | works | works |
| pytest with `tmp_path` | **fails** (`PermissionError`) | **fails** | works |
| `pip --version` | works | works | works |
| `npm --version`, `npm init -y` | works | works | works |
| PowerShell writing a file | works | works | works |

## What the measurements say

1. **A write-restricted token alone lets a command delete whatever the user
   can delete.** `DELETE` is not one of the rights the second check covers:
   `os.remove` of a file outside the workspace succeeded, while writing,
   renaming into or creating beside it was refused. A command could not
   plant anything outside the workspace, but `rm -rf` of the user's home
   would go through.

2. **Python does not start under a write-restricted token unless Everyone is
   a restricting SID.** Without it the process fails with
   `STATUS_DLL_INIT_FAILED`; neither the user's SID, Users, Authenticated
   Users, INTERACTIVE nor RESTRICTED in its place is enough (`cmd` starts
   either way). The token's default DACL must name the restricting SIDs too.
   So everything whose ACL lets Everyone write, the command can write.

3. **A write-restricted token cannot write into folders Python makes
   private.** Since Python 3.12.4, `os.mkdir(path, 0o700)` -- and so
   `tempfile.mkdtemp` and pytest's `tmp_path` -- gives the folder a protected
   ACL naming only SYSTEM, Administrators and OWNER RIGHTS. The second check
   matches none of them (OWNER RIGHTS as a restricting SID does not help), so
   the command cannot write into a folder it just made, and a test suite that
   uses `tmp_path` fails. For Foundry, whose main loop is running the tests,
   that alone rules the write-restricted configurations out.

4. **Low integrity alone does what the write-restricted token was for.** A
   low-integrity process may not write *or delete* anything labelled above
   it, and everything not labelled is medium. With the workspace and its
   `TEMP` labelled low, git, Python, pytest, npm and PowerShell worked, and
   nothing outside could be written, deleted or renamed. Folders Python makes
   private inherit the low label, so `tmp_path` works.

5. **None of them is a sandbox.** In every configuration the command reads
   whatever the user can read (other repositories, `~/.ssh`, browser
   profiles), connects anywhere the user can, and **decrypts what DPAPI
   protects for the user** -- including Foundry's own stored credential
   (`auth.json`). Reading a secret and sending it out is exactly what a
   write fence leaves open. Blocking the network needs Windows Filtering
   Platform filters and so an administrator (codex's elevated level), or an
   AppContainer, which also cuts reading everything not granted to it,
   including a Python installed under the user's profile; neither was
   measured here.

## What low integrity would cost

- **The label stays.** Labelling the workspace low is a change to the user's
  folder that outlives the session: files made there later, by the user too,
  inherit it. Removing it means walking the tree again at the end, and a
  crash leaves it.
- **Other low-integrity processes could write the workspace.** Some
  sandboxed programs run at low integrity; a low label lets them write there
  too.
- **Some tools would break where this probe did not look**: anything that
  writes to the user's profile (a global npm install, `pip install --user`,
  a tool's cache under `%LOCALAPPDATA%`) is refused, as every write outside
  is. Each would need a folder of its own, labelled low, or to fail.
- **Interprocess paths were not measured.** A low-integrity process cannot
  send window messages to a higher one, but COM and RPC servers running at
  medium integrity are a known way out for low-integrity code; a design would
  have to account for them.
- The measurements are from an elevated administrator's session. A
  non-elevated session differs in what `LUA_TOKEN` removes, and in who owns
  the folders it makes; the probe should be run there before anything relies
  on these results.

## Conclusion

Not adopted for V1; D-003 stands -- Foundry claims no sandbox. What the
research leaves is a narrower, honest candidate for V2: an opt-in **write
fence** at low integrity, disclosed as exactly that -- the model's commands
cannot write or delete outside the workspace -- and not as a sandbox, since
reading, the network and the user's DPAPI secrets stay open. A
write-restricted token, as built here, is not worth carrying over: it lets a
command delete the user's files, and it breaks pytest.
