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

from foundry.core.errors import PromptError

PROMPT_MAX_BYTES = 24_000
_COMMAND = re.compile(r"/([A-Za-z][A-Za-z0-9_-]*)(?:\s+(.*))?\Z", re.DOTALL)
_NAME = re.compile(r"[a-z][a-z0-9_-]*\Z")
# $$, $ARGUMENTS, $1..$9, and a named one (D-071): a whole word in capitals,
# so $FILE and $TICKET_ID are, and $PSVersionTable and $env:PATH are not.
_PLACEHOLDER = re.compile(r"\$(\$|ARGUMENTS|[1-9]|[A-Z][A-Z0-9_]*\b)")


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


def named_placeholders(body: str) -> list[str]:
    """The named placeholders a prompt uses -- ``$FILE``, ``$TICKET_ID`` --
    in the order they first appear (D-071)."""
    names: list[str] = []
    for match in _PLACEHOLDER.finditer(body):
        token = match.group(1)
        if token not in ("$", "ARGUMENTS") and not token.isdigit() and token not in names:
            names.append(token)
    return names


def usage_hint(prompt: CustomPrompt) -> str:
    """What /help shows after the prompt's name: its own argument-hint, or
    else the named values it needs."""
    if prompt.argument_hint:
        return prompt.argument_hint
    return " ".join(f"{name}=..." for name in named_placeholders(prompt.body))


def expand(prompt: CustomPrompt, arguments: str) -> str:
    """Fill ``$ARGUMENTS`` (everything after the name), ``$1``..``$9`` (its
    words, quotes respected), ``$NAME`` (from a ``NAME=value`` word, D-071)
    and ``$$`` (a dollar sign). A prompt without placeholders gets the
    arguments appended, so nothing typed is lost.

    A word is a named value only when the prompt uses that name, so
    ``DEBUG=1`` stays an ordinary word for a prompt without ``$DEBUG``. A
    named placeholder with no value is a PromptError: the prompt is not sent
    with a hole in it."""
    try:
        words = shlex.split(arguments)
    except ValueError:
        words = arguments.split()

    names = named_placeholders(prompt.body)
    given: dict[str, str] = {}
    positional: list[str] = []
    for word in words:
        key, sep, value = word.partition("=")
        if sep and key in names:
            given[key] = value
        else:
            positional.append(word)
    missing = [name for name in names if name not in given]
    if missing:
        raise PromptError(
            f"/{prompt.name} needs " + " ".join(f"{name}=..." for name in missing)
            + "; a $NAME in a prompt is filled from NAME=value, and $$NAME is a "
            "literal $NAME")

    used = False

    def fill(match: re.Match) -> str:
        nonlocal used
        token = match.group(1)
        if token == "$":
            return "$"
        used = True
        if token == "ARGUMENTS":
            return arguments
        if not token.isdigit():
            return given[token]
        index = int(token) - 1
        return positional[index] if index < len(positional) else ""

    text = _PLACEHOLDER.sub(fill, prompt.body)
    if arguments and not used:
        text += "\n\n" + arguments
    return text
