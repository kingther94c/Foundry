"""A patch that leaves a Python file unable to parse is refused (D-194).

SWE-agent's edit command refuses such an edit; its ablation on SWE-bench Lite
measured 18.0% solved with the check against 15.0% without.
"""

from __future__ import annotations

from foundry.core.tools.patch import ApplyPatch

from test_tools_patch import ctx, patch_text, read_first  # noqa: F401 (fixture)

GOOD = "def f():\n    return 1\n"


def _patch(ctx, body):  # noqa: F811
    tool = ApplyPatch()
    return tool.execute(tool.validate({"patch": patch_text(body)}), ctx)


def _update(path, search, replace):
    return f"*** Update File: {path}\n<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE"


def test_an_update_that_breaks_the_syntax_is_refused_and_says_where(ctx):  # noqa: F811
    target = ctx.workspace.root / "src" / "f.py"
    target.write_text(GOOD, encoding="utf-8")
    read_first(ctx, "src/f.py")
    out = _patch(ctx, _update("src/f.py", "    return 1", "    return (1"))
    assert out.is_error
    assert "would not parse as Python" in out.content and "line 2" in out.content
    assert "'return (1'" in out.content and "left as it was" in out.content
    assert target.read_text(encoding="utf-8") == GOOD


def test_other_files_in_the_patch_still_apply(ctx):  # noqa: F811
    (ctx.workspace.root / "src" / "f.py").write_text(GOOD, encoding="utf-8")
    read_first(ctx, "src/f.py")
    read_first(ctx)
    out = _patch(ctx, _update("src/f.py", "    return 1", "    return (1") + "\n"
                 + _update("src/app.py", "    return 1", "    return 2"))
    assert out.is_error and "Applied:" in out.content
    assert "return 2" in (ctx.workspace.root / "src" / "app.py").read_text(encoding="utf-8")


def test_an_added_file_that_does_not_parse_is_refused(ctx):  # noqa: F811
    out = _patch(ctx, "*** Add File: src/new.py\n+def f(:\n+    pass")
    assert out.is_error and "would not parse as Python" in out.content
    assert not (ctx.workspace.root / "src" / "new.py").exists()


def test_a_file_that_did_not_parse_before_is_not_judged(ctx):  # noqa: F811
    # Written for another Python, or broken already: the patch may be the fix, or a step.
    target = ctx.workspace.root / "src" / "py2.py"
    target.write_text("print 'hello'\nx = 1\n", encoding="utf-8")
    read_first(ctx, "src/py2.py")
    out = _patch(ctx, _update("src/py2.py", "x = 1", "x = (2"))
    assert not out.is_error, out.content


def test_a_fix_for_a_broken_file_goes_in(ctx):  # noqa: F811
    target = ctx.workspace.root / "src" / "b.py"
    target.write_text("def f(:\n    pass\n", encoding="utf-8")
    read_first(ctx, "src/b.py")
    out = _patch(ctx, _update("src/b.py", "def f(:", "def f():"))
    assert not out.is_error and target.read_text(encoding="utf-8") == "def f():\n    pass\n"


def test_only_python_is_judged(ctx):  # noqa: F811
    out = _patch(ctx, "*** Add File: src/notes.txt\n+def f(:")
    assert not out.is_error
    out = _patch(ctx, "*** Add File: src/stub.pyi\n+def f(: ...")
    assert out.is_error


def test_a_move_is_judged_by_where_it_goes(ctx):  # noqa: F811
    target = ctx.workspace.root / "src" / "t.txt"
    target.write_text("a = 1\n", encoding="utf-8")
    read_first(ctx, "src/t.txt")
    out = _patch(ctx, "*** Update File: src/t.txt\n*** Move to: src/t.py\n"
                      "<<<<<<< SEARCH\na = 1\n=======\nb = (\n>>>>>>> REPLACE")
    assert out.is_error and "would not parse as Python" in out.content
