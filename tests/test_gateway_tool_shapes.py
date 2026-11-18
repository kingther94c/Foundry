"""What a GPT model behind a local agent gateway sent the tools (D-210).

The eleven probe projects, run once each on OpenClaw's `openclaw/foundry`
agent (2026-11-18), passed but for one; the calls Foundry refused along the
way were these:

* `list_files` with `depth`, six times in four runs -- codex's directory
  listing takes one, and these models reach for it;
* `repo_map` with `{"path": ""}`, four times: its schema says the path
  defaults to the root, as `list_files`'s does, and that one has taken ""
  as the root since D-149;
* `apply_patch` with U+0000 where a backtick was meant, nine files in one
  run. A Python file was refused by the syntax check (D-193); any other
  would have been written with NUL bytes in it.
"""

from __future__ import annotations

import pytest

from foundry.core.errors import InvalidToolCall
from foundry.core.session import ArtifactStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.files import ListFiles
from foundry.core.tools.patch import ApplyPatch
from foundry.core.tools.repomap import RepoMap
from foundry.core.workspace import Workspace


@pytest.fixture
def ctx(tmp_path):
    root = tmp_path / "repo"
    for rel in ("top.py", "pkg/mid.py", "pkg/sub/deep.py", "pkg/sub/deeper/deepest.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x = 1\n", encoding="utf-8")
    return ToolContext(workspace=Workspace(root), artifacts=ArtifactStore(tmp_path / "artifacts"),
                       read_tracker=ReadTracker())


def _listed(ctx, **args) -> set[str]:
    tool = ListFiles()
    return set(tool.execute(tool.validate(args), ctx).content.splitlines())


# --- list_files: depth -----------------------------------------------------------------------


def test_list_files_goes_as_many_levels_down_as_depth_says(ctx):
    assert _listed(ctx, depth=1) == {"top.py"}
    assert _listed(ctx, depth=2) == {"top.py", "pkg/mid.py"}
    assert _listed(ctx, path="pkg", depth=2) == {"pkg/mid.py", "pkg/sub/deep.py"}
    # Left out, every level, as before.
    assert _listed(ctx) == {"top.py", "pkg/mid.py", "pkg/sub/deep.py",
                            "pkg/sub/deeper/deepest.py"}


def test_list_files_depth_is_a_positive_whole_number(ctx):
    for wrong in (0, -1, "2", 1.5, True):
        with pytest.raises(InvalidToolCall, match="depth"):
            ListFiles().validate({"depth": wrong})
    assert "depth" in ListFiles().schema().parameters["properties"]
    assert ListFiles().validate({"depth": 2}).display == "list . (*), 2 levels"
    assert ListFiles().validate({"depth": 1}).display == "list . (*), 1 level"


# --- repo_map: "" is the root ----------------------------------------------------------------


def test_repo_map_takes_an_empty_path_as_the_root(ctx):
    tool = RepoMap()
    op = tool.validate({"path": ""})
    assert op.args["path"] == "." and op.target == "."
    assert "top.py" in tool.execute(op, ctx).content
    for wrong in (None, 3, "   "):
        with pytest.raises(InvalidToolCall, match="path"):
            tool.validate({"path": wrong})


# --- apply_patch: no NUL ---------------------------------------------------------------------


def test_a_patch_holding_a_nul_character_is_refused_and_says_where(ctx):
    patch = ("*** Begin Patch\n*** Add File: README.md\n+# geo\n+\n"
             "+\x00geo.utils.haversine_km(a, b)\x00 gives the distance.\n*** End Patch\n")
    with pytest.raises(InvalidToolCall) as refused:
        ApplyPatch().validate({"patch": patch})
    said = str(refused.value)
    assert "U+0000" in said and "line 5" in said and "backtick" in said
    assert not (ctx.workspace.root / "README.md").exists()
    # The same patch with the backticks it meant is taken.
    ApplyPatch().validate({"patch": patch.replace("\x00", "`")})
