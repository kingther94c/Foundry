"""Slash commands: telling a command from a task, and custom prompts (D-039).

A line is a command only when it starts with ``/name`` -- a slash, then a
plain word, then whitespace or the end. "/usr/bin/python fails" is a task.

Custom prompts are Markdown files whose name is the command: ``/fix-test
test_add`` sends the text of ``fix-test.md`` with its placeholders filled.
They come from the user's own ``~/.foundry/prompts/`` and, only in a workspace
the user trusts, from the repository's ``.foundry/prompts/`` -- the same rule
as FOUNDRY.md, since a repository's prompt is text the user sends as their
own instruction. The model cannot add one: writing under ``.foundry/`` is
refused by the breaker table.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

PROMPT_MAX_BYTES = 24_000
_COMMAND = re.compile(r"/([A-Za-z][A-Za-z0-9_-]*)(?:\s+(.*))?\Z", re.DOTALL)
_NAME = re.compile(r"[a-z][a-z0-9_-]*\Z")
_PLACEHOLDER = re.compile(r"\$(\$|ARGUMENTS|[1-9])")


def parse_command(line: str) -> tuple[str, str] | None:
    """``(name, arguments)`` for a command line, None for a task."""
    match = _COMMAND.match(line.strip())
    if match is None:
        return None
    return match.group(1).lower(), (match.group(2) or "").strip()


def builtin_prompt(name: str) -> str:
    """A prompt Foundry ships, such as the one /review sends."""
    return resources.files("foundry.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")


@dataclass(frozen=True, slots=True)
class CustomPrompt:
    name: str
    body: str
    path: Path
    description: str = ""
    argument_hint: str = ""
    from_repository: bool = False


def _front_matter(text: str) -> tuple[dict[str, str], str]:
    """``---``-delimited ``key: value`` lines at the top, then the body."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            fields: dict[str, str] = {}
            for entry in lines[1:index]:
                key, sep, value = entry.partition(":")
                if sep:
                    fields[key.strip().lower()] = " ".join(value.split())
            return fields, "".join(lines[index + 1:])
    return {}, text


def _read_dir(folder: Path, *, from_repository: bool) -> dict[str, CustomPrompt]:
    found: dict[str, CustomPrompt] = {}
    if not folder.is_dir():
        return found
    for path in sorted(folder.glob("*.md")):
        name = path.stem.lower()
        if not _NAME.match(name) or not path.is_file():
            continue
        try:
            raw = path.read_bytes()[:PROMPT_MAX_BYTES]
        except OSError:
            continue
        fields, body = _front_matter(raw.decode("utf-8", errors="replace"))
        if not body.strip():
            continue
        found[name] = CustomPrompt(name=name, body=body.strip(), path=path,
                                   description=fields.get("description", "")[:120],
                                   argument_hint=fields.get("argument-hint", "")[:60],
                                   from_repository=from_repository)
    return found


def load_custom_prompts(home: Path, workspace: Path | None, *,
                        trusted: bool) -> dict[str, CustomPrompt]:
    """The user's prompts, over the repository's (read only when trusted)."""
    prompts: dict[str, CustomPrompt] = {}
    if workspace is not None and trusted:
        prompts.update(_read_dir(workspace / ".foundry" / "prompts", from_repository=True))
    prompts.update(_read_dir(home / "prompts", from_repository=False))
    return prompts


def expand(prompt: CustomPrompt, arguments: str) -> str:
    """Fill ``$ARGUMENTS`` (everything after the name), ``$1``..``$9`` (its
    words, quotes respected) and ``$$`` (a dollar sign). A prompt without
    placeholders gets the arguments appended, so nothing typed is lost."""
    try:
        words = shlex.split(arguments)
    except ValueError:
        words = arguments.split()

    used = False

    def fill(match: re.Match) -> str:
        nonlocal used
        token = match.group(1)
        if token == "$":
            return "$"
        used = True
        if token == "ARGUMENTS":
            return arguments
        index = int(token) - 1
        return words[index] if index < len(words) else ""

    text = _PLACEHOLDER.sub(fill, prompt.body)
    if arguments and not used:
        text += "\n\n" + arguments
    return text
