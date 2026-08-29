"""Workspace path containment.

Every file tool resolves its path through :meth:`Workspace.resolve`. The rules
below exist because a lexical prefix check is not containment on Windows: 8.3
short names, case folding, junctions (which ``islink`` does not report), device
names, alternate data streams, and drive-relative forms all defeat ``startswith``.

Tool inputs are workspace-relative by contract, so an absolute path is rejected
at the surface rather than compared -- an escape you cannot express is better
than one you have to detect.

Known accepted risks (threat model): check-then-act races, and hard links, which
no path-level check can see.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePath

# CON, PRN, AUX, NUL and the numbered device families, matched on the stem so
# "NUL.txt" is caught too.
_RESERVED_STEMS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class PathRejected(ValueError):
    """A path was refused before any filesystem effect."""


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    """A validated location inside the workspace."""

    absolute: Path
    relative: str  # forward slashes, for display and journaling


class Workspace:
    def __init__(self, root: Path) -> None:
        resolved = Path(os.path.realpath(root))
        if not resolved.is_dir():
            raise PathRejected(f"workspace root is not a directory: {root}")
        self.root = resolved
        self._root_key = os.path.normcase(str(self.root))
        # Paths that are inside the workspace but must never be written by tools.
        self._protected = ("\\.git\\", "/.git/", "\\.foundry\\", "/.foundry/")

    # -- validation -------------------------------------------------------

    def _reject_syntax(self, raw: str) -> None:
        if not raw or not raw.strip():
            raise PathRejected("empty path")
        if "\x00" in raw:
            raise PathRejected("path contains a null byte")

        text = raw.replace("\\", "/")
        if text.startswith("//") or raw.startswith("\\\\"):
            raise PathRejected(f"UNC and device paths are not allowed: {raw!r}")
        if raw.startswith(("\\\\?\\", "\\\\.\\")):
            raise PathRejected(f"extended-length/device paths are not allowed: {raw!r}")
        if PurePath(raw).is_absolute() or (len(raw) > 1 and raw[1] == ":"):
            # Covers both "C:/x" (absolute) and "C:x" (drive-relative).
            raise PathRejected(
                f"paths must be workspace-relative, got absolute or drive-relative: {raw!r}"
            )

        for part in text.split("/"):
            if not part or part == ".":
                continue
            if part == "..":
                continue  # handled by containment check after resolution
            if ":" in part:
                raise PathRejected(f"alternate data streams are not allowed: {raw!r}")
            if part != part.rstrip(". "):
                raise PathRejected(f"trailing dot or space is not allowed: {part!r}")
            if part.split(".")[0].lower() in _RESERVED_STEMS:
                raise PathRejected(f"reserved Windows device name: {part!r}")

    def _check_reparse_points(self, target: Path) -> None:
        """Walk from the root down, refusing any reparse point on the way.

        The target environment cannot create symlinks and has no Developer Mode,
        so a reparse point inside a workspace is either pre-existing or hostile;
        V1 refuses all of them rather than trying to classify safe ones.
        """
        try:
            relative = target.relative_to(self.root)
        except ValueError:
            return
        current = self.root
        for part in relative.parts:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                return  # not yet created: nothing to traverse
            except OSError as exc:
                raise PathRejected(f"cannot inspect path component {current}: {exc}") from exc
            attrs = getattr(info, "st_file_attributes", 0)
            if attrs & _REPARSE:
                raise PathRejected(f"reparse point (symlink/junction) in path: {current}")

    def _assert_contained(self, candidate: Path) -> None:
        # realpath resolves junctions and expands 8.3 short names; normcase then
        # makes the comparison case-insensitive, as NTFS is by default.
        real = Path(os.path.realpath(candidate))
        real_key = os.path.normcase(str(real))
        try:
            common = os.path.commonpath([self._root_key, real_key])
        except ValueError as exc:  # different drives
            raise PathRejected(f"path is outside the workspace: {candidate}") from exc
        if common != self._root_key:
            raise PathRejected(f"path escapes the workspace: {candidate}")

    # -- public API -------------------------------------------------------

    def resolve(self, raw: str, *, for_write: bool = False) -> ResolvedPath:
        self._reject_syntax(raw)
        candidate = (self.root / raw).absolute()
        self._check_reparse_points(candidate)
        self._assert_contained(candidate)

        real = Path(os.path.realpath(candidate))
        relative = os.path.relpath(real, self.root).replace("\\", "/")

        if for_write:
            marker = os.path.normcase(str(real))
            if any(p in marker for p in self._protected):
                raise PathRejected(f"writes to {relative} are never permitted")

        return ResolvedPath(absolute=real, relative=relative)

    def contains(self, path: Path) -> bool:
        try:
            self._assert_contained(path)
        except PathRejected:
            return False
        return True
