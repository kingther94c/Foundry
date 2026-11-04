"""Undoing what a session's patches changed (D-036).

For every file it changes, apply_patch records the content the file held
before -- an exact copy, kept as an artifact -- and a digest of what it wrote.
Undo puts the earlier content back, newest change first, but only where the
file still holds exactly what the session wrote. A file edited since, by the
user or by a command, is left alone and named, and so is everything the
session did to it before that edit: reverting an earlier patch would discard
the later change.

What undo cannot reach is said, never guessed at: what commands changed (no
record says which files they touched), content that held a credential (not
kept, because a redacted copy written back would corrupt the file), and a
patch interrupted before its result was recorded.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from foundry.core.errors import UndoError
from foundry.core.policy.engine import normalize_path
from foundry.core.session import ArtifactStore, EventType, JournalRecord, SessionStore
from foundry.core.tools.git import GitBaseline
from foundry.core.workspace import PathRejected, Workspace

_DIGEST = re.compile(r"[0-9a-f]{64}")
_TMP_SUFFIX = ".foundry-tmp"  # the suffix apply_patch writes through

# Why a change cannot be undone.
CHANGED_SINCE = "it changed after the session wrote it"
CHANGED_BETWEEN = "it changed between the session's patches (by a command, or outside the session)"
NOT_KEPT_CREDENTIAL = "what it held before held a credential, so that content was not kept"
NOT_KEPT = "what it held before was not kept"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha(value: Any) -> str | None:
    return value if isinstance(value, str) and _DIGEST.fullmatch(value) else None


@dataclass(frozen=True, slots=True)
class Change:
    """One file one patch call changed."""

    result: int  # the ordinal of the tool_result that recorded it
    call_id: str
    path: str
    before: str | None  # artifact id of the content before; None if absent or not kept
    before_sha: str | None
    after: str | None  # digest of what the patch left; None if it removed the file
    kept: bool
    partner: str | None = None  # the other end of a move, in the same result

    @property
    def key(self) -> tuple[int, str]:
        return (self.result, self.path)

    @property
    def created(self) -> bool:
        """The file did not exist before this patch."""
        return self.kept and self.before is None


def _change(result: int, call_id: str, entry: Any) -> Change | None:
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
        return None
    before = _sha(entry.get("before"))
    kept = entry.get("kept") is True
    partner = entry.get("moved_to") or entry.get("moved_from")
    return Change(result=result, call_id=call_id, path=entry["path"], before=before,
                  before_sha=_sha(entry.get("before_sha")), after=_sha(entry.get("after")),
                  # A recorded "kept" with nothing to restore from is not kept.
                  kept=kept and (before is not None or entry.get("before_sha") is None),
                  partner=partner if isinstance(partner, str) else None)


def recorded_changes(records: Iterable[JournalRecord]) -> list[Change]:
    """Every change a patch recorded that no undo has reverted, oldest first."""
    changes: list[Change] = []
    reverted: set[tuple[Any, Any]] = set()
    for record in records:
        if record.type == EventType.TOOL_RESULT:
            entries = record.payload.get("changes")
            if isinstance(entries, list) and isinstance(record.ordinal, int):
                call_id = str(record.payload.get("call_id", ""))
                changes.extend(c for c in (_change(record.ordinal, call_id, e) for e in entries)
                               if c is not None)
        elif record.type == EventType.UNDO:
            for entry in record.payload.get("reverted") or []:
                if isinstance(entry, dict):
                    reverted.add((entry.get("result"), entry.get("path")))
    return [c for c in changes if c.key not in reverted]


def patched_paths(records: Iterable[JournalRecord]) -> frozenset[str]:
    """Every file a patch changed or was asked to change, normalized."""
    paths: set[str] = set()
    for record in records:
        if record.type == EventType.TOOL_RESULT:
            for entry in record.payload.get("changes") or []:
                if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                    paths.add(normalize_path(entry["path"]))
        elif record.type == EventType.TOOL_CALL:
            for path in record.payload.get("paths") or []:
                if isinstance(path, str):
                    paths.add(normalize_path(path))
    return frozenset(paths)


# --- planning --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Step:
    """One file put back: to ``restore``'s content, or removed if that is None."""

    path: str
    restore: str | None
    restore_sha: str | None
    expect: str | None  # what the file must hold now; None: it must not exist
    reverts: tuple[Change, ...]

    @property
    def action(self) -> str:
        if self.restore is not None:
            return "restore"
        return "remove" if self.expect is not None else "none"


