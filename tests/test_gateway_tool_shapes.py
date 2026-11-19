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

The fourth probe set on the same agent (2026-11-19, D-212) found three more:

* `apply_patch` with an @@ hunk of context lines alone -- ` def parse(...)`
  -- before the hunk that changed the function, six times in one run: codex's
  apply_patch takes such a hunk as saying where the next one is;
* `finish` with its claims keyed `claim` and `event_id`, in the finish that
  was to end that run when its rounds ran out: refused, it ended with no
  summary;
* `repo_map` with `depth`, as `list_files` takes it;
* `search_text` with `max_results`, seven times in one run, and with `tax(`
  and `.total(` -- the text, which is no regular expression -- seven more.
"""

from __future__ import annotations

import pytest

from foundry.core.errors import InvalidToolCall
from foundry.core.session import ArtifactStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.files import ListFiles, ReadFile, SearchText
from foundry.core.tools.finish import Finish
from foundry.core.tools.patch import ApplyPatch, parse_patch
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


# --- apply_patch: an @@ hunk of context alone is an anchor ----------------------------------

TIERS = ("def fee_tier_07(amount):\n    fee = amount * 0.07\n    return round(fee, 2)\n\n\n"
         "def fee_tier_27(amount):\n    fee = amount * 0.27\n    return round(fee, 2)\n")


def _patched(ctx, rel, text, patch):
    (ctx.workspace.root / rel).write_text(text, encoding="utf-8")
    ReadFile().execute(ReadFile().validate({"path": rel}), ctx)
    tool = ApplyPatch()
    return tool.execute(tool.validate({"patch": patch}), ctx)


def test_an_at_at_hunk_of_context_alone_says_where_the_next_one_is(ctx):
    # `return round(fee, 2)` is in both functions: after the anchor, the first
    # one after it is meant, as codex reads it.
    out = _patched(ctx, "rules.py", TIERS,
                   "*** Begin Patch\n*** Update File: rules.py\n@@\n def fee_tier_27(amount):\n"
                   "@@\n-    return round(fee, 2)\n+    return min(round(fee, 2), 50.0)\n"
                   "*** End Patch")
    assert not out.is_error, out.content
    assert (ctx.workspace.root / "rules.py").read_text(encoding="utf-8") == TIERS.replace(
        "0.27\n    return round(fee, 2)", "0.27\n    return min(round(fee, 2), 50.0)")
    # Below the anchor it appears twice more: the first after it is the one meant.
    three = TIERS + TIERS.replace("_07", "_08").replace("_27", "_28")
    out = _patched(ctx, "rules.py", three,
                   "*** Begin Patch\n*** Update File: rules.py\n@@\n def fee_tier_27(amount):\n"
                   "@@\n-    return round(fee, 2)\n+    return 50.0\n*** End Patch")
    assert not out.is_error, out.content
    assert (ctx.workspace.root / "rules.py").read_text(encoding="utf-8") == three.replace(
        "0.27\n    return round(fee, 2)", "0.27\n    return 50.0", 1)


def test_the_patch_the_long_probe_sent_is_applied(ctx):
    source = ("import re\nfrom typing import Iterable\n\n\n"
              "def parse(lines: Iterable[str]) -> tuple[list, int]:\n"
              "    records, skipped = [], 0\n    return records, skipped\n\n\n"
              "__all__ = [\"Record\", \"parse\"]\n")
    patch = ("*** Begin Patch\n*** Update File: logq.py\n@@\n import re\n+from os import PathLike\n"
             " from typing import Iterable\n@@\n def parse(lines: Iterable[str]) -> tuple[list, int]:\n"
             "@@\n     return records, skipped\n \n \n-__all__ = [\"Record\", \"parse\"]\n"
             "+def read(path):\n+    return parse(open(path, encoding=\"utf-8\"))\n+\n+\n"
             "+__all__ = [\"Record\", \"parse\", \"read\"]\n*** End Patch")
    out = _patched(ctx, "logq.py", source, patch)
    assert not out.is_error, out.content
    text = (ctx.workspace.root / "logq.py").read_text(encoding="utf-8")
    assert text.startswith("import re\nfrom os import PathLike\nfrom typing import Iterable\n")
    assert text.endswith("def read(path):\n    return parse(open(path, encoding=\"utf-8\"))\n\n\n"
                         "__all__ = [\"Record\", \"parse\", \"read\"]\n")


def test_an_update_whose_every_hunk_is_context_still_changes_nothing():
    with pytest.raises(InvalidToolCall, match="changes nothing"):
        parse_patch("*** Begin Patch\n*** Update File: a.py\n@@\n a\n@@\n b\n*** End Patch")
    ops = parse_patch("*** Begin Patch\n*** Update File: a.py\n@@\n a\n@@\n-b\n+c\n*** End Patch")
    assert [h.anchor for h in ops[0].hunks] == [True, False]


def test_a_hunk_is_looked_for_only_after_the_anchor_before_it(ctx):
    # The change is above its anchor: not found after it, and nothing written.
    out = _patched(ctx, "rules.py", TIERS,
                   "*** Begin Patch\n*** Update File: rules.py\n@@\n def fee_tier_27(amount):\n"
                   "@@\n-    fee = amount * 0.07\n+    fee = amount * 0.08\n*** End Patch")
    assert out.is_error and "after hunk 1" in out.content
    assert (ctx.workspace.root / "rules.py").read_text(encoding="utf-8") == TIERS
    # An anchor that is not in the file is refused as any hunk would be.
    out = _patched(ctx, "rules.py", TIERS,
                   "*** Begin Patch\n*** Update File: rules.py\n@@\n def fee_tier_99(amount):\n"
                   "@@\n-    fee = amount * 0.27\n+    fee = 1\n*** End Patch")
    assert out.is_error and "hunk 1: SEARCH text not found" in out.content
    assert (ctx.workspace.root / "rules.py").read_text(encoding="utf-8") == TIERS


# --- finish: claim and event_id --------------------------------------------------------------


def test_finish_takes_a_claim_keyed_claim_and_event_id():
    op = Finish().validate({"status": "partial", "summary": "steps 1-6 done",
                            "claims": [{"event_id": 403, "claim": "export tests pass"}]})
    (claim,) = op.args["claims"]
    assert (claim.claim_text, claim.command_event_id, claim.expected_exit_code) == (
        "export tests pass", 403, 0)
    # The names the schema gives win where both are there.
    op = Finish().validate({"status": "partial", "summary": "s", "claims": [
        {"claim_text": "a", "claim": "b", "command_event_id": 1, "event_id": 2}]})
    assert (op.args["claims"][0].claim_text, op.args["claims"][0].command_event_id) == ("a", 1)
    with pytest.raises(InvalidToolCall, match="claim_text"):
        Finish().validate({"status": "partial", "summary": "s", "claims": [{"event_id": 4}]})


# --- repo_map: depth -------------------------------------------------------------------------


def _mapped(ctx, **args) -> str:
    tool = RepoMap()
    return tool.execute(tool.validate(args), ctx).content


def test_repo_map_goes_as_many_levels_down_as_depth_says(ctx):
    one, two, every = _mapped(ctx, depth=1), _mapped(ctx, depth=2), _mapped(ctx)
    assert "top.py" in one and "pkg/mid.py" not in one
    assert "pkg/mid.py" in two and "pkg/sub/deep.py" not in two
    assert "pkg/sub/deeper/deepest.py" in every
    assert RepoMap().validate({"depth": 2}).display == "map ., 2 levels"
    for wrong in (0, -1, "2", 1.5, True):
        with pytest.raises(InvalidToolCall, match="depth"):
            RepoMap().validate({"depth": wrong})
    assert "depth" in RepoMap().schema().parameters["properties"]


# --- search_text: max_results, and text that is no regular expression ------------------------


def _searched(ctx, **args):
    tool = SearchText()
    return tool.execute(tool.validate(args), ctx)


def test_search_text_shows_at_most_max_results_matches(ctx):
    for n in range(5):
        (ctx.workspace.root / f"m{n}.py").write_text("price = 1\n", encoding="utf-8")
    out = _searched(ctx, query="price", max_results=2)
    assert len([l for l in out.content.splitlines() if ": price = 1" in l]) == 2
    assert "[5 total matches, showing 2." in out.content and out.metadata["count"] == 5
    # Never past the tool's own cap, which is what leaving it out gives.
    capped = SearchText().validate({"query": "x", "max_results": 500})
    assert capped.args == SearchText().validate({"query": "x"}).args
    assert "max_results" not in SearchText().validate({"query": "x"}).args
    for wrong in (0, -3, "10", 2.5, True):
        with pytest.raises(InvalidToolCall, match="max_results"):
            SearchText().validate({"query": "x", "max_results": wrong})


def test_search_text_looks_for_text_that_is_no_regex_as_written(ctx):
    (ctx.workspace.root / "shop.py").write_text(
        "total = tax(amount, region)\nx = order.total()\ntaxes = 2\n", encoding="utf-8")
    out = _searched(ctx, query="tax(")
    assert out.content.startswith("[not a regular expression (")
    assert "searched for the text as written]" in out.content
    assert "shop.py:1: total = tax(amount, region)" in out.content
    assert "taxes" not in out.content and out.metadata["count"] == 1
    assert _searched(ctx, query=".total(").metadata["count"] == 1
    none = _searched(ctx, query="ship(")
    assert none.content.startswith("[not a regular expression") and "(no matches)" in none.content
    # A query that is a regular expression is still one, with no such line.
    out = _searched(ctx, query=r"tax\(")
    assert out.metadata["count"] == 1 and not out.content.startswith("[")
    # And one that nests quantifiers is still refused.
    with pytest.raises(InvalidToolCall, match="nests an unbounded quantifier"):
        SearchText().validate({"query": "(a+)+b"})
