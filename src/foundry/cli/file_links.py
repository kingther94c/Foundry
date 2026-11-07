"""A file the model cites, as a link that opens it in the user's editor
(D-106), as codex's ``file_opener`` makes one: ``src/app.py:42`` in inline
code opens that file at that line in VS Code, or Cursor or Windsurf.

The text is the model's, so the link is made only for what it shows: an
existing file inside the workspace, named by the citation itself. The target
is built here -- the editor's scheme, the file's own path, the line -- never
taken from the text, so a citation cannot point anywhere but at the file it
names. A Markdown link stays text (D-070): its address is the model's.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

# What a citation may look like: a path without spaces, then a line, a line
# and column, or a range.
_CITATION = re.compile(
    r"(?P<path>(?:[A-Za-z]:[\\/])?[^\s:]+?)"
    r"(?::(?P<line>\d{1,7})(?::(?P<column>\d{1,5})|-\d{1,7})?)?")
# Longer than any path Windows opens without the long-path prefix.
_MAX_CITATION = 300


def file_link(text: str, workspace: Path, opener: str) -> str | None:
    """The link ``text`` cites, if it names a file in ``workspace``; None
    for anything else, and for every citation when ``opener`` is ``none``."""
    if opener == "none" or len(text) > _MAX_CITATION:
        return None
    match = _CITATION.fullmatch(text)
    if match is None:
        return None
    try:
        root = workspace.resolve()
        path = (root / match["path"]).resolve()
        path.relative_to(root)
        if not path.is_file():
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    where = urllib.parse.quote(path.as_posix(), safe="/:")
    if not where.startswith("/"):
        where = "/" + where
    if match["line"]:
        where += f":{match['line']}" + (f":{match['column']}" if match["column"] else "")
    return f"{opener}://file{where}"
