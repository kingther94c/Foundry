"""What a message carries besides its text: images -- a file's, or the
clipboard's (D-045) -- files named with ``@`` and skills asked for with
``$name``.
"""

from __future__ import annotations

import base64
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape

from foundry.core.errors import ImageRejected, ToolError
from foundry.core.images import MAX_IMAGES_PER_MESSAGE, from_bytes as image_from_bytes
from foundry.core.images import load as load_image
from foundry.core.policy.engine import Verdict
from foundry.core.skills import LOAD_SKILL
from foundry.core.tools.files import ReadFile
from foundry.core.tools.git import run_git
from foundry.core.winapi import CREATE_NO_WINDOW, IS_WINDOWS, find_program
from foundry.core.workspace import PathRejected

if TYPE_CHECKING:
    from foundry.cli.wiring import Wiring


def attach(wiring: "Wiring", console: Console, raw: str, then: str = "") -> bool:
    """Queue an image for the next message (D-045): `/image PATH`, and `-i`.
    The path is the user's own, so it need not be in the workspace."""
    raw = raw.strip().strip('"').strip("'")
    if not raw:
        console.print("[yellow]usage: /image PATH[/yellow]")
        return False
    if not _may_attach(wiring, console):
        return False
    path = Path(raw).expanduser()
    try:
        picture = load_image(path if path.is_absolute() else Path.cwd() / path, raw)
    except ImageRejected as exc:
        console.print(f"[red]cannot attach {escape(raw)}: {escape(str(exc))}[/red]")
        return False
    wiring.images.append(picture.block)
    console.print(f"[dim]attached {escape(raw)} ({picture.describe()}){then}[/dim]")
    return True


def _may_attach(wiring: "Wiring", console: Console) -> bool:
    if not wiring.config.backend.images:
        console.print("[yellow]this backend is set up without images "
                      "(backend.images = false)[/yellow]")
        return False
    if len(wiring.images) >= MAX_IMAGES_PER_MESSAGE:
        console.print(f"[yellow]at most {MAX_IMAGES_PER_MESSAGE} images go with one "
                      "message[/yellow]")
        return False
    return True


# The clipboard's image, as PNG in base64 on stdout (D-065); exit 3 when it
# holds none. -Sta: the clipboard is not reachable from a multithreaded
# apartment.
CLIPBOARD_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms, System.Drawing; "
    "$image = [System.Windows.Forms.Clipboard]::GetImage(); "
    "if ($null -eq $image) { exit 3 }; "
    "$stream = New-Object System.IO.MemoryStream; "
    "$image.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png); "
    "[Convert]::ToBase64String($stream.ToArray())")

CLIPBOARD_EMPTY = 3

CLIPBOARD_TIMEOUT_S = 15

CLIPBOARD_NAME = "the clipboard's image"


def clipboard_image() -> bytes | None:
    """The image on the clipboard as PNG bytes, or None when it holds none
    (D-065). Raises ImageRejected when the clipboard cannot be read."""
    if not IS_WINDOWS:
        raise ImageRejected("the clipboard is read on Windows only; /image PATH attaches "
                            "a file")
    # powershell.exe from PATH, not a copy in the current directory (D-049).
    shell = find_program("powershell.exe")
    if shell is None:
        raise ImageRejected("powershell.exe was not found on PATH")
    try:
        done = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Sta", "-Command", CLIPBOARD_SCRIPT],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="ascii",
            errors="replace", timeout=CLIPBOARD_TIMEOUT_S, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise ImageRejected(f"reading the clipboard took over {CLIPBOARD_TIMEOUT_S}s") from exc
    except OSError as exc:
        raise ImageRejected(f"the clipboard could not be read ({exc})") from exc
    if done.returncode == CLIPBOARD_EMPTY:
        return None
    if done.returncode != 0:
        raise ImageRejected(f"the clipboard could not be read (PowerShell exit "
                            f"{done.returncode})")
    try:
        return base64.b64decode(done.stdout.strip(), validate=True)
    except ValueError as exc:
        raise ImageRejected("what the clipboard gave back is not an image") from exc


