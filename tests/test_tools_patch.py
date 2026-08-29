from __future__ import annotations

import pytest

from foundry.core.errors import InvalidToolCall, StaleFileError, ToolError
from foundry.core.session import ArtifactStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.files import ReadFile
from foundry.core.tools.patch import ApplyPatch, parse_patch
from foundry.core.workspace import Workspace

APP = "def run():\n    return 1\n"


@pytest.fixture()
def ctx(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text(APP, encoding="utf-8")
    return ToolContext(
        workspace=Workspace(repo),
        artifacts=ArtifactStore(tmp_path / "artifacts"),
        read_tracker=ReadTracker(),
    )


def read_first(ctx, path="src/app.py"):
    tool = ReadFile()
    tool.execute(tool.validate({"path": path}), ctx)


def patch_text(body: str) -> str:
    return f"*** Begin Patch\n{body}\n*** End Patch"


def test_update_replaces_anchored_text(ctx):
    read_first(ctx)
    tool = ApplyPatch()
    patch = patch_text(
        "*** Update File: src/app.py\n<<<<<<< SEARCH\n    return 1\n=======\n    return 2\n>>>>>>> REPLACE"
    )
    out = tool.execute(tool.validate({"patch": patch}), ctx)
    assert not out.is_error
    assert (ctx.workspace.root / "src" / "app.py").read_text(encoding="utf-8") == "def run():\n    return 2\n"


def test_edit_without_reading_is_refused(ctx):
    tool = ApplyPatch()
    patch = patch_text(
        "*** Update File: src/app.py\n<<<<<<< SEARCH\n    return 1\n=======\n    return 2\n>>>>>>> REPLACE"
    )
    out = tool.execute(tool.validate({"patch": patch}), ctx)
    assert out.is_error
    assert "read this file before editing" in out.content
    assert (ctx.workspace.root / "src" / "app.py").read_text(encoding="utf-8") == APP


def test_ambiguous_anchor_names_the_count(ctx):
    (ctx.workspace.root / "src" / "dup.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    read_first(ctx, "src/dup.py")
    tool = ApplyPatch()
    patch = patch_text(
        "*** Update File: src/dup.py\n<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE"
    )
    out = tool.execute(tool.validate({"patch": patch}), ctx)
    assert out.is_error
    assert "appears 2 times" in out.content


def test_missing_anchor_quotes_nearest_lines(ctx):
    read_first(ctx)
    tool = ApplyPatch()
    patch = patch_text(
        "*** Update File: src/app.py\n<<<<<<< SEARCH\n    return 99\n=======\n    return 2\n>>>>>>> REPLACE"
    )
    out = tool.execute(tool.validate({"patch": patch}), ctx)
    assert out.is_error
    assert "Nearest lines" in out.content
    assert "return 1" in out.content


def test_per_file_atomicity(ctx):
    """A file with one bad hunk keeps all its original content."""
    read_first(ctx)
    tool = ApplyPatch()
    patch = patch_text(
        "*** Update File: src/app.py\n"
        "<<<<<<< SEARCH\ndef run():\n=======\ndef go():\n>>>>>>> REPLACE\n"
        "<<<<<<< SEARCH\n    return 99\n=======\n    return 2\n>>>>>>> REPLACE"
    )
    out = tool.execute(tool.validate({"patch": patch}), ctx)
    assert out.is_error
    assert (ctx.workspace.root / "src" / "app.py").read_text(encoding="utf-8") == APP


def test_other_files_still_apply_when_one_fails(ctx):
    (ctx.workspace.root / "src" / "util.py").write_text("A = 1\n", encoding="utf-8")
    read_first(ctx)
    read_first(ctx, "src/util.py")
    tool = ApplyPatch()
    patch = patch_text(
        "*** Update File: src/util.py\n<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n>>>>>>> REPLACE\n"
        "*** Update File: src/app.py\n<<<<<<< SEARCH\nnope\n=======\nx\n>>>>>>> REPLACE"
    )
    out = tool.execute(tool.validate({"patch": patch}), ctx)
    assert out.is_error
    assert (ctx.workspace.root / "src" / "util.py").read_text(encoding="utf-8") == "A = 2\n"
    assert (ctx.workspace.root / "src" / "app.py").read_text(encoding="utf-8") == APP


def test_add_and_delete(ctx):
    tool = ApplyPatch()
    add = patch_text("*** Add File: src/new.py\n+VALUE = 3\n+")
    tool.execute(tool.validate({"patch": add}), ctx)
    assert (ctx.workspace.root / "src" / "new.py").read_text(encoding="utf-8") == "VALUE = 3\n"

    delete = patch_text("*** Delete File: src/new.py")
    tool.execute(tool.validate({"patch": delete}), ctx)
    assert not (ctx.workspace.root / "src" / "new.py").exists()


def test_add_refuses_to_clobber(ctx):
    tool = ApplyPatch()
    out = tool.execute(tool.validate({"patch": patch_text("*** Add File: src/app.py\n+x")}), ctx)
    assert out.is_error
    assert "already exists" in out.content


def test_crlf_is_preserved(ctx):
    target = ctx.workspace.root / "src" / "crlf.py"
    target.write_bytes(b"a = 1\r\nb = 2\r\n")
    read_first(ctx, "src/crlf.py")
    tool = ApplyPatch()
    patch = patch_text(
        "*** Update File: src/crlf.py\n<<<<<<< SEARCH\nb = 2\n=======\nb = 3\n>>>>>>> REPLACE"
    )
    out = tool.execute(tool.validate({"patch": patch}), ctx)
    assert not out.is_error
    assert target.read_bytes() == b"a = 1\r\nb = 3\r\n"


def test_bom_is_preserved(ctx):
    target = ctx.workspace.root / "src" / "bom.py"
    target.write_bytes(b"\xef\xbb\xbfvalue = 1\n")
    read_first(ctx, "src/bom.py")
    tool = ApplyPatch()
    patch = patch_text(
        "*** Update File: src/bom.py\n<<<<<<< SEARCH\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE"
    )
    tool.execute(tool.validate({"patch": patch}), ctx)
    assert target.read_bytes() == b"\xef\xbb\xbfvalue = 2\n"


def test_trailing_whitespace_tolerance(ctx):
    target = ctx.workspace.root / "src" / "ws.py"
    target.write_text("def f():   \n    pass\n", encoding="utf-8")
    read_first(ctx, "src/ws.py")
    tool = ApplyPatch()
    patch = patch_text(
        "*** Update File: src/ws.py\n<<<<<<< SEARCH\ndef f():\n    pass\n=======\ndef f():\n    return 1\n>>>>>>> REPLACE"
    )
    out = tool.execute(tool.validate({"patch": patch}), ctx)
    assert not out.is_error
    assert "return 1" in target.read_text(encoding="utf-8")


def test_write_outside_workspace_refused(ctx):
    tool = ApplyPatch()
    out = tool.execute(tool.validate({"patch": patch_text("*** Add File: ../evil.py\n+x")}), ctx)
    assert out.is_error
    assert "evil.py" in out.content


def test_write_into_git_dir_refused(ctx):
    (ctx.workspace.root / ".git").mkdir()
    tool = ApplyPatch()
    out = tool.execute(tool.validate({"patch": patch_text("*** Add File: .git/hooks/pre-commit\n+x")}), ctx)
    assert out.is_error


def test_repeated_failure_nudges_a_reread(ctx):
    read_first(ctx)
    tool = ApplyPatch()
    patch = patch_text(
        "*** Update File: src/app.py\n<<<<<<< SEARCH\nnope\n=======\nx\n>>>>>>> REPLACE"
    )
    for _ in range(3):
        out = tool.execute(tool.validate({"patch": patch}), ctx)
    assert "read it again" in out.content


@pytest.mark.parametrize("bad", [
    "no envelope at all",
    "*** Begin Patch\n*** End Patch",
    "*** Begin Patch\n*** Update File: a.py\n*** End Patch",
    "*** Begin Patch\n*** Update File: a.py\n<<<<<<< SEARCH\nx\n*** End Patch",
    "*** Begin Patch\n*** Add File: a.py\nmissing plus\n*** End Patch",
])
def test_malformed_patches_rejected_at_validation(bad):
    with pytest.raises(InvalidToolCall):
        ApplyPatch().validate({"patch": bad})


def test_parse_supports_move():
    ops = parse_patch(patch_text(
        "*** Update File: a.py\n*** Move to: b.py\n<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE"
    ))
    assert ops[0].move_to == "b.py"
