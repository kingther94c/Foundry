"""repo_map: an outline of Python code from the standard library's ast (D-046).

Aider's finding is that a model finds its way around a repository faster from
a map of its definitions than from listing and reading files one by one. Aider
builds the map with tree-sitter for every language and ranks it with PageRank;
this is the stdlib-only cut: Python files, parsed with ``ast`` (which reads the
source and never runs it), ranked by how many other files import each one.

It is a tool the model calls, not a block in every request: a map that is not
needed costs nothing, and the system prompt stays the same bytes across a
resume.
"""

from __future__ import annotations

import ast
import os
import re
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from foundry.core.conversation import ToolSchema
from foundry.core.errors import InvalidToolCall, ToolError
from foundry.core.tools.base import Operation, ToolContext, ToolKind, ToolOutput, truncate_middle
from foundry.core.tools.files import _prune
from foundry.core.workspace import PathRejected

# What a directory's map may take: about 3,000 tokens. A narrower path gets
# the same budget for fewer files, so it is the way to more detail.
MAP_BYTES = 12_000
MAX_MAP_FILES = 2_000
# A generated module or a vendored blob tells the model nothing an outline can.
MAX_MAP_FILE_BYTES = 1_000_000
MAP_TIME_BUDGET_S = 10.0
LINE_WIDTH = 160
SUMMARY_WIDTH = 100
UNLISTED_NAMES = 30
CACHE_LIMIT = 5_000

# Shown at module level: constants and type aliases, which start upper-case.
_SHOWN_NAME = re.compile(r"_*[A-Z]")
_TEST_DIRS = frozenset({"tests", "test", "testing"})
_SKIP_SUFFIXES = (".egg-info",)
_SKIP_NAMES = frozenset({"site-packages"})


@dataclass(frozen=True, slots=True)
class Entry:
    """One definition: its line, nesting, and how it reads in full and in brief."""

    lineno: int
    depth: int
    full: str
    brief: str
    public: bool
    name: str = ""          # a top-level definition's name, for the one-line form


@dataclass(frozen=True, slots=True)
class FileOutline:
    rel: str
    entries: tuple[Entry, ...]
    summary: str
    imports: tuple[tuple[str, ...], ...]   # per import, its candidates, most specific first
    error: str = ""

    def rows(self, brief: bool) -> list[str]:
        """Each definition as a numbered line. In brief -- a directory's map --
        what a caller outside the module would not use is left out: names with
        a leading underscore (``__init__`` stays), and a decorator's arguments."""
        if not brief:
            return [_row(e.lineno, e.depth, e.full) for e in self.entries]
        return [_row(e.lineno, e.depth, e.brief) for e in self.entries if e.public]

    def names(self) -> str:
        """The top-level public definitions on one line."""
        return ", ".join(e.name for e in self.entries if e.depth == 0 and e.public and e.name)


def _clip(text: str, width: int = LINE_WIDTH) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[:width - 3] + "..."


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except (ValueError, RecursionError):  # a default nested past the C stack
        return "..."


def _decorators(node: ast.AST, brief: bool) -> str:
    shown = []
    for d in getattr(node, "decorator_list", ()):
        if brief and isinstance(d, ast.Call):
            d = d.func
        shown.append(f"@{_unparse(d)} ")
    return "".join(shown)


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef, brief: bool) -> str:
    keyword = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {_unparse(node.returns)}" if node.returns is not None else ""
    return f"{_decorators(node, brief)}{keyword} {node.name}({_unparse(node.args)}){returns}"


def _class_line(node: ast.ClassDef, brief: bool) -> str:
    bases = [_unparse(b) for b in node.bases] + [_unparse(k) for k in node.keywords]
    return (f"{_decorators(node, brief)}class {node.name}"
            + (f"({', '.join(bases)})" if bases else ""))


