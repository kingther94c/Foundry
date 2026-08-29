"""Read-only file tools: list_files, search_text, read_file, read_artifact.

Output caps and windowing follow SWE-agent's ablations: a ~100-250 line window
with line numbers beat both a 30-line window and whole-file reads, and a search
that caps results and says so beat one that silently truncated.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from foundry.core.conversation import ToolSchema
from foundry.core.errors import InvalidToolCall, ToolError
from foundry.core.tools.base import Operation, ToolContext, ToolKind, ToolOutput, truncate_middle
from foundry.core.workspace import PathRejected

DEFAULT_READ_LINES = 250
MAX_SEARCH_MATCHES = 50
MAX_LIST_ENTRIES = 200

_SKIP_DIRS = frozenset({".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache"})

_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _is_reparse_point(path: Path) -> bool:
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & _REPARSE)
    except OSError:
        return True  # unreadable: treat as unsafe and skip


def _prune(dirpath: str, dirnames: list[str]) -> None:
    """Drop skipped and reparse-point directories from an os.walk descent.

    ``os.walk`` will not follow a symlink, but it does follow a Windows
    junction -- ``islink`` reports False for one. Without this, a junction
    inside the workspace lets a read-only tool return files from outside it,
    while ``Workspace.resolve`` correctly refuses the same path.
    """
    dirnames[:] = [
        d for d in dirnames
        if d not in _SKIP_DIRS and not _is_reparse_point(Path(dirpath) / d)
    ]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_str(args: dict[str, Any], key: str, default: str | None = None) -> str:
    value = args.get(key, default)
    if value is None:
        raise InvalidToolCall(f"missing required argument: {key}")
    if not isinstance(value, str):
        raise InvalidToolCall(f"{key} must be a string, got {type(value).__name__}")
    return value


def _require_int(args: dict[str, Any], key: str, default: int) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidToolCall(f"{key} must be an integer")
    return value


def _reject_unknown(args: dict[str, Any], allowed: set[str]) -> None:
    extra = set(args) - allowed
    if extra:
        raise InvalidToolCall(f"unknown argument(s): {', '.join(sorted(extra))}")


@dataclass(slots=True)
class ListFiles:
    name: str = "list_files"
    kind: ToolKind = ToolKind.READ_ONLY

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=(
                "List files under a workspace-relative directory, most recently "
                "modified first. Example: {\"path\": \"src\", \"pattern\": \"*.py\"}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory, workspace-relative. Defaults to the root."},
                    "pattern": {"type": "string", "description": "Optional glob filter, e.g. *.py"},
                    "max_entries": {"type": "integer", "description": f"Default {MAX_LIST_ENTRIES}."},
                },
                "additionalProperties": False,
            },
        )

    def validate(self, args: dict[str, Any]) -> Operation:
        _reject_unknown(args, {"path", "pattern", "max_entries"})
        path = _require_str(args, "path", ".")
        pattern = _require_str(args, "pattern", "*")
        limit = _require_int(args, "max_entries", MAX_LIST_ENTRIES)
        if limit < 1:
            raise InvalidToolCall("max_entries must be positive")
        return Operation(
            tool=self.name, kind=self.kind,
            args={"path": path, "pattern": pattern, "max_entries": limit},
            display=f"list {path} ({pattern})", target=path,
        )

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        try:
            root = ctx.workspace.resolve(op.args["path"])
        except PathRejected as exc:
            raise ToolError(str(exc)) from exc
        if not root.absolute.is_dir():
            raise ToolError(f"not a directory: {op.args['path']}")

        pattern = op.args["pattern"]
        entries: list[tuple[float, str]] = []
        for dirpath, dirnames, filenames in os.walk(root.absolute):
            _prune(dirpath, dirnames)
            for filename in filenames:
                if not fnmatch.fnmatch(filename, pattern):
                    continue
                full = Path(dirpath) / filename
                try:
                    mtime = full.stat().st_mtime
                except OSError:
                    continue
                entries.append((mtime, os.path.relpath(full, ctx.workspace.root).replace("\\", "/")))

        entries.sort(key=lambda item: item[0], reverse=True)
        limit = op.args["max_entries"]
        shown = entries[:limit]
        lines = [rel for _, rel in shown]
        if len(entries) > limit:
            lines.append(f"[{len(entries) - limit} more entries not shown; narrow the pattern]")
        body = "\n".join(lines) if lines else "(no matching files)"
        return ToolOutput(content=body, metadata={"count": len(entries)})


@dataclass(slots=True)
class SearchText:
    name: str = "search_text"
    kind: ToolKind = ToolKind.READ_ONLY

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=(
                "Search file contents with a regular expression. Returns at most "
                f"{MAX_SEARCH_MATCHES} matches with file:line. Example: "
                "{\"query\": \"def run_turn\", \"glob\": \"*.py\"}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Regular expression."},
                    "path": {"type": "string", "description": "Directory to search, workspace-relative."},
                    "glob": {"type": "string", "description": "Filename filter, e.g. *.py"},
                    "ignore_case": {"type": "boolean"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        )

    def validate(self, args: dict[str, Any]) -> Operation:
        _reject_unknown(args, {"query", "path", "glob", "ignore_case"})
        query = _require_str(args, "query")
        path = _require_str(args, "path", ".")
        glob = _require_str(args, "glob", "*")
        ignore_case = args.get("ignore_case", False)
        if not isinstance(ignore_case, bool):
            raise InvalidToolCall("ignore_case must be a boolean")
        try:
            re.compile(query)
        except re.error as exc:
            raise InvalidToolCall(f"invalid regular expression: {exc}") from exc
        return Operation(
            tool=self.name, kind=self.kind,
            args={"query": query, "path": path, "glob": glob, "ignore_case": ignore_case},
            display=f"search {query!r} in {path} ({glob})", target=path,
        )

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        try:
            root = ctx.workspace.resolve(op.args["path"])
        except PathRejected as exc:
            raise ToolError(str(exc)) from exc

        flags = re.IGNORECASE if op.args["ignore_case"] else 0
        regex = re.compile(op.args["query"], flags)
        glob = op.args["glob"]
        matches: list[str] = []
        total = 0

        for dirpath, dirnames, filenames in os.walk(root.absolute):
            _prune(dirpath, dirnames)
            for filename in filenames:
                if not fnmatch.fnmatch(filename, glob):
                    continue
                full = Path(dirpath) / filename
                try:
                    text = full.read_text(encoding="utf-8", errors="strict")
                except (UnicodeDecodeError, OSError):
                    continue  # binary or unreadable: skip quietly
                rel = os.path.relpath(full, ctx.workspace.root).replace("\\", "/")
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if regex.search(line):
                        total += 1
                        if len(matches) < MAX_SEARCH_MATCHES:
                            matches.append(f"{rel}:{lineno}: {line.strip()[:200]}")

        if not matches:
            return ToolOutput(content="(no matches)", metadata={"count": 0})
        body = "\n".join(matches)
        if total > MAX_SEARCH_MATCHES:
            body += (f"\n\n[{total} total matches, showing {MAX_SEARCH_MATCHES}. "
                     "Narrow the query or restrict path/glob.]")
        return ToolOutput(content=body, metadata={"count": total})


@dataclass(slots=True)
class ReadFile:
    name: str = "read_file"
    kind: ToolKind = ToolKind.READ_ONLY

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=(
                "Read a workspace-relative text file with line numbers. Reads a "
                f"window of {DEFAULT_READ_LINES} lines by default; pass start_line "
                "to page. You must read a file before editing it. Example: "
                "{\"path\": \"src/app.py\"}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "description": "1-based, default 1."},
                    "line_count": {"type": "integer", "description": f"Default {DEFAULT_READ_LINES}."},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def validate(self, args: dict[str, Any]) -> Operation:
        _reject_unknown(args, {"path", "start_line", "line_count"})
        path = _require_str(args, "path")
        start = _require_int(args, "start_line", 1)
        count = _require_int(args, "line_count", DEFAULT_READ_LINES)
        if start < 1:
            raise InvalidToolCall("start_line is 1-based and must be >= 1")
        if count < 1:
            raise InvalidToolCall("line_count must be positive")
        return Operation(
            tool=self.name, kind=self.kind,
            args={"path": path, "start_line": start, "line_count": count},
            display=f"read {path}", target=path,
        )

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        try:
            resolved = ctx.workspace.resolve(op.args["path"])
        except PathRejected as exc:
            raise ToolError(str(exc)) from exc
        if not resolved.absolute.is_file():
            raise ToolError(f"file not found: {op.args['path']}")

        raw = resolved.absolute.read_bytes()
        if b"\x00" in raw[:8000]:
            raise ToolError(f"{resolved.relative} appears to be binary; cannot read as text")
        text = raw.decode("utf-8", errors="replace")

        # Record the digest even on a partial read: the patch tool re-checks the
        # anchor anyway, and refusing to edit a file the model paged through
        # would be worse than useless.
        ctx.read_tracker.record(resolved.relative, hashlib.sha256(raw).hexdigest())

        lines = text.splitlines()
        start = op.args["start_line"]
        count = op.args["line_count"]
        window = lines[start - 1:start - 1 + count]
        if not window and lines:
            raise ToolError(
                f"start_line {start} is past the end of {resolved.relative} ({len(lines)} lines)"
            )

        numbered = "\n".join(f"{start + i:>6}\t{line}" for i, line in enumerate(window))
        header = f"{resolved.relative} ({len(lines)} lines total)"
        shown_end = start - 1 + len(window)
        footer = ""
        if shown_end < len(lines):
            footer = f"\n\n[showing lines {start}-{shown_end}; {len(lines) - shown_end} more below]"
        body, truncated = truncate_middle(f"{header}\n{numbered}{footer}", ctx.max_output_bytes)
        return ToolOutput(content=body, truncated=truncated,
                          metadata={"lines": len(lines), "path": resolved.relative})


@dataclass(slots=True)
class ReadArtifact:
    name: str = "read_artifact"
    kind: ToolKind = ToolKind.READ_ONLY

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=(
                "Read the full output of an earlier tool call that was too large "
                "to inline. Use the artifact_id reported with the truncated "
                "output. Example: {\"artifact_id\": \"a1b2...\", \"offset\": 0}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "offset": {"type": "integer", "description": "Character offset, default 0."},
                },
                "required": ["artifact_id"],
                "additionalProperties": False,
            },
        )

    def validate(self, args: dict[str, Any]) -> Operation:
        _reject_unknown(args, {"artifact_id", "offset"})
        artifact_id = _require_str(args, "artifact_id")
        offset = _require_int(args, "offset", 0)
        if offset < 0:
            raise InvalidToolCall("offset must be >= 0")
        # The id is an opaque token, never a path: reject anything path-shaped
        # so this cannot become a second unrestricted file reader.
        if not re.fullmatch(r"[0-9a-f]{64}", artifact_id):
            raise InvalidToolCall("artifact_id must be a 64-character hex token")
        return Operation(
            tool=self.name, kind=self.kind,
            args={"artifact_id": artifact_id, "offset": offset},
            display=f"read artifact {artifact_id[:12]}", target=artifact_id,
        )

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        try:
            text = ctx.artifacts.read_text(op.args["artifact_id"], offset=op.args["offset"],
                                           limit=ctx.max_output_bytes)
        except KeyError as exc:
            raise ToolError(
                f"unknown artifact id {op.args['artifact_id'][:12]}; artifacts are only "
                "readable within the session that produced them"
            ) from exc
        body, truncated = truncate_middle(text, ctx.max_output_bytes)
        return ToolOutput(content=body, truncated=truncated)
