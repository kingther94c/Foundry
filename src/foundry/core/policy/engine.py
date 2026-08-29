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

from foundry.core.policy.segmenter import SegmentedCommand, canonicalize, segment_command
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


@dataclass(frozen=True, slots=True)
class Rule:
    tool: str            # tool name or "*"
    pattern: str         # fnmatch against the operation target, "*" for any
    verdict: Verdict
    layer: Layer = Layer.USER
    rule_id: str = ""
    reason: str = ""

    def matches(self, op: Operation) -> bool:
        if self.tool not in ("*", op.tool):
            return False
        return fnmatch.fnmatch(op.target, self.pattern)

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

PROTECTED_WRITE_FRAGMENTS = (".git/", ".git\\", ".foundry/", ".foundry\\")

DESTRUCTIVE_GIT = (
    ("git", "checkout", "--"),
    ("git", "restore"),
    ("git", "reset", "--hard"),
    ("git", "clean"),
    ("git", "stash", "drop"),
    ("git", "stash", "clear"),
)

RECURSIVE_DELETE_HEADS = ("remove-item", "format-volume", "clear-disk")

_DANGEROUS_DELETE_TARGETS = re.compile(
    r"(^|[\s'\"])([a-z]:[\\/]?$|[a-z]:[\\/](windows|users|program files)|~[\\/]?$|/$)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class BreakerHit:
    reason: str


def check_breaker(op: Operation, segmented: SegmentedCommand | None = None) -> BreakerHit | None:
    """Returns a hit if the operation is categorically forbidden."""
    if op.kind is ToolKind.MUTATOR and op.tool == "apply_patch":
        for path in op.args.get("paths", []):
            normalized = path.replace("\\", "/").lower()
            if any(frag.replace("\\", "/") in f"/{normalized}" for frag in PROTECTED_WRITE_FRAGMENTS):
                return BreakerHit(f"writes to {path} are never permitted")

    if op.tool != "run_command":
        return None

    parsed = segmented or segment_command(op.target)
    for segment in parsed.segments:
        argv = tuple(a.lower() for a in segment.argv)
        head = canonicalize(segment.argv[0]) if segment.argv else ""

        for forbidden in DESTRUCTIVE_GIT:
            if argv[:len(forbidden)] == forbidden:
                return BreakerHit(
                    f"'{' '.join(forbidden)}' destroys uncommitted work and is never permitted"
                )

        if head in RECURSIVE_DELETE_HEADS:
            joined = " ".join(segment.argv[1:])
            recursive = any(a.startswith(("-r", "/s", "-recurse")) for a in argv[1:])
            if recursive and _DANGEROUS_DELETE_TARGETS.search(joined):
                return BreakerHit("recursive delete of a system or home directory is never permitted")

        if head == "git" and len(argv) > 1 and argv[1] in ("push", "commit", "rebase", "merge"):
            return BreakerHit(
                f"'git {argv[1]}' is not permitted; Foundry never publishes or rewrites history"
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
            if rule.verdict is Verdict.DENY and rule.matches(op):
                return finish(Verdict.DENY, rule.reason or "denied by rule", rule.identity(), 2)

        # Step 3 -- ASK rules, including the dirty-file guard.
        if op.tool == "apply_patch":
            touched = [p for p in op.args.get("paths", []) if p in self.dirty_files]
            if touched:
                return finish(
                    Verdict.ASK,
                    f"{', '.join(touched)} had uncommitted changes when this session started",
                    "builtin.dirty_file", 3,
                )
        for rule in self.rules:
            if rule.verdict is Verdict.ASK and rule.matches(op):
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
        for rule in self.rules:
            if rule.verdict is Verdict.ALLOW and rule.matches(op):
                return finish(Verdict.ALLOW, rule.reason or "allowed by rule", rule.identity(), 5)

        # Step 6 -- interactive approval, or fail closed.
        if self.mode is Mode.DONT_ASK or not self.interactive:
            return finish(Verdict.DENY,
                          "no matching allow rule and approval is unavailable",
                          "fail_closed", 6)
        return finish(Verdict.ASK, "no rule matched; asking for approval", "interactive", 6)