def _blocks(body: list[ast.stmt]):
    """The statements of a body, with those under an if, try or with taken as
    the body's own -- ``if TYPE_CHECKING:`` and ``try: import`` are where
    definitions hide."""
    for stmt in body:
        if isinstance(stmt, ast.If):
            yield from _blocks(stmt.body)
            yield from _blocks(stmt.orelse)
        elif isinstance(stmt, (ast.Try, ast.TryStar)):
            yield from _blocks(stmt.body)
            for handler in stmt.handlers:
                yield from _blocks(handler.body)
            yield from _blocks(stmt.orelse)
            yield from _blocks(stmt.finalbody)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            yield from _blocks(stmt.body)
        else:
            yield stmt


def _assigned_names(stmt: ast.stmt) -> list[str]:
    if isinstance(stmt, ast.Assign):
        return [t.id for t in stmt.targets if isinstance(t, ast.Name)]
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        return [stmt.target.id]
    if isinstance(stmt, ast.TypeAlias) and isinstance(stmt.name, ast.Name):
        return [stmt.name.id]
    return []


def _public(name: str) -> bool:
    return not name.startswith("_") or name == "__init__"


def _row(lineno: int, depth: int, text: str) -> str:
    indent = "  " * depth
    return f"{lineno:>5} {indent}{_clip(text, LINE_WIDTH - 6 - len(indent))}"


_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _first_line(stmt: ast.stmt) -> int:
    """Where a definition starts: its first decorator, which is where the line
    shows it starting and where read_file should begin."""
    return min([stmt.lineno, *(d.lineno for d in getattr(stmt, "decorator_list", ()))])


