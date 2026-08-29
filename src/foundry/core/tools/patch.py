"""apply_patch: an envelope of file operations with anchored text hunks.

Format choice (D-010): the corporate gateway serves several model families, and
Codex's V4A grammar is trained into GPT models specifically -- Claude-family
models produce malformed V4A. Anchored search/replace is what Claude Code,
OpenHands, and aider each converged on independently, and it carries no line
numbers, which models get wrong after any earlier edit.

Application semantics (per-file atomic): every hunk of every file is located
before anything is written. A file with a failing hunk is left untouched and
reported; files whose hunks all located are written atomically. The error names
the file and quotes the nearest real lines, and tells the model to resend the
whole failing file -- not just the failed hunk, which would silently drop the
rest of that file's edits.

    *** Begin Patch
    *** Update File: src/app.py
    <<<<<<< SEARCH
    def run():
        return 1
    =======
    def run():
        return 2
    >>>>>>> REPLACE
    *** Add File: src/new.py
    +print("hello")
    *** Delete File: src/old.py
    *** End Patch
"""

from __future__ import annotations

import difflib
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from foundry.core.conversation import ToolSchema
from foundry.core.errors import InvalidToolCall, StaleFileError, ToolError
from foundry.core.tools.base import Operation, ToolContext, ToolKind, ToolOutput
from foundry.core.workspace import PathRejected

BEGIN = "*** Begin Patch"
END = "*** End Patch"
UPDATE = "*** Update File: "
ADD = "*** Add File: "
DELETE = "*** Delete File: "
MOVE = "*** Move to: "
SEARCH_OPEN = "<<<<<<< SEARCH"
DIVIDER = "======="
REPLACE_CLOSE = ">>>>>>> REPLACE"

MAX_EDIT_FAILURES_PER_FILE = 3


class PatchParseError(InvalidToolCall):
    """The patch envelope itself is malformed."""


@dataclass(frozen=True, slots=True)
class Hunk:
    search: str
    replace: str


@dataclass(frozen=True, slots=True)
class FileOp:
    action: Literal["update", "add", "delete"]
    path: str
    hunks: tuple[Hunk, ...] = ()
    content: str = ""
    move_to: str = ""


def parse_patch(text: str) -> tuple[FileOp, ...]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or lines[0].strip() != BEGIN:
        raise PatchParseError(f"patch must start with {BEGIN!r}")
    if lines[-1].strip() != END:
        raise PatchParseError(f"patch must end with {END!r}")

    ops: list[FileOp] = []
    i = 1
    last = len(lines) - 1

    while i < last:
        line = lines[i]
        if line.startswith(UPDATE):
            path = line[len(UPDATE):].strip()
            i += 1
            move_to = ""
            if i < last and lines[i].startswith(MOVE):
                move_to = lines[i][len(MOVE):].strip()
                i += 1
            hunks: list[Hunk] = []
            while i < last and lines[i].strip() == SEARCH_OPEN:
                i += 1
                search: list[str] = []
                while i < last and lines[i].strip() != DIVIDER:
                    search.append(lines[i])
                    i += 1
                if i >= last:
                    raise PatchParseError(f"hunk for {path} is missing {DIVIDER!r}")
                i += 1
                replace: list[str] = []
                while i < last and lines[i].strip() != REPLACE_CLOSE:
                    replace.append(lines[i])
                    i += 1
                if i >= last:
                    raise PatchParseError(f"hunk for {path} is missing {REPLACE_CLOSE!r}")
                i += 1
                hunks.append(Hunk("\n".join(search), "\n".join(replace)))
            if not hunks:
                raise PatchParseError(f"update of {path} has no {SEARCH_OPEN} hunk")
            ops.append(FileOp("update", path, tuple(hunks), move_to=move_to))

        elif line.startswith(ADD):
            path = line[len(ADD):].strip()
            i += 1
            body: list[str] = []
            while i < last and not lines[i].startswith("*** "):
                if not lines[i].startswith("+"):
                    raise PatchParseError(
                        f"line in 'Add File: {path}' must start with '+': {lines[i]!r}"
                    )
                body.append(lines[i][1:])
                i += 1
            ops.append(FileOp("add", path, content="\n".join(body)))

        elif line.startswith(DELETE):
            ops.append(FileOp("delete", line[len(DELETE):].strip()))
            i += 1

        elif not line.strip():
            i += 1

        else:
            raise PatchParseError(f"unexpected line in patch: {line!r}")

    if not ops:
        raise PatchParseError("patch contains no file operations")
    return tuple(ops)


