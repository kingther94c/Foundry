"""git_status and git_diff: hardened invocations plus the session baseline.

Calling git on a repository is a code-execution surface: ``core.fsmonitor`` runs
a hook, a pager can spawn, and inherited ``GIT_*`` variables change behaviour.
These built-ins disable all of that, which is also why bare ``git`` is not on the
read-only command whitelist -- routing it through run_command would bypass this.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from foundry.core.conversation import ToolSchema
from foundry.core.errors import InvalidToolCall, ToolError
from foundry.core.tools.base import Operation, ToolContext, ToolKind, ToolOutput, truncate_middle
from foundry.core.winapi import CREATE_NO_WINDOW, IS_WINDOWS, decode_output

# A repository's own .git/config is attacker-controlled input whenever the repo
# arrived as an archive, off a share, or was touched by an approved command.
# Several git config keys turn `git diff` into a program launcher, so each is
# neutralized explicitly rather than trusted:
#   diff.external / diff.*.textconv / diff.*.command  -- run a program per file
#   filter.*.clean / .smudge                          -- run a program per blob
#   core.pager / core.editor / core.sshCommand        -- run a program
#   core.fsmonitor / core.hooksPath                   -- run a program/hook
_HARDENING = [
    "--no-pager",
    "-c", "core.fsmonitor=",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.pager=cat",
    "-c", "core.editor=false",
    "-c", "core.sshCommand=false",
    "-c", "diff.external=",
    "-c", "pager.diff=false",
    "-c", "pager.status=false",
    "-c", "protocol.ext.allow=never",
    "-c", "uploadpack.packObjectsHook=",
    # Without this, git escapes non-ASCII paths as "\303\251..." in porcelain
    # output, so a file with an accented or CJK name never matched the dirty
    # set and skipped the dirty-write guard.
    "-c", "core.quotepath=false",
]

# Applied to diff-family commands only: these reject configured helpers rather
# than merely blanking them.
_DIFF_HARDENING = ["--no-ext-diff", "--no-textconv"]

_DIFF_COMMANDS = frozenset({"diff", "show", "log", "format-patch"})


def _git_env(workspace: Path | None = None) -> dict[str, str]:
    """A minimal environment for git.

    ``run_command`` already filters credentials out of child environments; git
    was handing a subprocess the *full* environment, which matters exactly when
    a hostile config has convinced it to launch one.
    """
    from foundry.core.winapi import child_environment

    env = child_environment()
    env = {k: v for k, v in env.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    # Deliberately NOT set: GIT_CONFIG_NOSYSTEM. The system config is
    # administrator-owned and machine-local -- not the attacker-controlled input
    # the hardening above defends against, which is the repository's own
    # .git/config. Discarding it drops core.autocrlf=true, the default on every
    # Git-for-Windows install, and then every CRLF worktree file reads as
    # modified: the baseline calls a clean repo dirty, diffs come back as
    # whole-file rewrites, and change attribution reports nothing. Dangerous
    # system keys are neutralized by name in _HARDENING instead.
    return env


def run_git(args: list[str], cwd: Path, timeout_s: int = 30) -> tuple[int, str, str]:
    flags = CREATE_NO_WINDOW if IS_WINDOWS else 0
    hardening = list(_HARDENING)
    # safe.directory is scoped to this workspace rather than "*": the wildcard
    # switches off git's own ownership check everywhere.
    hardening += ["-c", f"safe.directory={cwd.as_posix()}"]
    extra = _DIFF_HARDENING if args and args[0] in _DIFF_COMMANDS else []
    try:
        proc = subprocess.run(
            ["git", *hardening, args[0], *extra, *args[1:]] if args else ["git", *hardening],
            cwd=str(cwd), env=_git_env(cwd),
            capture_output=True, timeout=timeout_s, creationflags=flags,
        )
    except FileNotFoundError as exc:
        raise ToolError("git is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"git {' '.join(args)} timed out") from exc
    return proc.returncode, decode_output(proc.stdout).text, decode_output(proc.stderr).text


@dataclass(frozen=True, slots=True)
class GitBaseline:
    """What the worktree looked like when the session began.

    Recorded so the final report can separate files this session touched from
    ones that were already dirty, and so a moved HEAD is detectable -- a model
    that commits mid-session would otherwise make the diff look clean.
    """

    head: str
    branch: str
    dirty_paths: frozenset[str] = frozenset()
    untracked_paths: frozenset[str] = frozenset()

    def as_payload(self) -> dict[str, Any]:
        return {
            "head": self.head,
            "branch": self.branch,
            "dirty_paths": sorted(self.dirty_paths),
            "untracked_paths": sorted(self.untracked_paths),
        }


def parse_porcelain_path(line: str) -> str | None:
    """Extract the path from one ``git status --porcelain=v2`` line.

    The path sits at a fixed field index, not at the end: splitting on
    whitespace mangles any path containing a space, and a rename line is
    ``<current>\\t<original>`` so taking the last tab field yields the path that
    no longer exists. Both produced dirty-file entries naming files that were
    not on disk, which meant the real file skipped the dirty-write guard.

        1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
        2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path>\\t<origPath>
        u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
        ? <path>
    """
    if not line:
        return None
    marker = line[0]
    if marker == "?":
        return line[2:].strip() or None
    if marker == "!":
        return None  # ignored file

    field_index = {"1": 8, "2": 9, "u": 10}.get(marker)
    if field_index is None:
        return None
    fields = line.split(" ", field_index)
    if len(fields) <= field_index:
        return None
    path = fields[field_index]
    if marker == "2":
        path = path.split("\t", 1)[0]  # the current path, not the original
    return path.strip() or None


def is_git_repository(path: Path) -> bool:
    code, out, _ = run_git(["rev-parse", "--is-inside-work-tree"], path)
    return code == 0 and out.strip() == "true"


def capture_baseline(path: Path) -> GitBaseline:
    code, head, err = run_git(["rev-parse", "HEAD"], path)
    head_sha = head.strip() if code == 0 else ""  # a repo with no commits yet

    code, branch, _ = run_git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    branch_name = branch.strip() if code == 0 else "(detached)"

    # porcelain=v2 also reports untracked files, which a plain `git diff` misses.
    code, status, err = run_git(["status", "--porcelain=v2", "--untracked-files=all"], path)
    if code != 0:
        raise ToolError(f"git status failed: {err.strip()}")

    dirty: set[str] = set()
    untracked: set[str] = set()
    for line in status.splitlines():
        path = parse_porcelain_path(line)
        if path is None:
            continue
        if line[0] == "?":
            untracked.add(path)
        else:
            dirty.add(path)

    return GitBaseline(head=head_sha, branch=branch_name,
                       dirty_paths=frozenset(dirty), untracked_paths=frozenset(untracked))


@dataclass(slots=True)
class GitStatus:
    name: str = "git_status"
    kind: ToolKind = ToolKind.READ_ONLY

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description="Show the working tree status of the workspace repository.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        )

    def validate(self, args: dict[str, Any]) -> Operation:
        if args:
            raise InvalidToolCall(f"unknown argument(s): {', '.join(sorted(args))}")
        return Operation(tool=self.name, kind=self.kind, args={},
                         display="git status", target="")

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        code, out, err = run_git(["status", "--short", "--branch"], ctx.workspace.root)
        if code != 0:
            raise ToolError(f"git status failed: {err.strip()}")
        body, truncated = truncate_middle(out or "(clean)", ctx.max_output_bytes)
        return ToolOutput(content=body, truncated=truncated)


@dataclass(slots=True)
class GitDiff:
    name: str = "git_diff"
    kind: ToolKind = ToolKind.READ_ONLY

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=(
                "Show uncommitted changes in the workspace repository. "
                "Example: {\"path\": \"src/app.py\", \"staged\": false}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Limit to one path."},
                    "staged": {"type": "boolean", "description": "Show staged changes instead."},
                },
                "additionalProperties": False,
            },
        )

    def validate(self, args: dict[str, Any]) -> Operation:
        extra = set(args) - {"path", "staged"}
        if extra:
            raise InvalidToolCall(f"unknown argument(s): {', '.join(sorted(extra))}")
        path = args.get("path", "")
        if not isinstance(path, str):
            raise InvalidToolCall("path must be a string")
        staged = args.get("staged", False)
        if not isinstance(staged, bool):
            raise InvalidToolCall("staged must be a boolean")
        return Operation(tool=self.name, kind=self.kind,
                         args={"path": path, "staged": staged},
                         display=f"git diff{' --staged' if staged else ''} {path}".strip(),
                         target=path)

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        args = ["diff"]
        if op.args["staged"]:
            args.append("--staged")
        if op.args["path"]:
            resolved = ctx.workspace.resolve(op.args["path"])
            args += ["--", resolved.relative]
        code, out, err = run_git(args, ctx.workspace.root)
        if code != 0:
            raise ToolError(f"git diff failed: {err.strip()}")
        body, truncated = truncate_middle(out or "(no changes)", ctx.max_output_bytes)
        artifact_id = ""
        if truncated and ctx.artifacts is not None:
            artifact_id = ctx.artifacts.put_text(out).artifact_id
            body += f"\n\n[full diff saved; read_artifact artifact_id=\"{artifact_id}\"]"
        return ToolOutput(content=body, truncated=truncated, artifact_id=artifact_id)


@dataclass(slots=True)
class GitEvidence:
    """Final-gate evidence: what changed, and whether HEAD moved underneath us."""

    baseline: GitBaseline
    head_now: str = ""
    changed: frozenset[str] = field(default_factory=frozenset)

    @property
    def head_moved(self) -> bool:
        # No guard on the baseline being non-empty: a repository with no commits
        # records head="", which switched this check off for the whole session
        # -- so a first commit made mid-run went undetected, and that is exactly
        # the case the check exists for. A repo that stays empty compares "" to
        # "" and is still False.
        return self.head_now != self.baseline.head

    @property
    def session_changed(self) -> frozenset[str]:
        """Files changed that were clean at baseline: attributable to this run."""
        return frozenset(self.changed - self.baseline.dirty_paths - self.baseline.untracked_paths)

    @property
    def preexisting_changed(self) -> frozenset[str]:
        return frozenset(self.changed & (self.baseline.dirty_paths | self.baseline.untracked_paths))


def collect_evidence(path: Path, baseline: GitBaseline) -> GitEvidence:
    code, head, _ = run_git(["rev-parse", "HEAD"], path)
    head_now = head.strip() if code == 0 else ""
    code, status, err = run_git(["status", "--porcelain=v2", "--untracked-files=all"], path)
    if code != 0:
        raise ToolError(f"git status failed: {err.strip()}")

    changed: set[str] = set()
    for line in status.splitlines():
        path = parse_porcelain_path(line)
        if path is not None:
            changed.add(path)

    return GitEvidence(baseline=baseline, head_now=head_now, changed=frozenset(changed))