@dataclass(frozen=True, slots=True)
class Skipped:
    path: str
    reason: str
    changes: tuple[Change, ...]


@dataclass(frozen=True, slots=True)
class Plan:
    steps: tuple[Step, ...]
    skipped: tuple[Skipped, ...]

    @property
    def empty(self) -> bool:
        return not self.steps and not self.skipped


class _Unreadable:
    pass


_UNREADABLE = _Unreadable()


def _state(path: Path) -> str | None | _Unreadable:
    """A file's digest, None if nothing is there, or unreadable."""
    try:
        if not os.path.lexists(path):
            return None
        if not path.is_file():
            return _UNREADABLE
        return _digest(path.read_bytes())
    except OSError:
        return _UNREADABLE


def _suffix(chain: list[Change], workspace: Workspace,
            artifacts: ArtifactStore) -> tuple[int, str]:
    """Where the run of changes that can be reverted starts, and why not earlier.

    Walked from the newest change back: the file must hold what the newest
    wrote, each change's earlier content must have been kept, and each must
    have started from what the one before it left.
    """
    newest = chain[-1]
    try:
        target = workspace.resolve(newest.path, for_write=True).absolute
    except PathRejected as exc:
        return len(chain), str(exc)
    if _state(target) != newest.after:
        return len(chain), CHANGED_SINCE
    for i in range(len(chain) - 1, -1, -1):
        change = chain[i]
        if not change.kept:
            reason = NOT_KEPT_CREDENTIAL if change.before_sha else NOT_KEPT
            return i + 1, reason
        if change.before is not None and not artifacts.has(change.before):
            return i + 1, NOT_KEPT
        if i and chain[i - 1].after != change.before_sha:
            return i, CHANGED_BETWEEN
    return 0, ""


def plan_undo(changes: list[Change], workspace: Workspace, artifacts: ArtifactStore, *,
              last: bool = False) -> Plan:
    """What undoing ``changes`` would do; ``last`` keeps only the newest patch's."""
    if last and changes:
        newest = max(c.result for c in changes)
        changes = [c for c in changes if c.result == newest]
    chains: dict[str, list[Change]] = {}
    for change in changes:
        chains.setdefault(normalize_path(change.path), []).append(change)
    start: dict[str, int] = {}
    reason: dict[str, str] = {}
    for key, chain in chains.items():
        start[key], reason[key] = _suffix(chain, workspace, artifacts)

    # A move is reverted whole or not at all: restoring its source while the
    # destination stays would leave two copies, and removing the destination
    # while the source stays deleted would lose the file.
    settled = False
    while not settled:
        settled = True
        reverting = {c.key for key, chain in chains.items() for c in chain[start[key]:]}
        for key, chain in chains.items():
            for i in range(len(chain) - 1, start[key] - 1, -1):
                change = chain[i]
                if change.partner and (change.result, change.partner) not in reverting:
                    start[key] = i + 1
                    reason[key] = f"it was moved to or from {change.partner}, which cannot be undone"
                    settled = False
                    break

    steps: list[Step] = []
    skipped: list[Skipped] = []
    for key, chain in chains.items():
        first = start[key]
        if first:
            skipped.append(Skipped(chain[-1].path, reason[key], tuple(chain[:first])))
        if first < len(chain):
            oldest, newest = chain[first], chain[-1]
            steps.append(Step(path=newest.path, restore=oldest.before,
                              restore_sha=oldest.before_sha, expect=newest.after,
                              reverts=tuple(chain[first:])))
    return Plan(steps=tuple(sorted(steps, key=lambda s: s.path)),
                skipped=tuple(sorted(skipped, key=lambda s: s.path)))


# --- applying --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Outcome:
    reverted: tuple[Step, ...]
    failed: tuple[Skipped, ...]