def attach_clipboard(wiring: "Wiring", console: Console, then: str = "") -> bool:
    """``/image`` alone: the image on the clipboard -- a screenshot, as
    codex's Ctrl+V pastes one (D-065) -- goes with the next message, checked
    as a file's would be."""
    if not _may_attach(wiring, console):
        return False
    try:
        data = clipboard_image()
        if data is None:
            console.print("[yellow]the clipboard holds no image; /image PATH attaches a "
                          "file[/yellow]")
            return False
        picture = image_from_bytes(data, CLIPBOARD_NAME)
    except ImageRejected as exc:
        console.print(f"[red]cannot attach {CLIPBOARD_NAME}: {escape(str(exc))}[/red]")
        return False
    wiring.images.append(picture.block)
    console.print(f"[dim]attached {CLIPBOARD_NAME} ({picture.describe()}){then}[/dim]")
    return True


def take_images(wiring: "Wiring") -> tuple:
    images, wiring.images = tuple(wiring.images), []
    return images


# `@path`, or `@"a path"`, in a message the user typed attaches that file
# (D-058). Not after a word character, so an e-mail address is not one.
_MENTION = re.compile(r'(?<![\w@])@(?:"([^"\r\n]+)"|([^\s"\'`]+))')

# What ends a sentence rather than a path: "see @a.py, then".
_MENTION_TRAILING = ".,;:!?)]}"

# What looks meant as a file, so a miss is worth saying: a separator or an
# extension. `@property` in pasted code is not.
_LOOKS_LIKE_A_FILE = re.compile(r"[/\\]|\.\w{1,10}$")

MAX_MENTIONS = 10

MENTION_NOTE = "The user attached this file with @, as read_file shows it:\n{content}"

# How many of the files a name matches are said when it matches several.
MENTION_CHOICES = 5


def attach_mentions(wiring: "Wiring", console: Console, text: str) -> str:
    """``@path`` in a message the user typed: the file goes with the
    message, as read_file would show it -- numbered, the first window, cut to
    a tool result's size -- and counts as read (D-058). Only a file in the
    workspace, at most ten, each once; a policy rule that refuses reading it
    refuses this too, while one that would ask is answered by the user having
    typed it. What is attached is scrubbed; the message itself is unchanged.
    """
    attached: list[tuple[str, str]] = []
    seen: set[str] = set()
    files: list[str] | None = None
    for match in _MENTION.finditer(text):
        quoted, bare = match.group(1), match.group(2)
        name = quoted if quoted is not None else bare.rstrip(_MENTION_TRAILING)
        if not name:
            continue
        resolved = _file_at(wiring, name)
        if resolved is None:
            if quoted is None and not _LOOKS_LIKE_A_FILE.search(name):
                continue
            # Not a path here: a file the name ends, as codex's @ finds one
            # by its name (D-107).
            if files is None:
                files = workspace_files(wiring.workspace.root)
            named = files_named(name, files)
            if len(named) > 1:
                shown = ", ".join(named[:MENTION_CHOICES])
                more = ", ..." if len(named) > MENTION_CHOICES else ""
                console.print(f"[dim]@{escape(name)}: {len(named)} files are named so "
                              f"({escape(shown)}{more}); write more of the path; sent as "
                              "written[/dim]", soft_wrap=True)
                continue
            resolved = _file_at(wiring, named[0]) if named else None
            if resolved is None:
                console.print(f"[dim]@{escape(name)}: no such file in the workspace; "
                              "sent as written[/dim]", soft_wrap=True)
                continue
        if resolved.relative in seen:
            continue
        if len(seen) == MAX_MENTIONS:
            console.print(f"[yellow]at most {MAX_MENTIONS} files go with one message; "
                          f"@{escape(name)} does not[/yellow]", soft_wrap=True)
            continue
        seen.add(resolved.relative)
        tool = ReadFile()
        operation = tool.validate({"path": resolved.relative})
        decision, _ = wiring.runtime.policy.evaluate(operation)
        if decision.verdict is Verdict.DENY:
            console.print(f"[yellow]@{escape(name)}: not attached, the policy refuses "
                          f"reading it ({escape(decision.reason)})[/yellow]", soft_wrap=True)
            continue
        try:
            output = tool.execute(operation, wiring.runtime.tool_ctx)
        except ToolError as exc:
            console.print(f"[yellow]@{escape(name)}: not attached, {escape(str(exc))}[/yellow]",
                          soft_wrap=True)
            continue
        attached.append((resolved.relative, output.content))
    if not attached:
        return text
    console.print(f"[dim]attached {escape(', '.join(path for path, _ in attached))}[/dim]",
                  soft_wrap=True)
    scrub = wiring.runtime.context.redactor.scrub
    return "\n\n".join([text, *(MENTION_NOTE.format(content=scrub(content))
                                 for _, content in attached)])