# --- locating a hunk ------------------------------------------------------


def _normalize_ws(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.split("\n"))


def _strip_indent(text: str) -> str:
    return "\n".join(line.strip() for line in text.split("\n"))


@dataclass(frozen=True, slots=True)
class Located:
    start: int
    end: int
    rung: str


def locate(haystack: str, needle: str) -> Located | list[str]:
    """Find a unique occurrence, loosening only in ways that cannot mislocate.

    Returns the span, or a list of near-miss lines to quote back. The >80%
    similarity rung aider offers is deliberately not implemented: a silently
    misplaced edit is worse than a failed one, and this tool tells the model
    exactly what to fix.
    """
    if not needle:
        return ["(empty search block)"]

    count = haystack.count(needle)
    if count == 1:
        start = haystack.index(needle)
        return Located(start, start + len(needle), "exact")
    if count > 1:
        return [f"__AMBIGUOUS__{count}"]

    # Rung 2: ignore trailing whitespace differences.
    norm_hay, norm_needle = _normalize_ws(haystack), _normalize_ws(needle)
    if norm_hay.count(norm_needle) == 1:
        span = _map_span(haystack, norm_hay, norm_needle)
        if span:
            return Located(span[0], span[1], "trailing-whitespace")

    # Rung 3: ignore leading indentation differences.
    flat_hay, flat_needle = _strip_indent(haystack), _strip_indent(needle)
    if flat_needle and flat_hay.count(flat_needle) == 1:
        span = _map_span(haystack, flat_hay, flat_needle)
        if span:
            return Located(span[0], span[1], "indentation")

    return _near_misses(haystack, needle)


def _map_span(original: str, transformed: str, needle: str) -> tuple[int, int] | None:
    """Map a match in a line-wise transformed copy back to original offsets."""
    line_index = transformed[:transformed.index(needle)].count("\n")
    span_lines = needle.count("\n") + 1
    original_lines = original.split("\n")
    if line_index + span_lines > len(original_lines):
        return None
    start = sum(len(l) + 1 for l in original_lines[:line_index])
    end = start + sum(len(l) + 1 for l in original_lines[line_index:line_index + span_lines]) - 1
    return start, end


def _near_misses(haystack: str, needle: str) -> list[str]:
    first = needle.split("\n", 1)[0].strip()
    if not first:
        return []
    lines = haystack.split("\n")
    scored = sorted(
        ((difflib.SequenceMatcher(None, first, line.strip()).ratio(), i, line) for i, line in enumerate(lines, 1)),
        key=lambda item: item[0], reverse=True,
    )
    return [f"{i}: {line}" for ratio, i, line in scored[:3] if ratio > 0.5]


# --- file writing ---------------------------------------------------------


