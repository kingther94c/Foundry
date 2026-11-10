"""How long a recorded session is kept (OQ-19, D-163).

``runtime.session_retention_days`` is the user's to set: 0, the default, keeps
every session; N removes, as a session starts, each session whose journal was
last written more than N days ago. A repository or a ``foundry mcp-server``
client may not set it: lowering it deletes the user's journals.

What is never removed: an archived session (D-128) -- archiving one is the
user saying "keep this", and archived sessions live outside ``sessions/``; a
session a store holds, in this process or another; a folder that is not a
session's, by its name and its journal; and anything reached through a link
or a junction, which could lead outside the store.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from foundry.core.errors import SessionLocked
from foundry.core.resume import delete_session

DAY_S = 86_400
# What new_session_id makes: the start time, then eight hex digits.
_SESSION_ID = re.compile(r"\d{8}T\d{6}-[0-9a-f]{8}")


@dataclass(frozen=True, slots=True)
class Sweep:
    """What one pass removed, and what it would have but left: a session in
    use, or one the file system would not let go of, tried again next time."""
    removed: tuple[str, ...] = ()
    left: tuple[str, ...] = ()


def last_written(session_dir: Path) -> float:
    """When the session's journal was last appended to. Not the folder's
    time: opening a store, or asking whether one is open, writes its lock
    file, and a listing would make every session it showed look new."""
    return (session_dir / "events.jsonl").stat().st_mtime


def expired(sessions: Path, days: int, now: float | None = None) -> list[Path]:
    """The session folders last written more than ``days`` days ago, oldest
    first; none for 0."""
    if days <= 0:
        return []
    try:
        entries = list(sessions.iterdir())
    except OSError:
        return []  # no sessions yet, or none this user may list
    cutoff = (time.time() if now is None else now) - days * DAY_S
    found: list[tuple[float, Path]] = []
    for entry in entries:
        if not _SESSION_ID.fullmatch(entry.name):
            continue
        try:
            if entry.is_symlink() or entry.is_junction() or not entry.is_dir():
                continue
            written = last_written(entry)
        except OSError:
            continue  # no journal: not a session's folder, or not one to judge
        if written < cutoff:
            found.append((written, entry))
    return [entry for _, entry in sorted(found)]


def remove(folders: list[Path]) -> Sweep:
    """Remove these sessions' folders, as foundry delete does (D-128). Never
    raises for one: a session in use, or one that cannot be removed, is left
    for the next pass, and whatever called this goes on."""
    removed: list[str] = []
    left: list[str] = []
    for folder in folders:
        try:
            delete_session(folder)       # refuses a session a store holds
        except (SessionLocked, OSError):
            left.append(folder.name)
            continue
        removed.append(folder.name)
    return Sweep(tuple(removed), tuple(left))


def sweep(sessions: Path, days: int, *, now: float | None = None) -> Sweep:
    """The pass a session makes as it starts: the expired sessions removed."""
    return remove(expired(sessions, days, now))