# $name, where name is a skill's (D-113): as codex's mention of one.
_SKILL_MENTION = re.compile(r"(?<![\w$])\$([a-z0-9][a-z0-9-]{0,63})(?![\w-])")

SKILL_NOTE = "The user asked for the skill {name} with ${name}; {tool} gives:\n{content}"

MAX_SKILL_MENTIONS = 3


def attach_skills(wiring: "Wiring", console: Console, typed: str, text: str) -> str:
    """``$name`` in a message the user typed, where name is a skill's: its
    instructions go with the message, as load_skill gives them (D-113) -- at
    most three, each once. Any other ``$word`` is left as it was written."""
    named: list[str] = []
    for match in _SKILL_MENTION.finditer(typed):
        name = match.group(1)
        if name in wiring.skills.found and name not in named:
            named.append(name)
    if not named:
        return text
    if len(named) > MAX_SKILL_MENTIONS:
        console.print(f"[yellow]at most {MAX_SKILL_MENTIONS} skills go with one message; "
                      f"{escape(', '.join(named[MAX_SKILL_MENTIONS:]))} do not[/yellow]",
                      soft_wrap=True)
        named = named[:MAX_SKILL_MENTIONS]
    tool = wiring.runtime.registry.get(LOAD_SKILL)
    attached: list[tuple[str, str]] = []
    for name in named:
        operation = tool.validate({"name": name})
        decision, _ = wiring.runtime.policy.evaluate(operation)
        if decision.verdict is Verdict.DENY:
            console.print(f"[yellow]${escape(name)}: not attached, the policy refuses "
                          f"loading it ({escape(decision.reason)})[/yellow]", soft_wrap=True)
            continue
        try:
            output = tool.execute(operation, wiring.runtime.tool_ctx)
        except ToolError as exc:
            console.print(f"[yellow]${escape(name)}: not attached, {escape(str(exc))}"
                          "[/yellow]", soft_wrap=True)
            continue
        attached.append((name, output.content))
    if not attached:
        return text
    console.print(f"[dim]using the skill {escape(', '.join(n for n, _ in attached))}[/dim]",
                  soft_wrap=True)
    scrub = wiring.runtime.context.redactor.scrub
    return "\n\n".join([text, *(SKILL_NOTE.format(name=name, tool=LOAD_SKILL,
                                                  content=scrub(content))
                                for name, content in attached)])


def _file_at(wiring: "Wiring", name: str):
    """The workspace's file at ``name``, or None."""
    try:
        resolved = wiring.workspace.resolve(name)
        return resolved if resolved.absolute.is_file() else None
    except (PathRejected, OSError):
        return None


def workspace_files(root: Path) -> list[str]:
    """The files git knows in the workspace: tracked, and new ones it does
    not ignore -- so a file ignored, a secret's usual place, is never found
    by its name alone. Nothing when git cannot say."""
    try:
        code, out, _ = run_git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                               root)
    except ToolError:
        return []
    return [path for path in out.split("\0") if path] if code == 0 else []


def files_named(name: str, files: list[str]) -> list[str]:
    """The files whose path ends with ``name`` -- whole names, a folder or
    more of the path if it says them -- in any case, as Windows reads one."""
    wanted = name.replace("\\", "/").lower()
    return sorted(path for path in files
                  if path.lower() == wanted or path.lower().endswith("/" + wanted))
