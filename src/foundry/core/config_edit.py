"""Adding and removing an MCP server in the user's own config.toml (D-082),
as ``codex mcp add`` and ``codex mcp remove`` do, and turning a feature on
or off there (D-144), as ``codex features enable`` and ``disable`` do.

The standard library reads TOML but does not write it, and the file is the
user's: its comments, order and layout are theirs. So an edit is text -- a
table appended, or one table's lines taken out -- and it is checked before
anything is written: the edited text must parse, and parse to exactly the
old settings with that one server added or gone. An edit that does not is
refused, and the file is left as it was; a server written some other way
(inline, dotted keys, under a profile) is for the user to change by hand.
The file is replaced in one step, so a failure part way never leaves half
of one.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from foundry.core.config import _parse_mcp_servers
from foundry.core.errors import ConfigError

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+\Z")
# A table header line, [a.b] -- not an array of tables, [[a.b]] -- and any
# header at all. A line of a value that looks like one ends a table early,
# and the check after the edit refuses it.
_HEADER = re.compile(r"\s*\[(?!\[)([^\[\]]+)\]\s*(#.*)?\Z")
_ANY_HEADER = re.compile(r"\s*\[\[?[^\[\]]+\]\]?\s*(#.*)?\Z")
# A setting's line: its key, bare or quoted, and a comment after its value.
_SETTING = re.compile(r'\s*(?:([A-Za-z0-9_-]+)|"([^"\\]*)")\s*=[^#]*(#.*)?\Z')


def toml_string(value: str) -> str:
    """A TOML basic string. JSON's escapes are TOML's, except that TOML also
    refuses DEL and any \\u escape of a surrogate -- so non-ASCII is written
    as itself, not escaped."""
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return toml_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(v)}" for k, v in value.items()) + " }"
    raise TypeError(f"no TOML for {type(value).__name__}")


def _key(key: str) -> str:
    return key if _BARE_KEY.match(key) else toml_string(key)


def server_table(name: str, entry: dict[str, Any]) -> str:
    """``[mcp_servers.<name>]`` and its settings, as TOML."""
    lines = [f"[mcp_servers.{_key(name)}]"]
    lines += [f"{_key(key)} = {_value(value)}" for key, value in entry.items()]
    return "\n".join(lines) + "\n"


def _read(path: Path) -> tuple[str, dict[str, Any]]:
    if not path.is_file():
        return "", {}
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        return text, tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc


def _servers(data: dict[str, Any]) -> dict[str, Any]:
    servers = data.get("mcp_servers", {})
    return servers if isinstance(servers, dict) else {}


def _without(data: dict[str, Any], name: str) -> dict[str, Any]:
    """The settings with that server gone (and an empty mcp_servers with it,
    which is how the file reads once its last table is taken out)."""
    data = copy.deepcopy(data)
    servers = _servers(data)
    servers.pop(name, None)
    if "mcp_servers" in data and not servers:
        del data["mcp_servers"]
    return data


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".config-", suffix=".toml", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(text.encode("utf-8"))
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def add_server(path: Path, name: str, entry: dict[str, Any]) -> None:
    """Append ``[mcp_servers.<name>]`` to the config at ``path``."""
    _parse_mcp_servers({name: entry})          # the same checks as reading it
    text, data = _read(path)
    if name in _servers(data):
        raise ConfigError(f"{path} already has an MCP server named {name}; "
                          f"`foundry mcp remove {name}` first")
    newline = "\r\n" if "\r\n" in text else "\n"
    table = server_table(name, entry).replace("\n", newline)
    if text and not text.endswith(("\n", "\r")):
        text += newline
    edited = text + (newline if text.strip() else "") + table
    try:
        parsed = tomllib.loads(edited)
    except tomllib.TOMLDecodeError:
        parsed = None
    if (parsed is None or _servers(parsed).get(name) != entry
            or _without(parsed, name) != _without(data, name)):
        # mcp_servers written inline, say, which a table cannot extend.
        raise ConfigError(f"the servers in {path} are not written as [mcp_servers.<name>] "
                          f"tables, so {name} cannot be added to them here; add it by hand")
    _write(path, edited)


def _header_key(line: str) -> str | None:
    match = _HEADER.match(line)
    if match is None:
        return None
    return ".".join(part.strip().strip("\"'") for part in match.group(1).split("."))


def remove_server(path: Path, name: str) -> None:
    """Take ``[mcp_servers.<name>]`` -- and its subtables, such as its env --
    out of the config at ``path``."""
    text, data = _read(path)
    if name not in _servers(data):
        raise ConfigError(f"{path} has no MCP server named {name}")
    lines = text.splitlines(keepends=True)
    own = f"mcp_servers.{name}"
    kept: list[str] = []
    found = False
    index = 0
    while index < len(lines):
        key = _header_key(lines[index].rstrip("\r\n"))
        if key is not None and (key == own or key.startswith(own + ".")):
            found = True
            # Comments right above it, set apart from what is before them by
            # a blank line, are about it: they go too.
            above = len(kept)
            while above > 0 and kept[above - 1].strip().startswith("#"):
                above -= 1
            if above < len(kept) and above > 0 and not kept[above - 1].strip():
                del kept[above:]
            end = index + 1
            while end < len(lines) and not _ANY_HEADER.match(lines[end].rstrip("\r\n")):
                end += 1
            # Comments at its end that a blank line sets apart belong to what
            # follows: they stay. Those right under its settings were its own.
            tail = end
            while tail > index + 1 and (not lines[tail - 1].strip()
                                        or lines[tail - 1].strip().startswith("#")):
                tail -= 1
            gap = next((i for i in range(tail, end) if not lines[i].strip()), None)
            if gap is not None:
                kept.extend(lines[gap + 1:end])
            index = end
            continue
        kept.append(lines[index])
        index += 1
    edited = "".join(kept)
    try:
        parsed = tomllib.loads(edited) if found else None
    except tomllib.TOMLDecodeError:
        parsed = None
    if parsed is None or parsed != _without(data, name):
        raise ConfigError(f"{name} is not written as an [mcp_servers.{name}] table in "
                          f"{path}, so it cannot be taken out here; remove it by hand")
    newline = "\r\n" if "\r\n" in text else "\n"
    _write(path, edited.rstrip() + (newline if edited.strip() else ""))


def set_feature(path: Path, name: str, value: bool) -> None:
    """``name = value`` under ``[features]`` in the config at ``path``, as
    codex's ``features enable`` writes it: its line changed where it has one,
    added under the table's header where it has none, and the table appended
    to a file without one."""
    text, data = _read(path)
    expected = copy.deepcopy(data)
    features = expected.setdefault("features", {})
    if not isinstance(features, dict):
        raise ConfigError(f"features in {path} is not a table; set {name} there by hand")
    features[name] = value
    newline = "\r\n" if "\r\n" in text else "\n"
    line = f"{_key(name)} = {_value(value)}"
    lines = text.splitlines(keepends=True)
    header = next((i for i, text_line in enumerate(lines)
                   if _header_key(text_line.rstrip("\r\n")) == "features"), None)
    if header is None:
        if text and not text.endswith(("\n", "\r")):
            text += newline
        edited = text + (newline if text.strip() else "") + f"[features]{newline}{line}{newline}"
    else:
        if not lines[header].endswith(("\n", "\r")):
            lines[header] += newline
        end = header + 1
        while end < len(lines) and not _ANY_HEADER.match(lines[end].rstrip("\r\n")):
            end += 1
        for index in range(header + 1, end):
            match = _SETTING.match(lines[index].rstrip("\r\n"))
            if match and name in (match.group(1), match.group(2)):
                # Its comment kept, as the rest of the user's file is.
                comment = f"  {match.group(3)}" if match.group(3) else ""
                ending = lines[index][len(lines[index].rstrip("\r\n")):] or newline
                lines[index] = line + comment + ending
                break
        else:
            lines.insert(header + 1, line + newline)
        edited = "".join(lines)
    try:
        parsed = tomllib.loads(edited)
    except tomllib.TOMLDecodeError:
        parsed = None
    if parsed != expected:
        # [features] written inline or as dotted keys, which a line here
        # cannot change.
        raise ConfigError(f"the features in {path} are not written as a [features] table, so "
                          f"{name} cannot be set here; set it by hand")
    _write(path, edited)