def apply_undo(plan: Plan, workspace: Workspace, artifacts: ArtifactStore) -> Outcome:
    """Carry the plan out, checking each file again just before it is written."""
    done: list[Step] = []
    failed: list[Skipped] = []
    for step in plan.steps:
        target: Path | None = None
        try:
            target = workspace.resolve(step.path, for_write=True).absolute
            if _state(target) != step.expect:
                failed.append(Skipped(step.path, CHANGED_SINCE, step.reverts))
                continue
            if step.restore is not None:
                data = artifacts.read_bytes(step.restore)
                if _digest(data) != step.restore_sha:
                    failed.append(Skipped(step.path, "the content kept for it is damaged",
                                          step.reverts))
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + _TMP_SUFFIX)
                tmp.write_bytes(data)
                os.replace(tmp, target)
            elif step.expect is not None:
                target.unlink()
            done.append(step)
        except (OSError, KeyError, PathRejected) as exc:
            failed.append(Skipped(step.path, f"{type(exc).__name__}: {exc}", step.reverts))
            if target is not None:
                tmp = target.with_name(target.name + _TMP_SUFFIX)
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
    return Outcome(reverted=tuple(done), failed=tuple(failed))


def undo_payload(plan: Plan, outcome: Outcome, *, scope: str, by: str) -> dict[str, Any]:
    """The ``undo`` record: which recorded changes were reverted, and which not."""
    return {
        "scope": scope,  # all | last
        "by": by,  # cli | repl
        "reverted": [{"result": c.result, "path": c.path}
                     for step in outcome.reverted for c in step.reverts],
        "files": [{"path": s.path, "action": s.action} for s in outcome.reverted],
        "skipped": [{"path": s.path, "reason": s.reason,
                     "results": sorted({c.result for c in s.changes})}
                    for s in plan.skipped + outcome.failed],
    }


# --- a session's journal ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class UndoSource:
    session_id: str
    workspace: str
    first_task: str
    baseline: GitBaseline | None
    changes: tuple[Change, ...]
    patched: frozenset[str]


def read_source(path: Path) -> UndoSource:
    """What undo needs from a journal. Only its own records: a journal resume
    could not read may still be undone."""
    try:
        records = list(SessionStore.read_records(path))
    except (OSError, UnicodeDecodeError) as exc:
        raise UndoError(f"cannot read the journal: {exc}") from exc
    meta = next((r for r in records if r.type == EventType.SESSION_META), None)
    if meta is None:
        raise UndoError("the journal has no session header")
    base = next((r for r in records
                 if r.type == EventType.GIT_BASELINE and "phase" not in r.payload), None)
    try:
        baseline = GitBaseline.from_payload(base.payload) if base is not None else None
    except ValueError:
        baseline = None
    task = next((r.payload.get("text") for r in records if r.type == EventType.USER_INPUT), "")
    return UndoSource(session_id=str(meta.payload.get("session_id", path.parent.name)),
                      workspace=str(meta.payload.get("workspace", "")),
                      first_task=task if isinstance(task, str) else "",
                      baseline=baseline,
                      changes=tuple(recorded_changes(records)),
                      patched=patched_paths(records))


def other_changes(source: UndoSource, current: GitBaseline) -> list[str]:
    """Files changed since the session started that no patch of it touched:
    a command's doing, or someone else's. Undo does not reach them."""
    if source.baseline is None:
        return []
    before = {normalize_path(p) for p in
              source.baseline.dirty_paths | source.baseline.untracked_paths}
    return sorted(p for p in current.dirty_paths | current.untracked_paths
                  if normalize_path(p) not in before and normalize_path(p) not in source.patched)


def undo_note(outcome: Outcome) -> str:
    """What the model is told about an undo it did not ask for."""
    restored = [s.path for s in outcome.reverted if s.action == "restore"]
    removed = [s.path for s in outcome.reverted if s.action == "remove"]
    parts = []
    if restored:
        parts.append("these files hold what they held before your patches again: "
                     + ", ".join(restored))
    if removed:
        parts.append("these files your patches created were removed: " + ", ".join(removed))
    if not parts:
        return ""
    return ("The user undid changes you made with apply_patch; " + "; ".join(parts)
            + ". Read a file again before relying on its content or patching it.")
