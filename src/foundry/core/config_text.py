"""A configuration file's text, read as Windows editors and shells save it.

Foundry's files are UTF-8. Notepad and PowerShell 5.1's ``Set-Content
-Encoding utf8`` begin one with a byte-order mark, which TOML does not allow
and which ``tomllib`` reported as a syntax error on line 1. PowerShell 5.1's
``>`` and ``Out-File`` write UTF-16, which ended in a traceback. A mark is
now taken off; UTF-16, or bytes that are not UTF-8 at all, is said for what
it is, with how to save the file again.
"""

from __future__ import annotations

from pathlib import Path

from foundry.core.errors import ConfigError

_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def _save_as_utf8(path: Path) -> str:
    # The parentheses read the whole file before Set-Content opens it again.
    return (f"save it again as UTF-8 -- in Notepad, File > Save as, Encoding: UTF-8; or in "
            f"PowerShell: (Get-Content -LiteralPath '{path}') | Set-Content -Encoding utf8 "
            f"-LiteralPath '{path}'")


def read_config_text(path: Path) -> str:
    """The text of the configuration file at ``path``, without a UTF-8 mark.

    Raises ``ConfigError`` for a file that cannot be read, one saved as
    UTF-16, and one that is not UTF-8."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc
    if data.startswith(_UTF8_BOM):
        data = data[len(_UTF8_BOM):]
    # A UTF-16 file without a mark still has a zero byte beside each ASCII
    # character, and no UTF-8 text a configuration holds has one.
    if data.startswith(_UTF16_BOMS) or b"\x00" in data:
        raise ConfigError(f"{path} is saved as UTF-16, and Foundry reads UTF-8: "
                          + _save_as_utf8(path))
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        line = data[:exc.start].count(b"\n") + 1
        raise ConfigError(f"{path} is not UTF-8 (byte 0x{data[exc.start]:02X} on line {line}), "
                          "as a file saved in the system's code page is not: "
                          + _save_as_utf8(path)) from exc
