"""What real models wrote in auto mode, read as what it meant (D-189).

Probing pure auto mode against Claude, five of six tasks lost rounds to the
patch format: codex's @@ hunks, sent unasked, refused twice before the model
switched to SEARCH/REPLACE; a bare patch with no end line; a blank line in an
added file written without its '+'. Each reads one way only, so each is taken
as what it means; what could have been cut short is still refused.
"""

from __future__ import annotations

import re

import pytest

from foundry.core.errors import InvalidToolCall
from foundry.core.tools.patch import ApplyPatch, Hunk, parse_patch

from test_tools_patch import ctx, read_first  # noqa: F401 (fixture)

RULES = (
    "def fee_tier_07(amount):\n"
    "    \"\"\"Fee for tier 7.\"\"\"\n"
    "    fee = amount * 0.07\n"
    "    return round(fee, 2)\n"
    "\n"
    "\n"
    "def fee_tier_27(amount):\n"
    "    \"\"\"Fee for tier 27.\"\"\"\n"
    "    fee = amount * 0.27\n"
    "    return round(fee, 2)\n"
)


# --- codex's @@ hunks -----------------------------------------------------------------------


def test_an_at_at_hunk_is_the_anchored_hunk_it_means():
    ops = parse_patch("*** Begin Patch\n*** Update File: a.py\n@@ def f\n x = 1\n-y = 2\n+y = 3\n"
                      " z = 4\n*** End Patch")
    assert ops[0].hunks == (Hunk("x = 1\ny = 2\nz = 4", "x = 1\ny = 3\nz = 4"),)


def test_several_at_at_hunks_and_a_search_hunk_in_one_update():
    ops = parse_patch("*** Begin Patch\n*** Update File: a.py\n@@\n-a\n+b\n@@\n c\n+d\n"
                      "<<<<<<< SEARCH\ne\n=======\nf\n>>>>>>> REPLACE\n*** End Patch")
    assert ops[0].hunks == (Hunk("a", "b"), Hunk("c", "c\nd"), Hunk("e", "f"))


def test_blank_lines_in_an_at_at_hunk():
    # An empty context line whose space was dropped is a blank line inside the hunk;
    # blank lines after its last line are the gap before what follows.
    ops = parse_patch("*** Begin Patch\n*** Update File: a.py\n@@\n a\n\n-b\n+c\n\n\n"
                      "*** Update File: b.py\n@@\n-x\n+y\n*** End Patch")
    assert ops[0].hunks == (Hunk("a\n\nb", "a\n\nc"),)
    assert ops[1].hunks == (Hunk("x", "y"),)


def test_the_end_of_file_marker_is_passed_over():
    ops = parse_patch("*** Begin Patch\n*** Update File: a.py\n@@\n last\n+added\n"
                      "*** End of File\n*** End Patch")
    assert ops[0].hunks == (Hunk("last", "last\nadded"),)


@pytest.mark.parametrize("hunk, says", [
    ("@@\n a\n b", "changes nothing"),
    ("@@\n+only added", "no context or removed line"),
    ("@@\n\n+x", "no context or removed line"),
    ("@@\n a\nx = 2", "must start with ' ', '-' or '+'"),
])
def test_an_at_at_hunk_that_cannot_be_placed_is_refused(hunk, says):
    with pytest.raises(InvalidToolCall, match=re.escape(says)):
        parse_patch(f"*** Begin Patch\n*** Update File: a.py\n{hunk}\n*** End Patch")


def test_the_patch_the_probe_sent_is_applied(ctx):  # noqa: F811
    """b-surgical's first patch, as the model sent it: bare, @@ hunks, no end."""
    (ctx.workspace.root / "rules.py").write_text(RULES, encoding="utf-8")
    read_first(ctx, "rules.py")
    patch = ("*** Update File: rules.py\n@@\n     \"\"\"Fee for tier 7.\"\"\"\n"
             "-    fee = amount * 0.07\n+    fee = max(amount * 0.07, 1.00)\n"
             "     return round(fee, 2)\n@@\n     \"\"\"Fee for tier 27.\"\"\"\n"
             "-    fee = amount * 0.27\n+    fee = min(amount * 0.27, 50.00)\n"
             "     return round(fee, 2)\n")
    tool = ApplyPatch()
    out = tool.execute(tool.validate({"patch": patch}), ctx)
    assert not out.is_error, out.content
    text = (ctx.workspace.root / "rules.py").read_text(encoding="utf-8")
    assert text == RULES.replace("fee = amount * 0.07", "fee = max(amount * 0.07, 1.00)").replace(
        "fee = amount * 0.27", "fee = min(amount * 0.27, 50.00)")


def test_an_at_at_hunk_is_located_as_any_hunk_is(ctx):  # noqa: F811
    # Its lines appear twice: refused as an ambiguous SEARCH would be, nothing written.
    (ctx.workspace.root / "rules.py").write_text(RULES, encoding="utf-8")
    read_first(ctx, "rules.py")
    tool = ApplyPatch()
    out = tool.execute(tool.validate({"patch": "*** Update File: rules.py\n@@\n-    return round(fee, 2)\n"
                                                "+    return fee\n"}), ctx)
    assert out.is_error and "appears 2 times" in out.content
    assert (ctx.workspace.root / "rules.py").read_text(encoding="utf-8") == RULES


# --- the bare form's end line -------------------------------------------------------------


def test_a_bare_patch_needs_no_end_line():
    ops = parse_patch("*** Update File: a.py\n<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\n")
    assert ops[0].hunks == (Hunk("x", "y"),)
    ops = parse_patch("*** Add File: b.py\n+b = 1\n\n")
    assert [(op.action, op.content) for op in ops] == [("add", "b = 1\n")]


def test_a_patch_that_began_and_lost_its_end_is_still_refused():
    # It may have been cut short: what was cut off would be lost unseen.
    with pytest.raises(InvalidToolCall, match="must end with"):
        parse_patch("*** Begin Patch\n*** Add File: a.py\n+x\n")


# --- blank lines in an added file -------------------------------------------------------------


def test_a_blank_line_without_its_plus_is_a_blank_line():
    ops = parse_patch("*** Begin Patch\n*** Add File: a.py\n+import os\n\n\n+def f():\n"
                      "+    return 1\n   \n+x = 2\n*** End Patch")
    assert ops[0].content == "import os\n\n\ndef f():\n    return 1\n\nx = 2\n"


def test_blank_lines_before_the_next_operation_are_not_the_files():
    ops = parse_patch("*** Begin Patch\n*** Add File: a.py\n+a\n\n\n*** Add File: b.py\n+b\n\n"
                      "*** End Patch")
    assert [op.content for op in ops] == ["a\n", "b\n"]


def test_other_lines_in_an_added_file_still_need_their_plus():
    with pytest.raises(InvalidToolCall, match="must start with '\\+'"):
        parse_patch("*** Begin Patch\n*** Add File: a.py\n+a\nb\n*** End Patch")


# --- what a refusal says ---------------------------------------------------------------------------


def test_an_update_with_no_hunk_is_told_the_format():
    with pytest.raises(InvalidToolCall) as info:
        parse_patch("*** Begin Patch\n*** Update File: a.py\n*** End Patch")
    assert "<<<<<<< SEARCH, the exact lines to find, =======" in str(info.value)