def _detect_line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _write_atomic(path: Path, text: str, encoding: str, line_ending: str,
                  had_bom: bool) -> None:
    if line_ending == "\r\n":
        text = text.replace("\n", "\r\n")
    data = text.encode(encoding)
    if had_bom and not data.startswith(b"\xef\xbb\xbf"):
        data = b"\xef\xbb\xbf" + data
    tmp = path.with_name(path.name + ".foundry-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


@dataclass(slots=True)
class _Planned:
    op: FileOp
    path: Path
    relative: str
    new_text: str
    line_ending: str
    had_bom: bool
    rungs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ApplyPatch:
    name: str = "apply_patch"
    kind: ToolKind = ToolKind.MUTATOR
    failures: dict[str, int] = field(default_factory=dict)

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=(
                "Apply an anchored patch to workspace files. The patch is one "
                "string. SEARCH text must match the file exactly and appear "
                "exactly once; read the file first. A file whose hunks do not "
                "all apply is left untouched -- resend that whole file's hunks.\n"
                f"{BEGIN}\n{UPDATE}src/app.py\n{SEARCH_OPEN}\nold line\n{DIVIDER}\n"
                f"new line\n{REPLACE_CLOSE}\n{END}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "patch": {"type": "string", "description": "The complete patch envelope."},
                },
                "required": ["patch"],
                "additionalProperties": False,
            },
        )

    def validate(self, args: dict[str, Any]) -> Operation:
        extra = set(args) - {"patch"}
        if extra:
            raise InvalidToolCall(f"unknown argument(s): {', '.join(sorted(extra))}")
        patch = args.get("patch")
        if not isinstance(patch, str) or not patch.strip():
            raise InvalidToolCall("patch must be a non-empty string")

        ops = parse_patch(patch)
        paths = [op.path for op in ops]
        summary = ", ".join(f"{op.action} {op.path}" for op in ops)
        return Operation(
            tool=self.name, kind=self.kind,
            args={"patch": patch, "paths": paths},
            display=f"apply_patch: {summary}",
            target=paths[0] if len(paths) == 1 else " ".join(paths),
            digest=hashlib.sha256(patch.encode("utf-8")).hexdigest()[:16],
        )

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        ops = parse_patch(op.args["patch"])
        planned: list[_Planned] = []
        rejected: list[str] = []

        for file_op in ops:
            try:
                planned.append(self._plan(file_op, ctx))
            except (ToolError, PathRejected) as exc:
                self.failures[file_op.path] = self.failures.get(file_op.path, 0) + 1
                note = ""
                if self.failures[file_op.path] >= MAX_EDIT_FAILURES_PER_FILE:
                    note = (f" (this file has failed {self.failures[file_op.path]} times; "
                            "read it again before retrying)")
                rejected.append(f"{file_op.path}: {exc}{note}")

        applied: list[str] = []
        for item in planned:
            if item.op.action == "delete":
                item.path.unlink()
                ctx.read_tracker.forget(item.relative)
                applied.append(f"deleted {item.relative}")
                continue

            item.path.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(item.path, item.new_text, "utf-8", item.line_ending, item.had_bom)
            ctx.read_tracker.record(item.relative,
                                    hashlib.sha256(item.path.read_bytes()).hexdigest())
            self.failures.pop(item.op.path, None)

            if item.op.move_to:
                target = ctx.workspace.resolve(item.op.move_to, for_write=True)
                target.absolute.parent.mkdir(parents=True, exist_ok=True)
                os.replace(item.path, target.absolute)
                applied.append(f"updated and moved {item.relative} -> {target.relative}")
            else:
                verb = "created" if item.op.action == "add" else "updated"
                detail = f" [{', '.join(sorted(set(item.rungs)))}]" if item.rungs and set(item.rungs) != {"exact"} else ""
                applied.append(f"{verb} {item.relative}{detail}")

        lines = []
        if applied:
            lines.append("Applied:\n" + "\n".join(f"  {a}" for a in applied))
        if rejected:
            lines.append("Rejected (left untouched):\n" + "\n".join(f"  {r}" for r in rejected))
        content = "\n\n".join(lines) if lines else "(no operations)"
        return ToolOutput(content=content, is_error=bool(rejected),
                          metadata={"applied": len(applied), "rejected": len(rejected)})

    def _plan(self, file_op: FileOp, ctx: ToolContext) -> _Planned:
        resolved = ctx.workspace.resolve(file_op.path, for_write=True)
        path = resolved.absolute

        if file_op.action == "delete":
            if not path.is_file():
                raise ToolError("file does not exist")
            return _Planned(file_op, path, resolved.relative, "", "\n", False)

        if file_op.action == "add":
            if path.exists():
                raise ToolError("file already exists; use Update File instead")
            return _Planned(file_op, path, resolved.relative, file_op.content, "\n", False)

        if not path.is_file():
            raise ToolError("file does not exist")
        if not ctx.read_tracker.has_read(resolved.relative):
            raise StaleFileError("you must read this file before editing it")

        raw = path.read_bytes()
        had_bom = raw.startswith(b"\xef\xbb\xbf")
        body = raw[3:] if had_bom else raw
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"file is not valid UTF-8 ({exc.reason}); cannot patch") from exc

        line_ending = _detect_line_ending(text)
        working = text.replace("\r\n", "\n")
        rungs: list[str] = []

        for index, hunk in enumerate(file_op.hunks, start=1):
            search = hunk.search.replace("\r\n", "\n")
            result = locate(working, search)
            if isinstance(result, list):
                raise ToolError(self._explain(index, result, resolved.relative))
            working = working[:result.start] + hunk.replace.replace("\r\n", "\n") + working[result.end:]
            rungs.append(result.rung)

        return _Planned(file_op, path, resolved.relative, working, line_ending, had_bom, rungs)

    @staticmethod
    def _explain(index: int, near: list[str], relative: str) -> str:
        if near and near[0].startswith("__AMBIGUOUS__"):
            count = near[0].removeprefix("__AMBIGUOUS__")
            return (f"hunk {index}: SEARCH text appears {count} times in {relative}; "
                    "include more surrounding lines to make it unique")
        if not near:
            return f"hunk {index}: SEARCH text not found in {relative}"
        quoted = "\n".join(f"    {line}" for line in near)
        return (f"hunk {index}: SEARCH text not found in {relative}. "
                f"Nearest lines in the file:\n{quoted}")