def _definition(stmt: ast.stmt, depth: int, public: bool, out: list[Entry]) -> None:
    """Record a def or a class, and a class's members."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        out.append(Entry(_first_line(stmt), depth, _signature(stmt, False),
                         _signature(stmt, True), public and _public(stmt.name),
                         f"{stmt.name}()"))
    elif isinstance(stmt, ast.ClassDef):
        public = public and _public(stmt.name)
        out.append(Entry(_first_line(stmt), depth, _class_line(stmt, False),
                         _class_line(stmt, True), public, stmt.name))
        _class_body(stmt, depth + 1, public, out)


def _outline(body: list[ast.stmt], out: list[Entry]) -> None:
    """A module's definitions, with its constants and type aliases (names that
    start upper-case) gathered a run at a time onto one line."""
    names: list[str] = []
    first = 0

    def flush() -> None:
        if names:
            text = ", ".join(names)
            public = ", ".join(n for n in names if _public(n))
            out.append(Entry(first, 0, text, public, bool(public), public))
            names.clear()

    for stmt in _blocks(body):
        if isinstance(stmt, _DEFINITIONS):
            flush()
            _definition(stmt, 0, True, out)
            continue
        shown = [n for n in _assigned_names(stmt) if _SHOWN_NAME.match(n)]
        if shown:
            if not names:
                first = stmt.lineno
            names.extend(shown)
        else:
            flush()
    flush()


def _class_body(node: ast.ClassDef, depth: int, public: bool, out: list[Entry]) -> None:
    """A class's annotated fields on one line (a dataclass's are its
    interface), then its methods and nested classes."""
    fields = [stmt for stmt in node.body
              if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)]
    if fields:
        full = ", ".join(f"{f.target.id}: {_unparse(f.annotation)}" for f in fields)
        brief = ", ".join(f"{f.target.id}: {_unparse(f.annotation)}" for f in fields
                          if _public(f.target.id))
        out.append(Entry(fields[0].lineno, depth, full, brief, public and bool(brief)))
    for stmt in _blocks(node.body):
        if isinstance(stmt, _DEFINITIONS):
            _definition(stmt, depth, public, out)


def _package(rel: str) -> list[str]:
    return rel.split("/")[:-1]


def _imports(tree: ast.Module, rel: str) -> tuple[tuple[str, ...], ...]:
    """Each import as dotted names to look up, the most specific first.

    A relative import is resolved here, against the file's own directory, to
    the full dotted path from the workspace root; an absolute one is matched
    later against every suffix of every file's path.
    """
    found: list[tuple[str, ...]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                found.append(tuple(".".join(parts[:n]) for n in range(len(parts), 0, -1)))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = _package(rel)
                up = node.level - 1
                if up > len(package):
                    continue
                base = package[:len(package) - up]
                module = ".".join(base + (node.module.split(".") if node.module else []))
            else:
                module = node.module or ""
            if not module:
                continue
            for alias in node.names:
                found.append((f"{module}.{alias.name}", module))
    return tuple(found)


def _module_names(rel: str) -> list[str]:
    """Every name the file could be imported by: each suffix of its dotted
    path, so ``src/pkg/mod.py`` answers to ``pkg.mod`` and to ``mod``."""
    parts = rel[:-len(Path(rel).suffix)].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return [".".join(parts[i:]) for i in range(len(parts))]


def _is_test(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return (any(p in _TEST_DIRS for p in parts[:-1]) or name.startswith("test_")
            or name.endswith("_test.py") or name == "conftest.py")


def parse(raw: bytes, rel: str) -> FileOutline:
    """One file's outline. ``ast.parse`` reads the source; nothing in it runs.

    The bytes go in as bytes, so a BOM or a coding declaration is honoured.
    A file that will not parse gets an error and no rows: it is named, never
    silently left out.
    """
    try:
        with warnings.catch_warnings():
            # An invalid escape in a string is a SyntaxWarning on stderr, which
            # would land in the middle of the terminal's own output.
            warnings.simplefilter("ignore")
            tree = ast.parse(raw, filename=rel)
    except SyntaxError as exc:
        where = f"line {exc.lineno}: " if exc.lineno else ""
        return FileOutline(rel, (), "", (), error=f"{where}{exc.msg}")
    except (ValueError, RecursionError, MemoryError) as exc:
        return FileOutline(rel, (), "", (), error=type(exc).__name__)
    entries: list[Entry] = []
    try:
        _outline(tree.body, entries)
        imports = _imports(tree, rel)
    except RecursionError:
        return FileOutline(rel, (), "", (), error="nested too deeply to outline")
    doc = (ast.get_docstring(tree, clean=True) or "").strip()
    # The first paragraph, not the first line: a summary wrapped at 79
    # columns would otherwise stop mid-sentence.
    summary = _clip(doc.split("\n\n")[0], SUMMARY_WIDTH) if doc else ""
    return FileOutline(rel, tuple(entries), summary, imports)


def _path(outline: FileOutline, importers: int | None) -> str:
    return outline.rel + (f" (imported by {importers})" if importers else "")


def _block(outline: FileOutline, importers: int | None, brief: bool) -> str:
    head = _path(outline, importers)
    if outline.summary:
        head += f" -- {outline.summary}"
    rows = outline.rows(brief) or ["      (no definitions)"]
    return "\n".join((head, *rows))


def _one_line(outline: FileOutline, importers: int | None) -> str:
    """A file in one line: its public top-level names, then its summary."""
    line = _path(outline, importers)
    names = outline.names()
    if names:
        line += f": {names}"
    if outline.summary:
        line += f" -- {outline.summary}"
    return _clip(line)


def _importers(outlines: list[FileOutline]) -> dict[str, int]:
    """How many other files import each file, by its path."""
    index: dict[str, set[str]] = {}
    for outline in outlines:
        for name in _module_names(outline.rel):
            if name:
                index.setdefault(name, set()).add(outline.rel)
    importers: dict[str, set[str]] = {}
    for outline in outlines:
        for candidates in outline.imports:
            for name in candidates:
                targets = index.get(name)
                if targets is None:
                    continue
                # A name two files answer to -- `utils` in two packages -- says
                # nothing about which is meant; neither is credited.
                if len(targets) == 1:
                    target = next(iter(targets))
                    if target != outline.rel:
                        importers.setdefault(target, set()).add(outline.rel)
                break
    return {rel: len(files) for rel, files in importers.items()}


@dataclass(slots=True)
class RepoMap:
    name: str = "repo_map"
    kind: ToolKind = ToolKind.READ_ONLY
    # Outlines by (path, mtime, size): a map asked for again after an edit
    # parses only the files that changed.
    _cache: dict[tuple[str, int, int], FileOutline] = field(default_factory=dict)

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=(
                "Outline Python code: the classes, functions with their signatures, "
                "methods and constants of each .py file, with line numbers for "
                "read_file. For a directory, the files most imported by the others "
                f"come first, within about {MAP_BYTES // 4} tokens; narrow the path "
                "for more detail. For one file, its whole outline. Use it to find "
                "your way around before reading. Example: {\"path\": \"src\"}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "A directory or a .py file, workspace-relative. "
                                            "Defaults to the root."},
                },
                "additionalProperties": False,
            },
        )

    def validate(self, args: dict[str, Any]) -> Operation:
        unknown = set(args) - {"path"}
        if unknown:
            raise InvalidToolCall(f"unknown argument(s): {', '.join(sorted(unknown))}")
        path = args.get("path", ".")
        if not isinstance(path, str) or not path.strip():
            raise InvalidToolCall("path must be a non-empty string")
        return Operation(tool=self.name, kind=self.kind, args={"path": path},
                         display=f"map {path}", target=path)

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        try:
            root = ctx.workspace.resolve(op.args["path"])
        except PathRejected as exc:
            raise ToolError(str(exc)) from exc
        if root.absolute.is_file():
            return self._one(root, ctx)
        if not root.absolute.is_dir():
            raise ToolError(f"no such path: {op.args['path']}")
        return self._tree(root, op.args["path"], ctx)

    def _read(self, full: Path, rel: str) -> FileOutline | str:
        """The file's outline, or why it has none."""
        try:
            info = full.stat()
        except OSError as exc:
            return f"unreadable ({exc.strerror or type(exc).__name__})"
        if info.st_size > MAX_MAP_FILE_BYTES:
            return f"over {MAX_MAP_FILE_BYTES // 1_000_000} MB"
        key = (rel, info.st_mtime_ns, info.st_size)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            raw = full.read_bytes()
        except OSError as exc:
            return f"unreadable ({exc.strerror or type(exc).__name__})"
        outline = parse(raw, rel)
        if len(self._cache) >= CACHE_LIMIT:
            self._cache.clear()
        self._cache[key] = outline
        return outline

    def _one(self, root, ctx: ToolContext) -> ToolOutput:
        if Path(root.relative).suffix not in (".py", ".pyi"):
            raise ToolError(f"repo_map outlines Python files (.py, .pyi); {root.relative} "
                            "is not one. Use read_file to read it.")
        outline = self._read(root.absolute, root.relative)
        if isinstance(outline, str):
            raise ToolError(f"{root.relative} cannot be outlined: {outline}")
        if outline.error:
            raise ToolError(f"{root.relative} does not parse ({outline.error}); "
                            "use read_file to read it.")
        body, truncated = truncate_middle(_block(outline, None, False), ctx.max_output_bytes)
        return ToolOutput(content=body, truncated=truncated,
                          metadata={"files": 1, "shown": 1, "unparsed": 0, "incomplete": False})

    def _walk(self, root, ctx: ToolContext):
        """The Python files under ``root``, workspace-relative, sorted.

        Pruned like list_files, and of hidden directories too: a `.claude` or
        `.tox` holding another copy of the tree would double the map.
        """
        found: list[tuple[str, Path]] = []
        for dirpath, dirnames, filenames in os.walk(root.absolute):
            _prune(dirpath, dirnames)
            dirnames[:] = [d for d in dirnames if not d.startswith(".")
                           and d not in _SKIP_NAMES and not d.endswith(_SKIP_SUFFIXES)]
            for filename in filenames:
                if filename.endswith(".py"):
                    full = Path(dirpath) / filename
                    found.append((os.path.relpath(full, ctx.workspace.root).replace("\\", "/"), full))
        found.sort()
        return found

    def _tree(self, root, shown_path: str, ctx: ToolContext) -> ToolOutput:
        files = self._walk(root, ctx)
        if not files:
            return ToolOutput(content=f"(no Python files under {shown_path}; repo_map "
                                      "outlines Python only -- use list_files)",
                              metadata={"files": 0, "shown": 0, "unparsed": 0,
                                        "incomplete": False})
        deadline = time.monotonic() + MAP_TIME_BUDGET_S
        outlines: list[FileOutline] = []
        failed: list[str] = []
        stopped = 0
        for index, (rel, full) in enumerate(files):
            if (index >= MAX_MAP_FILES or time.monotonic() > deadline
                    or (ctx.cancelled and ctx.cancelled())):
                stopped = len(files) - index
                break
            outline = self._read(full, rel)
            if isinstance(outline, str):
                failed.append(f"{rel} ({outline})")
            elif outline.error:
                failed.append(f"{rel} ({outline.error})")
            else:
                outlines.append(outline)

        importers = _importers(outlines)
        ranked = sorted(outlines, key=lambda o: (
            _is_test(o.rel), -importers.get(o.rel, 0), o.rel.count("/"), o.rel))
        # Three tiers, in rank order: a file's outline while it fits; then a
        # line of its public names; then its path in the closing note. A third
        # of the budget is kept back for the lines, so the map stays broad --
        # a few large modules must not leave the rest of the tree unnamed.
        # Greedy, so a small file further down still takes room a large one
        # could not use.
        budget = min(MAP_BYTES, ctx.max_output_bytes)
        lines = {o.rel: _one_line(o, importers.get(o.rel)) for o in ranked}
        reserve = min(sum(len(line) + 1 for line in lines.values()), budget // 3)
        chosen: list[tuple[str, str]] = []
        brief: list[tuple[str, str]] = []
        unlisted: list[str] = []
        used = 0
        rest: list[FileOutline] = []
        for outline in ranked:
            block = _block(outline, importers.get(outline.rel), True)
            if outline.rows(True) and used + len(block) + 2 <= budget - reserve:
                chosen.append((outline.rel, block))
                used += len(block) + 2
            else:
                rest.append(outline)
        for outline in rest:
            line = lines[outline.rel]
            if used + len(line) + 1 <= budget:
                brief.append((outline.rel, line))
                used += len(line) + 1
            else:
                unlisted.append(outline.rel)

        head = (f"Python outline of {shown_path}: {len(files)} file(s), the most imported "
                f"first; {len(chosen)} outlined"
                + (f", {len(brief)} by name only" if brief else "")
                + ". The numbers are lines, for read_file's start_line.")
        parts = [head] + [block for _, block in sorted(chosen)]
        if brief:
            parts.append("\n".join(line for _, line in sorted(brief)))
        notes: list[str] = []
        if unlisted:
            names = ", ".join(unlisted[:UNLISTED_NAMES]) + (" ..." if len(unlisted) > UNLISTED_NAMES else "")
            notes.append(f"[{len(unlisted)} more file(s) not shown, to stay within the "
                         f"budget: {names}. Map a narrower path to see them.]")
        if failed:
            names = ", ".join(failed[:10]) + (" ..." if len(failed) > 10 else "")
            notes.append(f"[{len(failed)} file(s) could not be outlined: {names}.]")
        if stopped:
            notes.append(f"[stopped before {stopped} more file(s): the map reads at most "
                         f"{MAX_MAP_FILES} files in {MAP_TIME_BUDGET_S:.0f}s. Map a narrower "
                         "path; this one is incomplete.]")
        body = "\n\n".join(parts + notes)
        body, truncated = truncate_middle(body, ctx.max_output_bytes)
        return ToolOutput(content=body, truncated=truncated, metadata={
            "files": len(files), "shown": len(chosen), "named": len(brief),
            "unparsed": len(failed),
            "incomplete": bool(stopped)})
