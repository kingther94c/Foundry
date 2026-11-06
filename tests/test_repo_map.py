"""repo_map (D-046): an outline of Python code from the standard library's ast.

The model asks for it; nothing in a file runs. A directory's map ranks files
by how many others import them and fills a budget in three tiers -- outlines,
then one line of names, then paths -- so a large tree stays readable and a
file that was left out is always named.
"""

from __future__ import annotations

import os
import warnings

import pytest

from foundry.core.errors import InvalidToolCall, ToolError
from foundry.core.policy.engine import Mode, PolicyEngine, Verdict
from foundry.core.redaction import Redactor
from foundry.core.tools import files as files_module
from foundry.core.tools import repomap
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.registry import default_registry
from foundry.core.tools.repomap import RepoMap, _importers, parse
from foundry.core.workspace import Workspace
from test_resume import _core, call, turn


def _ctx(root, **kw) -> ToolContext:
    return ToolContext(Workspace(root), None, ReadTracker(), **kw)


def _write(root, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _map(root, path=".", **kw):
    tool = RepoMap()
    return tool.execute(tool.validate({"path": path}), _ctx(root, **kw))


def _rows(source: str, brief: bool = False) -> list[str]:
    outline = parse(source.encode("utf-8"), "m.py")
    assert not outline.error, outline.error
    return [" ".join(row.split()) for row in outline.rows(brief)]


# --- one file's outline ----------------------------------------------------------------


SAMPLE = '''"""A sample module.

Its second paragraph is not part of the summary.
"""
import os

LIMIT = 3
Pair = tuple[int, int]
counter = 0
TIMEOUT = 1.5


@dataclass(frozen=True)
class Point(Base, metaclass=Meta):
    x: int
    _hidden: str = ""

    def __init__(self, x: int = 0) -> None:
        def inner():
            pass

    @property
    def norm(self) -> float:
        return 0.0

    def _private(self):
        pass

    def __repr__(self):
        return ""

    class Inner:
        def deep(self, *args, **kw):
            pass


async def fetch(url: str, *, retries: int = 2) -> bytes:
    pass


def _helper(a, b):
    pass


class _Internal:
    def visible_only_in_full(self):
        pass


type Alias = list[int]
_PRIVATE_LIMIT = 9
'''


def test_an_outline_lists_definitions_with_signatures_and_line_numbers():
    rows = _rows(SAMPLE)
    assert rows == [
        "7 LIMIT, Pair",
        "10 TIMEOUT",
        "13 @dataclass(frozen=True) class Point(Base, metaclass=Meta)",
        "15 x: int, _hidden: str",
        "18 def __init__(self, x: int=0) -> None",
        "22 @property def norm(self) -> float",
        "26 def _private(self)",
        "29 def __repr__(self)",
        "32 class Inner",
        "33 def deep(self, *args, **kw)",
        "37 async def fetch(url: str, *, retries: int=2) -> bytes",
        "41 def _helper(a, b)",
        "45 class _Internal",
        "46 def visible_only_in_full(self)",
        "50 Alias, _PRIVATE_LIMIT",
    ]
    outline = parse(SAMPLE.encode(), "m.py")
    assert outline.summary == "A sample module."
    # Nesting is shown by indentation under the line number.
    full = outline.rows(False)
    assert full[4] == "   18   def __init__(self, x: int=0) -> None"
    assert full[9] == "   33     def deep(self, *args, **kw)"


def test_a_brief_outline_leaves_out_what_a_caller_would_not_use():
    assert _rows(SAMPLE, brief=True) == [
        "7 LIMIT, Pair",
        "10 TIMEOUT",
        "13 @dataclass class Point(Base, metaclass=Meta)",
        "15 x: int",
        "18 def __init__(self, x: int=0) -> None",
        "22 @property def norm(self) -> float",
        "32 class Inner",
        "33 def deep(self, *args, **kw)",
        "37 async def fetch(url: str, *, retries: int=2) -> bytes",
        "50 Alias",
    ]
    assert parse(SAMPLE.encode(), "m.py").names() == "LIMIT, Pair, TIMEOUT, Point, fetch(), Alias"


def test_definitions_under_if_try_and_with_are_found():
    rows = _rows(
        "if TYPE_CHECKING:\n    from x import Y\n    def typed() -> Y: ...\nelse:\n    ALT = 1\n"
        "try:\n    import fast\nexcept ImportError:\n    def fallback(): pass\n"
        "finally:\n    DONE = True\n"
        "with ctx():\n    class Managed: pass\n")
    assert rows == ["3 def typed() -> Y", "5 ALT", "9 def fallback()", "11 DONE",
                    "13 class Managed"]


def test_a_public_class_inside_a_private_one_is_private_too():
    source = "class _Hidden:\n    class Shown:\n        def m(self): pass\n"
    assert _rows(source, brief=True) == []
    assert _rows(source) == ["1 class _Hidden", "2 class Shown", "3 def m(self)"]


def test_a_class_with_no_fields_has_no_fields_line_and_a_private_field_alone_is_brief_hidden():
    assert _rows("class A:\n    _x: int\n    def f(self): pass\n", brief=True) == [
        "1 class A", "3 def f(self)"]
    assert _rows("class A:\n    pass\n") == ["1 class A"]


def test_a_long_signature_is_clipped_and_a_multiline_one_collapsed():
    many = ", ".join(f"argument_{n}: int = {n}" for n in range(30))
    row = parse(f"def f({many}):\n    pass\n".encode(), "m.py").rows(False)[0]
    assert len(row) == repomap.LINE_WIDTH and row.endswith("...")
    assert _rows("def g(\n    a,\n    b,\n):\n    pass\n") == ["1 def g(a, b)"]


def test_parsing_runs_nothing(tmp_path):
    marker = tmp_path / "ran"
    source = (f"open({str(marker)!r}, 'w').write('x')\nraise SystemExit(3)\n"
              "def still_listed(): pass\n")
    outline = parse(source.encode(), "evil.py")
    assert [r.strip() for r in outline.rows(False)] == ["3 def still_listed()"]
    assert not marker.exists()


def test_a_file_that_will_not_parse_says_why():
    assert parse(b"def broken(:\n", "a.py").error.startswith("line 1: ")
    assert parse(b"x = 1\x00\n", "a.py").error            # a NUL byte
    assert parse(b"(" * 300 + b")" * 300, "a.py").error   # too deeply nested


def test_an_outline_too_deep_to_walk_is_an_error_not_a_crash(monkeypatch):
    def deep(body, out):
        raise RecursionError
    monkeypatch.setattr(repomap, "_outline", deep)
    assert parse(b"x = 1\n", "a.py").error == "nested too deeply to outline"


def test_the_source_encoding_is_honoured():
    latin = "# -*- coding: latin-1 -*-\ndef caf\u00e9(): pass\n".encode("latin-1")
    assert [r.strip() for r in parse(latin, "a.py").rows(False)] == ["2 def caf\u00e9()"]
    bom = b"\xef\xbb\xbfdef f(): pass\n"
    assert [r.strip() for r in parse(bom, "a.py").rows(False)] == ["1 def f()"]


def test_a_syntax_warning_in_the_source_is_not_printed():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        outline = parse(b'PATTERN = "\\d+"\n', "a.py")
    assert not outline.error and caught == []


def test_a_long_docstring_summary_is_its_first_paragraph_clipped():
    doc = "word " * 60
    outline = parse(f'"""{doc}\n\nmore"""\n'.encode(), "a.py")
    assert len(outline.summary) == repomap.SUMMARY_WIDTH and outline.summary.endswith("...")
    assert parse(b'"""Wrapped\n   across lines."""\n', "a.py").summary == "Wrapped across lines."


# --- imports -----------------------------------------------------------------------------


def _outlines(sources: dict[str, str]):
    return [parse(text.encode(), rel) for rel, text in sources.items()]


def test_importers_counts_each_importing_file_once():
    counts = _importers(_outlines({
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": "",
        "src/pkg/util.py": "import pkg.core\nfrom pkg.core import thing\n",
        "src/pkg/cli.py": "from pkg import core\n",
        "src/pkg/sub/deep.py": "from ..core import x\nfrom .. import util\n",
        "src/pkg/sub/__init__.py": "from . import deep\n",
        "tests/test_core.py": "def t():\n    import pkg.core\n",   # inside a function
        "src/pkg/self.py": "import pkg.self\n",
        "app.py": "import os\nimport pkg\n",
    }))
    assert counts == {"src/pkg/core.py": 4, "src/pkg/util.py": 1,
                      "src/pkg/sub/deep.py": 1, "src/pkg/__init__.py": 1}


def test_a_name_two_files_answer_to_credits_neither():
    counts = _importers(_outlines({
        "a/utils.py": "", "b/utils.py": "", "main.py": "import utils\n",
        "other.py": "import a.utils\n"}))
    assert counts == {"a/utils.py": 1}


@pytest.mark.parametrize("rel, expected", [
    ("tests/x.py", True), ("test/x.py", True), ("pkg/testing/x.py", True),
    ("test_x.py", True), ("pkg/x_test.py", True), ("conftest.py", True),
    ("src/contest.py", False), ("src/latest.py", False), ("tests.py", False),
])
def test_what_counts_as_a_test_file(rel, expected):
    assert repomap._is_test(rel) is expected


def test_a_relative_import_past_the_top_is_ignored():
    counts = _importers(_outlines({"x.py": "from ...y import z\n", "y.py": ""}))
    assert counts == {}


# --- a directory's map -------------------------------------------------------------------


def test_a_map_ranks_by_importers_and_prints_in_path_order(tmp_path):
    _write(tmp_path, "pkg/base.py", '"""Shared types."""\nclass Base:\n    pass\n')
    _write(tmp_path, "pkg/a.py", "from pkg.base import Base\ndef run(): pass\n")
    _write(tmp_path, "pkg/b.py", "from pkg.base import Base\ndef go(): pass\n")
    _write(tmp_path, "tests/test_a.py", "from pkg import a\ndef test_run(): pass\n")
    out = _map(tmp_path)
    text = out.content
    assert text.startswith("Python outline of .: 4 file(s), the most imported first; 4 outlined.")
    assert "pkg/base.py (imported by 2) -- Shared types.\n    2 class Base" in text
    assert text.index("pkg/a.py") < text.index("pkg/b.py") < text.index("pkg/base.py") \
        < text.index("tests/test_a.py")
    assert out.metadata == {"files": 4, "shown": 4, "named": 0, "unparsed": 0,
                            "incomplete": False}


def test_a_tight_budget_keeps_the_most_imported_outlined_and_names_the_rest(tmp_path, monkeypatch):
    _write(tmp_path, "hub.py", "".join(f"def f{n}(): pass\n" for n in range(8)))
    for n in range(12):
        _write(tmp_path, f"leaf{n:02}.py", "import hub\n" + "".join(
            f"def leaf_function_with_a_long_name_{n}_{k}(): pass\n" for k in range(6)))
    # At the top and first by name, so only being a test puts it last.
    _write(tmp_path, "aaa_test.py", "import hub\n" + "".join(
        f"def test_with_a_name_long_enough_not_to_fit_{k}(): pass\n" for k in range(6)))
    monkeypatch.setattr(repomap, "MAP_BYTES", 900)
    out = _map(tmp_path)
    text = out.content
    assert "\n\nhub.py (imported by 13)\n    1 def f0()" in text            # outlined
    assert "\n\nleaf00.py\n    2 def leaf_function_with_a_long_name_0_0()" in text
    assert "\nleaf01.py: leaf_function_with_a_long_name_1_0(), " in text   # a line
    note = text.split("\n\n")[-1]
    assert note.startswith("[10 more file(s) not shown, to stay within the budget: leaf03.py")
    assert note.endswith("leaf11.py, aaa_test.py. Map a narrower path to see them.]")
    assert out.metadata == {"files": 14, "shown": 2, "named": 2, "unparsed": 0,
                            "incomplete": False}
    assert len(text) - len(note) <= 900 + len(text.split("\n\n")[0])


def test_a_third_of_the_budget_is_kept_for_the_lines_of_names(tmp_path, monkeypatch):
    _write(tmp_path, "big.py", "".join(f"def function_number_{n}(): pass\n" for n in range(40)))
    for n in range(6):
        _write(tmp_path, f"m{n}.py",
               f"import big\ndef some_rather_long_function_name_in_module_{n}(): pass\n")
    monkeypatch.setattr(repomap, "MAP_BYTES", 1300)
    out = _map(tmp_path)
    # big.py alone would take the budget and leave the other five unnamed;
    # with a third kept back it takes a line, and every file is in the map.
    assert "\nbig.py (imported by 6): function_number_0(), " in out.content
    assert "m5.py\n    2 def some_rather_long_function_name_in_module_5()" in out.content
    assert "not shown" not in out.content
    assert out.metadata["shown"] == 6 and out.metadata["named"] == 1


def test_a_file_without_public_definitions_takes_a_line_not_an_outline(tmp_path):
    _write(tmp_path, "pkg/__init__.py", '"""The package."""\n')
    _write(tmp_path, "pkg/_impl.py", "def _x(): pass\n")
    text = _map(tmp_path).content
    assert "pkg/__init__.py -- The package." in text
    assert "(no definitions)" not in text and "pkg/_impl.py" in text


def test_hidden_build_and_vendored_directories_are_skipped(tmp_path):
    _write(tmp_path, "src/keep.py", "def kept(): pass\n")
    for skipped in (".claude/worktrees/x/src/keep.py", ".venv/lib/a.py",
                    "node_modules/a.py", "build/a.py", "pkg.egg-info/a.py",
                    "lib/site-packages/a.py"):
        _write(tmp_path, skipped, "def copied(): pass\n")
    out = _map(tmp_path)
    assert "copied" not in out.content and out.metadata["files"] == 1


def test_a_reparse_point_directory_is_not_followed(tmp_path, monkeypatch):
    _write(tmp_path, "src/keep.py", "def kept(): pass\n")
    _write(tmp_path, "linked/elsewhere.py", "def outside(): pass\n")
    real = files_module._is_reparse_point
    monkeypatch.setattr(files_module, "_is_reparse_point",
                        lambda path: path.name == "linked" or real(path))
    assert "outside" not in _map(tmp_path).content


def test_files_that_cannot_be_outlined_are_named(tmp_path, monkeypatch):
    _write(tmp_path, "good.py", "def ok(): pass\n")
    _write(tmp_path, "bad.py", "def broken(:\n")
    _write(tmp_path, "huge.py", "x = 1\n" * 50)
    monkeypatch.setattr(repomap, "MAX_MAP_FILE_BYTES", 100)
    out = _map(tmp_path)
    assert "[2 file(s) could not be outlined: bad.py (line 1: " in out.content
    assert out.content.endswith("), huge.py (over 0 MB).]")
    assert "good.py\n    1 def ok()" in out.content
    assert out.metadata["unparsed"] == 2 and out.metadata["shown"] == 1


def test_a_map_over_its_file_cap_says_it_is_incomplete(tmp_path, monkeypatch):
    for n in range(5):
        _write(tmp_path, f"m{n}.py", f"def f{n}(): pass\n")
    monkeypatch.setattr(repomap, "MAX_MAP_FILES", 2)
    out = _map(tmp_path)
    assert "[stopped before 3 more file(s): the map reads at most 2 files" in out.content
    assert "m1.py" in out.content and "m2.py" not in out.content
    assert out.metadata["incomplete"] is True


def test_a_map_past_its_time_budget_says_it_is_incomplete(tmp_path, monkeypatch):
    for n in range(3):
        _write(tmp_path, f"m{n}.py", "x = 1\n")
    monkeypatch.setattr(repomap, "MAP_TIME_BUDGET_S", -1.0)
    out = _map(tmp_path)
    assert out.metadata["incomplete"] is True and "stopped before 3" in out.content


def test_a_cancel_stops_the_map(tmp_path):
    for n in range(3):
        _write(tmp_path, f"m{n}.py", "x = 1\n")
    out = _map(tmp_path, cancelled=lambda: True)
    assert out.metadata["incomplete"] is True and "stopped before 3" in out.content


def test_a_directory_without_python_says_so(tmp_path):
    _write(tmp_path, "docs/readme.md", "# hi\n")
    out = _map(tmp_path, "docs")
    assert out.content.startswith("(no Python files under docs;")
    assert out.metadata["files"] == 0


def test_a_directory_map_is_cut_to_the_output_cap(tmp_path):
    for n in range(30):
        _write(tmp_path, f"module_with_a_long_name_{n}.py", f"def f{n}(): pass\n")
    out = _map(tmp_path, max_output_bytes=400)
    assert out.truncated and len(out.content) <= 400


def test_the_map_is_cut_to_the_output_cap(tmp_path):
    _write(tmp_path, "m.py", "".join(f"def f{n}(): pass\n" for n in range(400)))
    out = _map(tmp_path, "m.py", max_output_bytes=500)
    assert out.truncated and len(out.content) <= 500


# --- the tool ----------------------------------------------------------------------------


def test_one_file_gets_its_whole_outline(tmp_path):
    _write(tmp_path, "pkg/mod.py", '"""Doc."""\ndef _private(): pass\ndef public(): pass\n')
    out = _map(tmp_path, "pkg/mod.py")
    assert out.content == ("pkg/mod.py -- Doc.\n    2 def _private()\n    3 def public()")
    _write(tmp_path, "empty.py", "")
    assert _map(tmp_path, "empty.py").content == "empty.py\n      (no definitions)"


def test_the_tool_refuses_what_it_cannot_outline(tmp_path, monkeypatch):
    _write(tmp_path, "notes.txt", "hi\n")
    _write(tmp_path, "bad.py", "def broken(:\n")
    _write(tmp_path, "big.py", "x = 1\n")
    with pytest.raises(ToolError, match="outlines Python files"):
        _map(tmp_path, "notes.txt")
    with pytest.raises(ToolError, match="bad.py does not parse \\(line 1: "):
        _map(tmp_path, "bad.py")
    with pytest.raises(ToolError, match="no such path: gone"):
        _map(tmp_path, "gone")
    with pytest.raises(ToolError, match="path escapes the workspace"):
        _map(tmp_path, "../outside")
    monkeypatch.setattr(repomap, "MAX_MAP_FILE_BYTES", 2)
    with pytest.raises(ToolError, match="big.py cannot be outlined: over 0 MB"):
        _map(tmp_path, "big.py")


def test_a_stub_file_can_be_outlined(tmp_path):
    _write(tmp_path, "api.pyi", "def f(x: int) -> str: ...\n")
    assert _map(tmp_path, "api.pyi").content == "api.pyi\n    1 def f(x: int) -> str"


def test_validate_takes_only_a_path():
    tool = RepoMap()
    op = tool.validate({})
    assert op.args == {"path": "."} and op.display == "map ." and op.target == "."
    for bad in ({"path": ""}, {"path": 3}, {"path": "a", "depth": 2}):
        with pytest.raises(InvalidToolCall, match="path must be|unknown argument"):
            tool.validate(bad)


def test_an_unchanged_file_is_not_parsed_again(tmp_path, monkeypatch):
    _write(tmp_path, "a.py", "def one(): pass\n")
    _write(tmp_path, "b.py", "def two(): pass\n")
    parsed: list[str] = []
    real = repomap.parse
    monkeypatch.setattr(repomap, "parse", lambda raw, rel: parsed.append(rel) or real(raw, rel))
    tool = RepoMap()
    tool.execute(tool.validate({}), _ctx(tmp_path))
    assert sorted(parsed) == ["a.py", "b.py"]
    _write(tmp_path, "a.py", "def uno(): pass\n")       # the same size: only the time says
    stat = (tmp_path / "a.py").stat()
    os.utime(tmp_path / "a.py", ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    out = tool.execute(tool.validate({}), _ctx(tmp_path))
    assert sorted(parsed) == ["a.py", "a.py", "b.py"] and "def uno()" in out.content


def test_the_cache_is_bounded(tmp_path, monkeypatch):
    for n in range(4):
        _write(tmp_path, f"m{n}.py", "x = 1\n")
    monkeypatch.setattr(repomap, "CACHE_LIMIT", 2)
    tool = RepoMap()
    tool.execute(tool.validate({}), _ctx(tmp_path))
    assert len(tool._cache) <= 2


def test_repo_map_is_registered_and_read_only_in_every_mode():
    assert "repo_map" in default_registry().names()
    op = RepoMap().validate({"path": "src"})
    for mode in Mode:
        decision = PolicyEngine(mode=mode, interactive=False).evaluate(op)[0]
        assert decision.verdict is Verdict.ALLOW, mode


# --- through the runtime -----------------------------------------------------------------


def test_the_model_gets_the_map_with_known_secrets_scrubbed(tmp_path):
    secret = "sk-test-" + "q" * 40
    redactor = Redactor()
    redactor.register(secret)
    runtime, _session, repo = _core(tmp_path, [
        turn(call("m1", "repo_map", {"path": "."})), turn(text="mapped")], redactor=redactor)
    _write(repo, "client.py", f'def connect(token: str = "{secret}") -> None:\n    pass\n')
    runtime.run_turn("map it")
    result = runtime.backend.seen[1].messages[-1].blocks[0]
    assert "def connect(token: str=" in result.content and not result.is_error
    assert secret not in result.content
