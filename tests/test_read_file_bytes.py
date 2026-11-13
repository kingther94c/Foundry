"""read_file says what its numbered lines cannot show (D-191).

Probing auto mode, a model twice tried a shell command to learn whether a
file's lines ended in CRLF and whether it began with a byte order mark before
patching it -- both refused -- when read_file knew and did not say.
"""

from __future__ import annotations

import pytest

from foundry.core.tools.files import ReadFile

from test_tools_patch import ctx  # noqa: F401 (fixture)


def _header(ctx, data: bytes) -> str:  # noqa: F811
    (ctx.workspace.root / "f.txt").write_bytes(data)
    tool = ReadFile()
    return tool.execute(tool.validate({"path": "f.txt"}), ctx).content.split("\n")[0]


@pytest.mark.parametrize("data, header", [
    (b"a\nb\n", "f.txt (2 lines total)"),
    (b"a\r\nb\r\n", "f.txt (2 lines total; CRLF line endings, which apply_patch keeps)"),
    (b"a\r\nb\r\nc\n", "f.txt (3 lines total; mixed line endings (2 CRLF, 1 LF): "
                       "apply_patch makes them all CRLF)"),
    (b"a\r\nb\nc\n", "f.txt (3 lines total; mixed line endings (1 CRLF, 2 LF): "
                     "apply_patch makes them all LF)"),
    (b"\xef\xbb\xbfa\r\n", "f.txt (1 lines total; CRLF line endings, which apply_patch keeps; "
                           "a UTF-8 byte order mark, which apply_patch keeps)"),
    (b"caf\xe9\n", "f.txt (1 lines total; not valid UTF-8: what does not decode is shown "
                   "as \ufffd, and apply_patch cannot edit it)"),
])
def test_the_header_says_what_the_lines_do_not_show(ctx, data, header):  # noqa: F811
    assert _header(ctx, data) == header


def test_the_byte_order_mark_is_not_shown_as_part_of_the_first_line(ctx):  # noqa: F811
    (ctx.workspace.root / "f.txt").write_bytes(b"\xef\xbb\xbf[core]\n")
    tool = ReadFile()
    out = tool.execute(tool.validate({"path": "f.txt"}), ctx).content
    assert out.split("\n")[1] == "     1\t[core]"
