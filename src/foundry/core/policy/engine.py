"""The policy pipeline.

    0  circuit breaker      hard-coded, precedes everything
    1  pre_tool callback    optional; a rewrite re-enters at step 0
    2  DENY rules           a DENY at any layer cannot be undone by any ALLOW
    3  ASK rules            built-ins live here (dirty-file writes)
    4  mode baseline        default / accept_edits / plan / dont_ask
    5  ALLOW rules          read-only built-ins; persisted approvals
    6  interactive approval  headless and dont_ask resolve this to DENY

Where each default sits matters more than it looks. Read-only tools are allowed
by *built-in rules at step 5*, and mutators simply fall through to step 6 --
writing "mutator defaults to ASK" as a step-3 rule would make accept_edits
unable to allow anything and make persisted approvals dead on arrival, since ask
beats allow. The dirty-file guard is a step-3 rule precisely so it outranks
accept_edits.
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable

from foundry.core.policy.segmenter import (
    effective_argv_candidates,
    SegmentedCommand,
    canonicalize,
    effective_argv,
    paranoid_segments,
    segment_command,
)
from foundry.core.tools.base import Operation, ToolKind


class Verdict(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class Mode(str, Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "accept_edits"
    PLAN = "plan"
    DONT_ASK = "dont_ask"


class Layer(str, Enum):
    BUILTIN = "builtin"
    MANAGED = "managed"
    USER = "user"
    PROJECT = "project"


POLICY_VERSION = 1


def normalize_path(path: str) -> str:
    """One spelling for a workspace-relative path.

    Model-supplied envelopes, git porcelain output, and rule patterns all name
    the same files differently; comparing raw strings has repeatedly let an
    alternate spelling slip past a guard.
    """
    parts: list[str] = []
    for part in path.replace("\\", "/").lower().split("/"):
        if part in ("", "."):
            continue
        if part == ".." and parts:
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


@dataclass(frozen=True, slots=True)
class Rule:
    tool: str            # tool name or "*"
    pattern: str         # fnmatch against the operation target, "*" for any
    verdict: Verdict
    layer: Layer = Layer.USER
    rule_id: str = ""
    reason: str = ""

    def matches(self, op: Operation, targets: tuple[set[str], ...] | None = None) -> bool:
        """Match against every part of the operation.

        ``targets`` is one entry per part that will actually run -- a command
        segment, or a path a patch touches. Each entry is the set of acceptable
        spellings for that part (canonical and raw), and a rule matches a part
        if it matches any spelling of it.

        An ALLOW must cover *every* part: matching the joined string instead is
        the classic allowlist bypass, since ``pytest -q; rm -r ~`` starts with
        ``pytest``. A DENY or ASK fires if any part matches, which only makes
        them stricter.
        """
        if self.tool not in ("*", op.tool):
            return False
        parts = targets if targets is not None else ({op.target},)

        def part_matches(spellings: set[str]) -> bool:
            return any(fnmatch.fnmatch(s, self.pattern) for s in spellings)

        if self.verdict is Verdict.ALLOW:
            return all(part_matches(p) for p in parts)
        # DENY and ASK also match the whole command line, so a pattern that
        # spans a separator (`*;*`, `git status; *`) still works. Dropping it
        # made such rules match nothing at all, silently.
        return (any(part_matches(p) for p in parts)
                or fnmatch.fnmatch(op.target, self.pattern))

    def identity(self) -> str:
        return self.rule_id or f"{self.layer.value}:{self.tool}({self.pattern})={self.verdict.value}"


@dataclass(frozen=True, slots=True)
class Decision:
    verdict: Verdict
    reason: str
    rule_id: str = ""
    step: int = 0
    policy_digest: str = ""
    operation_digest: str = ""


# --- circuit breaker ------------------------------------------------------
# Hard-coded, unreachable by any rule, mode, or callback. Entries are written in
# canonical form because the segmenter canonicalizes aliases before matching.

# Matched per path component: a substring check for ".git/" misses a path whose
# final component is itself .git.
PROTECTED_WRITE_NAMES = frozenset({".git", ".foundry"})

DESTRUCTIVE_GIT = (
    ("git", "checkout", "--"),
    ("git", "restore"),
    ("git", "reset", "--hard"),
    ("git", "clean"),
    ("git", "stash", "drop"),
    ("git", "stash", "clear"),
)

# Subcommands that publish or move HEAD. `pull` earns its place the hard way:
# the system prompt told the model "merge is always refused" while `git pull`
# -- which performs exactly that merge -- passed the table. cherry-pick, revert
# and am rewrite the tree the same way and were named nowhere.
HISTORY_MOVING_GIT = ("push", "commit", "rebase", "merge", "pull",
                      "cherry-pick", "revert", "am")

RECURSIVE_DELETE_HEADS = ("remove-item", "format-volume", "clear-disk")

# Matched against each argument on its own. Anchoring against the joined string
# meant a switch written after the path pushed the target off the end:
# `Remove-Item -Recurse -Force C:\` was caught, `Remove-Item -Force C:\ -Recurse`
# was not, and PowerShell binds parameters in any order.
_DANGEROUS_DELETE_TARGET = re.compile(
    r"^['\"]?"
    r"(\\\\[?.]\\)?"                               # \\?\ and \\.\ prefixes
    r"("
    r"[a-z]:[\\/]?|"                              # a drive root
    r"[a-z]:[\\/](windows|users|program files).*|"  # a system tree
    r"~[\\/]?|/|"                                  # home or root
    # Unexpanded variables, in both spellings PowerShell accepts. Only the bare
    # `$name` form was listed, so `"${HOME}"` -- which expands identically --
    # walked past a table whose whole purpose is to be categorical.
    r"\$\{?(home|profile|pwd)\}?[\\/]?|"
    r"\$\{?env:userprofile\}?[\\/]?"
    r")['\"]?$",
    re.IGNORECASE,
)


#: ``git clean`` options that consume the next argument. Without this, that
#: argument is read as a flag -- and `git clean -fd -e -n` looked like a dry run
#: while deleting untracked files.
_CLEAN_VALUE_OPTIONS = frozenset({"-e", "--exclude"})


def _is_dry_run(args: tuple[str, ...]) -> bool:
    """True only for git's own dry-run spellings: ``--dry-run``, ``-n``, or a
    short-flag cluster containing n (``-xdn``). Deliberately exact -- anything
    looser would be a hole in the one table that is meant to be categorical.

    Option values are skipped. `-e`'s pattern argument is arbitrary text, and
    when it happened to be ``-n`` this read the deletion as a dry run: the
    exemption added to make `git clean -n` usable opened a hole in the entry it
    was carved out of.
    """
    index = 0
    while index < len(args):
        arg = args[index]
        if arg.lower() in _CLEAN_VALUE_OPTIONS:
            index += 2                      # the option and the value it takes
            continue
        if arg == "--dry-run" or arg == "-n":
            return True
        if len(arg) > 1 and arg[0] == "-" and arg[1] != "-" and arg[1:].isalpha():
            if "n" in arg[1:]:
                return True
        index += 1
    return False


def categorical_denials() -> tuple[str, ...]:
    """The breaker's own account of what it refuses, for the system prompt.

    Hand-writing this paragraph let it drift: it named `merge` as always
    refused while `git pull` passed, and omitted eight shapes the table does
    deny, which the model could only discover by being refused.
    """
    destructive = ", ".join(" ".join(form[1:]) for form in DESTRUCTIVE_GIT)
    return (
        "writing to .git, .foundry, or Foundry's own configuration",
        f"git {destructive} (git clean -n is allowed -- it only lists)",
        "git " + ", ".join(HISTORY_MOVING_GIT),
        "git apply (use apply_patch, which checks anchors and read-before-edit)",
        "git switch --force/--discard-changes, rm --force, filter-branch, "
        "checkout-index, read-tree --reset, worktree remove, branch --delete, "
        "update-ref/symbolic-ref -d, reflog expire",
        "recursive deletion of a system or home directory",
    )


@dataclass(frozen=True, slots=True)
class BreakerHit:
    reason: str


def check_breaker(op: Operation, segmented: SegmentedCommand | None = None) -> BreakerHit | None:
    """Returns a hit if the operation is categorically forbidden."""
    if op.kind is ToolKind.MUTATOR and op.tool == "apply_patch":
        for path in op.args.get("paths", []):
            components = {p.lower() for p in path.replace("\\", "/").split("/")}
            if components & PROTECTED_WRITE_NAMES:
                return BreakerHit(f"writes to {path} are never permitted")

    if op.tool != "run_command":
        return None

    parsed = segmented or segment_command(op.target)

    # Two readings: Foundry's parse, and a deliberately naive split that ignores
    # quotes and comments entirely. A forbidden command found under *either* is
    # refused. The naive reading exists because four review rounds each found a
    # construct Foundry mis-lexed, and it cannot be fooled by a fifth: it makes
    # no assumptions to be wrong about. It can only add denials.
    raw_readings: list[tuple[str, ...]] = [segment.argv for segment in parsed.segments]
    raw_readings.extend(paranoid_segments(op.target))

    # A git argv has more than one defensible reading of where the subcommand
    # sits, depending on whether an option's value is consumed. Both are
    # compared, because either alone loses a denial the other catches.
    readings: list[tuple[str, ...]] = []
    for raw in raw_readings:
        readings.extend(effective_argv_candidates(raw) if raw else ())

    for argv_candidate in readings:
        raw_argv = argv_candidate
        if not raw_argv:
            continue
        # Canonicalize the head and drop git's global options, so `git -C .
        # reset --hard` and `git.exe reset --hard` compare the same as the
        # plain form. Indexing raw argv is how these tables get bypassed.
        #
        # Leading commas are stripped because PowerShell's array operator lets
        # any non-first argument be written `,--hard`: git receives `--hard`,
        # but the token this table compares against was `,--hard`. Every
        # flag-keyed entry below was bypassable that way.
        argv = tuple(a.lower().lstrip(",") for a in raw_argv)
        head = argv[0] if argv else ""

        for forbidden in DESTRUCTIVE_GIT:
            if argv[:len(forbidden)] != forbidden:
                continue
            # `git clean -n` only lists what would be removed. Refusing it with
            # "destroys uncommitted work" was false, and it is the one form that
            # lets the model check before asking. The exemption is per-reading,
            # so the naive reading still denies if only the lexed one saw the
            # flag -- an attacker has to get -n past both, and git then sees it
            # too and does nothing.
            if forbidden == ("git", "clean") and _is_dry_run(argv[2:]):
                break
            return BreakerHit(
                f"'{' '.join(forbidden)}' destroys uncommitted work and is never permitted"
            )

        # `git checkout <name>` is ambiguous by design -- git cannot tell a
        # branch from a pathspec either, which is why it added `switch` and
        # `restore`. Since the pathspec form discards uncommitted work, the
        # whole shape is refused and the model is pointed at `git switch`.
        if head == "git" and len(argv) > 2 and argv[1] == "checkout":
            return BreakerHit(
                "'git checkout' can discard uncommitted work and is never permitted; "
                "use 'git switch' to change branches"
            )

        # ...and `git switch` itself discards work when forced. Recommending it
        # above while leaving the forced form uncovered pointed the model
        # straight at the gap.
        if head == "git" and len(argv) > 1 and argv[1] == "switch":
            if any(a in ("--discard-changes", "-f", "--force") for a in argv[2:]):
                return BreakerHit(
                    "'git switch --discard-changes/--force' throws away uncommitted "
                    "work and is never permitted"
                )

        # Other ways to destroy the working tree or rewrite history.
        if head == "git" and len(argv) > 1:
            subcommand = argv[1]
            if subcommand == "rm" and any(a in ("-f", "--force") for a in argv[2:]):
                return BreakerHit("'git rm --force' is never permitted")
            if subcommand in ("filter-branch", "checkout-index"):
                return BreakerHit(f"'git {subcommand}' is never permitted")
            if subcommand == "read-tree" and "--reset" in argv[2:]:
                return BreakerHit("'git read-tree --reset' discards work and is never permitted")
            if subcommand == "worktree" and len(argv) > 2 and argv[2] == "remove":
                return BreakerHit("'git worktree remove' is never permitted")
            # argv is lowercased here, so -D and -d are indistinguishable.
            # Branch management is a non-goal anyway, so both are refused.
            if subcommand == "branch" and any(a in ("-d", "--delete") for a in argv[2:]):
                return BreakerHit("deleting a branch is never permitted")
            if subcommand in ("update-ref", "symbolic-ref") and "-d" in argv[2:]:
                return BreakerHit(f"'git {subcommand} -d' is never permitted")
            if subcommand == "reflog" and len(argv) > 2 and argv[2] == "expire":
                return BreakerHit("'git reflog expire' destroys recovery data and is never permitted")

        if head in RECURSIVE_DELETE_HEADS:
            recursive = any(a.startswith(("-r", "/s", "-recurse", "/q")) for a in argv[1:])
            if recursive and any(_DANGEROUS_DELETE_TARGET.match(a) for a in argv[1:]):
                return BreakerHit("recursive delete of a system or home directory is never permitted")

        if head == "git" and len(argv) > 1 and argv[1] in HISTORY_MOVING_GIT:
            return BreakerHit(
                f"'git {argv[1]}' is not permitted; Foundry never publishes or rewrites history"
            )

        # `git apply` edits the working tree without passing the read-before-edit
        # check, the dirty-file guard, or the anchored-patch parser -- every
        # protection apply_patch exists to apply.
        if head == "git" and len(argv) > 1 and argv[1] == "apply":
            return BreakerHit(
                "'git apply' bypasses the patch tool's checks; use apply_patch instead"
            )

        joined_lower = " ".join(argv)
        if ".foundry" in joined_lower and head in RECURSIVE_DELETE_HEADS:
            return BreakerHit("modifying Foundry's own configuration is never permitted")

    return None


# --- built-in rules -------------------------------------------------------

READ_ONLY_TOOLS = ("list_files", "search_text", "read_file", "read_artifact",
                   "git_status", "git_diff", "finish")


def builtin_rules() -> tuple[Rule, ...]:
    return tuple(
        Rule(tool=name, pattern="*", verdict=Verdict.ALLOW, layer=Layer.BUILTIN,
             rule_id=f"builtin.readonly.{name}", reason="read-only tool")
        for name in READ_ONLY_TOOLS
    )


PreToolHook = Callable[[Operation], "Decision | Operation | None"]


@dataclass(slots=True)
class PolicyEngine:
    rules: list[Rule] = field(default_factory=lambda: list(builtin_rules()))
    mode: Mode = Mode.DEFAULT
    interactive: bool = True
    dirty_files: set[str] = field(default_factory=set)
    pre_tool: PreToolHook | None = None
    session_grants: set[str] = field(default_factory=set)

    # -- rule management --------------------------------------------------

    def add_rule(self, rule: Rule) -> None:
        self.rules.append(rule)

    def add_rules(self, rules: Iterable[Rule]) -> None:
        self.rules.extend(rules)

    def grant_for_session(self, op: Operation) -> None:
        """In-memory only: a session grant never touches a config file."""
        self.session_grants.add(self._grant_key(op))

    @staticmethod
    def _grant_key(op: Operation) -> str:
        return f"{op.tool}\x00{op.target}\x00{op.digest}"

    def policy_digest(self) -> str:
        joined = "|".join(sorted(r.identity() for r in self.rules))
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]

    def dead_rules(self, known_tools: Iterable[str]) -> list[Rule]:
        """Authored rules that can never fire, reported at startup.

        A deny that matches nothing is silent, and a silent deny reads as
        protection that is not there. Built-ins are generated from the same
        constant as the tool registry, so they are never user error.
        """
        names = set(known_tools)
        return [
            r for r in self.rules
            if r.layer is not Layer.BUILTIN and r.tool != "*" and r.tool not in names
        ]

    # -- the pipeline -----------------------------------------------------

    def evaluate(self, op: Operation, *, _depth: int = 0) -> tuple[Decision, Operation]:
        digest = op.digest or hashlib.sha256(
            f"{op.tool}\x00{op.target}".encode("utf-8")).hexdigest()[:16]

        def finish(verdict: Verdict, reason: str, rule_id: str, step: int) -> tuple[Decision, Operation]:
            return Decision(verdict=verdict, reason=reason, rule_id=rule_id, step=step,
                            policy_digest=self.policy_digest(), operation_digest=digest), op

        # Step 0 -- circuit breaker.
        segmented = segment_command(op.target) if op.tool == "run_command" else None
        hit = check_breaker(op, segmented)
        if hit:
            return finish(Verdict.DENY, hit.reason, "breaker", 0)

        # Rules match per segment for commands, and per touched path for
        # patches, so nothing can hide behind a chain or a second file.
        #
        # A segment contributes both its canonical and its raw spelling, and a
        # rule need only match one of them: requiring both would make any rule
        # for an aliased or path-qualified command impossible to write (a
        # pattern cannot match "get-childitem src" and "dir src" at once),
        # pushing users toward pattern="*".
        if segmented is not None and segmented.segments:
            targets = tuple({s.canonical, s.text} for s in segmented.segments)
        elif op.tool == "apply_patch":
            targets = tuple({p} for p in op.args.get("paths", ())) or ({op.target},)
        else:
            targets = ({op.target},)

        # Step 1 -- pre_tool hook. A rewrite restarts the pipeline so the breaker
        # and every rule bind the final input, not the original.
        if self.pre_tool and _depth == 0:
            outcome = self.pre_tool(op)
            if isinstance(outcome, Decision):
                if outcome.verdict is Verdict.ALLOW:
                    pass  # a hook ALLOW does not skip DENY/ASK rules
                else:
                    return outcome, op
            elif isinstance(outcome, Operation) and outcome != op:
                return self.evaluate(outcome, _depth=_depth + 1)

        # Step 2 -- DENY rules.
        for rule in self.rules:
            if rule.verdict is Verdict.DENY and rule.matches(op, targets):
                return finish(Verdict.DENY, rule.reason or "denied by rule", rule.identity(), 2)

        # Step 3 -- ASK rules, including the dirty-file guard.
        if op.tool == "apply_patch":
            # Both sides normalized: the envelope's spelling comes from the
            # model, dirty_files comes from git porcelain. Comparing raw
            # strings let './src/app.py' and 'src\app.py' skip the guard that
            # exists precisely to outrank accept_edits.
            dirty = {normalize_path(p) for p in self.dirty_files}
            touched = [p for p in op.args.get("paths", []) if normalize_path(p) in dirty]
            if touched:
                return finish(
                    Verdict.ASK,
                    f"{', '.join(touched)} had uncommitted changes when this session started",
                    "builtin.dirty_file", 3,
                )
        for rule in self.rules:
            if rule.verdict is Verdict.ASK and rule.matches(op, targets):
                return finish(Verdict.ASK, rule.reason or "approval required", rule.identity(), 3)

        # An unparseable command can never be auto-allowed: the safety valve.
        if segmented is not None and not segmented.trusted:
            return finish(
                Verdict.ASK,
                f"command cannot be safely parsed ({segmented.untrusted_reason})",
                "builtin.unparseable", 3,
            )

        # Step 4 -- mode baseline.
        if self.mode is Mode.PLAN and op.kind is ToolKind.MUTATOR:
            return finish(Verdict.DENY,
                          "plan mode: no changes until the plan is approved",
                          "mode.plan", 4)
        if self.mode is Mode.ACCEPT_EDITS and op.tool == "apply_patch":
            return finish(Verdict.ALLOW, "accept_edits: workspace patch", "mode.accept_edits", 4)

        # Step 5 -- ALLOW rules and session grants.
        if self._grant_key(op) in self.session_grants:
            return finish(Verdict.ALLOW, "approved earlier in this session", "session_grant", 5)

        # A single rule need not cover the whole command: every part must be
        # covered by *some* allow rule. Requiring one rule to match all parts
        # meant two rules each covering half a chain allowed nothing, and no
        # number of rules could ever cover `git status; python -m pytest` --
        # while the prompt tells the model to chain with `;` because
        # PowerShell 5.1 has no `&&`. Safety is unchanged: a part with no allow
        # rule still blocks the whole command, and DENY/ASK ran earlier.
        allow_rules = [r for r in self.rules if r.verdict is Verdict.ALLOW]
        matched_by: list[str] = []
        for part in targets:
            covering = next((r for r in allow_rules if r.matches(op, (part,))), None)
            if covering is None:
                matched_by = []
                break
            matched_by.append(covering.identity())
        if matched_by:
            unique = sorted(set(matched_by))
            return finish(Verdict.ALLOW,
                          "every part of this command is covered by an allow rule",
                          unique[0] if len(unique) == 1 else "+".join(unique), 5)

        # Step 6 -- interactive approval, or fail closed.
        if self.mode is Mode.DONT_ASK or not self.interactive:
            return finish(Verdict.DENY,
                          "no matching allow rule and approval is unavailable",
                          "fail_closed", 6)
        return finish(Verdict.ASK, "no rule matched; asking for approval", "interactive", 6)
