"""JSON files, including the ones Windows writes (SPEC.md "researchos.jsonio").

Windows PowerShell 5.1 writes UTF-16 with a byte order mark when output is
redirected with ``>``, and ``Out-File -Encoding utf8`` (like older Notepad)
puts a UTF-8 byte order mark in front. ``read_json`` decodes a file by its
byte order mark, so such a file reads like a plain UTF-8 one. Every JSON file
ResearchOS reads goes through it; ``write_json`` writes plain UTF-8.
"""

from __future__ import annotations

import codecs
import json
from pathlib import Path

# Byte order marks and the codec each one names, in the order they are checked.
# A file without a mark is UTF-8.
_MARKS = (
    (codecs.BOM_UTF8, "utf-8"),  # EF BB BF
    (codecs.BOM_UTF16_LE, "utf-16-le"),  # FF FE
    (codecs.BOM_UTF16_BE, "utf-16-be"),  # FE FF
)


def _codec(raw: bytes) -> tuple[str, bytes]:
    """The codec raw's byte order mark names (UTF-8 without one) and the bytes after the mark."""
    for mark, codec in _MARKS:
        if raw.startswith(mark):
            return codec, raw[len(mark):]
    return "utf-8", raw


def read_json(path) -> object:
    """Parse the JSON file at path, decoded by its byte order mark.

    ``EF BB BF`` is UTF-8, ``FF FE`` UTF-16 little-endian, ``FE FF`` UTF-16
    big-endian, the mark itself dropped; no mark means UTF-8. Raises
    ValueError naming path if the bytes do not decode or the text is not JSON
    (an empty file included). OSError (a missing file, a directory) propagates
    unchanged.
    """
    raw = Path(path).read_bytes()
    codec, body = _codec(raw)
    try:
        text = body.decode(codec)
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path}: not valid {codec} text ({exc})") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from exc


def write_json(path, data) -> None:
    """Write ``json.dumps(data, indent=2, ensure_ascii=False)`` and a newline.

    UTF-8 with no byte order mark, newlines exactly as dumped (no CRLF on
    Windows). The text is encoded before the file is opened, so data that
    cannot be serialized or encoded leaves an existing file as it was.
    """
    payload = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    Path(path).write_bytes(payload)
