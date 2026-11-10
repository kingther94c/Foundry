"""Agent skills (D-113), as codex's: a folder holding ``SKILL.md`` -- a
``name`` and a ``description`` in its front matter, then instructions for one
kind of task -- and whatever else the instructions use: references, scripts,
templates.

The model is told each skill's name and description at the start, within a
small share of its window, and loads one with ``load_skill`` when a task
matches it, as codex loads a skill's instructions only when it uses one; the
same tool reads the skill's other files. A message that names a skill as
``$name`` asks for it.

Where they come from: the user's own, under ``skills/`` in the Foundry folder,
and a repository's, under ``.agents/skills`` (codex's place for them) -- read
only where the user trusts the repository, as its instructions and prompts
are, and only where each folder is inside the repository. The user's own win
a name both have. Nothing in a skill grants anything: its scripts run through
``run_command`` and are asked about like any command.
"""

from __future__ import annotations

import hashlib
import itertools
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from foundry.core.conversation import ToolSchema
from foundry.core.errors import InvalidToolCall, ToolError
from foundry.core.prompts import repository_top
from foundry.core.tools.base import Operation, ToolContext, ToolKind, ToolOutput, truncate_middle

LOAD_SKILL = "load_skill"
SKILL_FILE = "SKILL.md"
REPOSITORY_SKILLS = Path(".agents") / "skills"
# The Agent Skills format's limits: a name of lowercase letters, digits and
# hyphens, at most 64; a description at most 1024 characters.
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
MAX_DESCRIPTION = 1024
# Descriptions in the list when every one whole does not fit.
SHORT_DESCRIPTION = 160
MAX_READ_BYTES = 1_000_000
MAX_LISTED_FILES = 200
# Entries looked at when a skill's files are listed: a folder of a skill is
# small, and one that is not is not walked to its end (D-157).
MAX_WALKED = 5_000
YOURS, REPOSITORY = "yours", "repository"

LIST_HEADER = (
    "# Skills\n"
    "A skill is a folder of instructions for one kind of task. When the task in hand "
    f"matches a skill's description, call {LOAD_SKILL} with its name before you start, "
    "and follow what it says; it lists the skill's other files, which "
    f"{LOAD_SKILL} reads too. A message that names a skill as $name asks for that skill.")


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    folder: Path        # resolved
    source: str         # YOURS or REPOSITORY

    @property
    def path(self) -> Path:
        return self.folder / SKILL_FILE


@dataclass(slots=True)
class Skills:
    """What was found: the skills by name, those a skill of the same name
    hides, and the folders that are not skills, with why."""

    found: dict[str, Skill] = field(default_factory=dict)
    hidden: list[Skill] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    # Turned off by the user's [[skills.config]] (D-114).
    disabled: list[Skill] = field(default_factory=list)
    # Those the model's list had no room for: loadable all the same.
    left_out: list[str] = field(default_factory=list)


def repository_skill_folders(workspace: Path) -> list[Path]:
    """``.agents/skills`` in the workspace and each folder above it up to the
    repository's top, the nearest first, as codex looks (D-114)."""
    top = repository_top(workspace)
    folders = [workspace]
    if workspace != top:
        folders.extend(p for p in workspace.parents if p.is_relative_to(top))
    return [folder / REPOSITORY_SKILLS for folder in folders]


def skill_roots(home: Path, workspace: Path | None, *, trusted: bool) -> list[tuple[Path, str]]:
    """Where skills are looked for, the user's first, then the repository's
    nearest the workspace."""
    roots = [(home / "skills", YOURS)]
    if workspace is not None and trusted:
        roots.extend((folder, REPOSITORY) for folder in repository_skill_folders(workspace))
    return roots


def switched_on(skill: Skill, switches) -> bool:
    """What the user's ``[[skills.config]]`` says of a skill: the last entry
    that matches it -- its SKILL.md's path, or its name -- or on."""
    on = True
    for kind, value, enabled in switches:
        if kind == "name" and value == skill.name:
            on = enabled
        elif kind == "path" and _same_file(Path(value), skill.path):
            on = enabled
    return on


def _same_file(given: Path, path: Path) -> bool:
    try:
        # A Windows path is the same whatever its case.
        return given.resolve() == path
    except OSError:
        return False


def load_skills(home: Path, workspace: Path | None, *, trusted: bool,
                switches=()) -> Skills:
    skills = Skills()
    # What a repository's skill must be in: the repository, wherever its
    # .agents leads -- not the folder it was found in, which may itself be a
    # link out of it (D-157).
    inside = _resolved(repository_top(workspace)) if workspace is not None else None
    for root, source in skill_roots(home, workspace, trusted=trusted):
        if not root.is_dir():
            continue
        try:
            folders = sorted(p for p in root.iterdir() if not p.name.startswith("."))
        except OSError:
            continue
        for folder in folders:
            if not (folder / SKILL_FILE).is_file():
                continue   # not a skill: a folder of something else
            try:
                skill = _read_skill(folder, inside, source)
            except _NotASkill as exc:
                skills.skipped.append((folder, str(exc)))
                continue
            if not switched_on(skill, switches):
                skills.disabled.append(skill)
            elif skill.name in skills.found:
                skills.hidden.append(skill)
            else:
                skills.found[skill.name] = skill
    return skills


class _NotASkill(Exception):
    pass


def _resolved(path: Path) -> Path | None:
    try:
        return path.resolve()
    except OSError:
        return None


def _read_skill(folder: Path, inside: Path | None, source: str) -> Skill:
    try:
        resolved = folder.resolve()
        if source == REPOSITORY and (inside is None or not resolved.is_relative_to(inside)):
            # A link out of the repository: what it reads is not the
            # repository's to offer.
            raise _NotASkill("it leads outside the repository")
        raw = (resolved / SKILL_FILE).read_bytes()[:MAX_READ_BYTES]
    except OSError as exc:
        raise _NotASkill(f"cannot be read ({exc.strerror or exc})") from exc
    fields = front_matter(raw.decode("utf-8", errors="replace"))[0]
    name, description = fields.get("name", ""), " ".join(fields.get("description", "").split())
    if not name:
        raise _NotASkill("its SKILL.md has no name")
    if not _NAME.match(name):
        raise _NotASkill(f"its name {name[:70]!r} is not lowercase letters, digits and "
                         "hyphens, at most 64")
    if not description:
        raise _NotASkill("its SKILL.md has no description")
    return Skill(name=name, description=description[:MAX_DESCRIPTION], folder=resolved,
                 source=source)


_BLOCK = (">", "|", ">-", "|-", ">+", "|+")


def front_matter(text: str) -> tuple[dict[str, str], str]:
    """The top-level ``key: value`` lines between ``---`` lines, then the
    body. A value may be quoted, or a block (``>`` or ``|``) on the indented
    lines after it; nested keys are passed over."""
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        return {}, text
    fields: dict[str, str] = {}
    index = 1
    while index < end:
        line = lines[index]
        index += 1
        if not line.strip() or line[0].isspace() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip()
        if value in _BLOCK:
            block = []
            while index < end and (not lines[index].strip() or lines[index][0].isspace()):
                block.append(lines[index].strip())
                index += 1
            value = " ".join(part for part in block if part)
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key.strip().lower()] = value
    return fields, "\n".join(lines[end + 1:])


def skill_list(skills: Skills, budget: int) -> str:
    """The model's list: every skill's name and description in at most
    ``budget`` characters -- descriptions shortened first, as codex's are,
    then skills left out, which ``skills.left_out`` names."""
    entries = sorted(skills.found.values(), key=lambda s: s.name)
    if not entries:
        return ""
    for limit in (MAX_DESCRIPTION, SHORT_DESCRIPTION):
        lines = [f"- {s.name}: {_cut(s.description, limit)}" for s in entries]
        if len(LIST_HEADER) + sum(len(line) + 1 for line in lines) <= budget:
            skills.left_out = []
            return "\n".join([LIST_HEADER, *lines])
    kept: list[str] = []
    used = len(LIST_HEADER)
    for line in lines:
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    skills.left_out = [s.name for s in entries[len(kept):]]
    kept.append(f"- ({len(skills.left_out)} more, not listed for room: "
                f"{LOAD_SKILL} loads any skill by its name)")
    return "\n".join([LIST_HEADER, *kept])


def list_budget(window_tokens: int) -> int:
    """codex's share: 2% of the window, as characters at about four a
    token; at least 2,000 and at most 40,000."""
    return max(2_000, min(40_000, window_tokens * 8 // 100))


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


@dataclass(slots=True)
class SkillTool:
    """``load_skill``: a skill's instructions, or one of its other files."""

    skills: Skills
    name: str = LOAD_SKILL

    @property
    def kind(self) -> ToolKind:
        return ToolKind.READ_ONLY

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description="Load a skill's instructions (its SKILL.md) and the list of its "
                        "other files; or, with file, one of those files.",
            parameters={"type": "object", "properties": {
                "name": {"type": "string", "description": "the skill's name"},
                "file": {"type": "string",
                         "description": "one of the skill's other files, by its path as "
                                        "the instructions list it"},
            }, "required": ["name"]})

    def validate(self, args: dict[str, Any]) -> Operation:
        if not isinstance(args, dict):
            raise InvalidToolCall(f"{self.name} takes an object of arguments")
        extra = sorted(set(args) - {"name", "file"})
        if extra:
            raise InvalidToolCall(f"{self.name} takes no {extra[0]!r}")
        name = args.get("name")
        skill = self.skills.found.get(name) if isinstance(name, str) else None
        if skill is None:
            known = sorted(self.skills.found)
            shown = ", ".join(known[:20]) + (", ..." if len(known) > 20 else "")
            raise InvalidToolCall(f"no skill is named {str(name)[:70]!r}; the skills are "
                                  f"{shown}")
        file = args.get("file")
        if file is not None:
            relative = _relative(file)
            args = {"name": name, "file": relative}
        digest_of = f"{self.name}\x00{name}\x00{args.get('file', '')}"
        digest = hashlib.sha256(digest_of.encode("utf-8")).hexdigest()[:16]
        what = args.get("file", SKILL_FILE)
        return Operation(tool=self.name, kind=self.kind, args=args,
                         display=f"skill {name}: {what}", target=f"{name} {what}",
                         digest=digest)

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        skill = self.skills.found[op.args["name"]]
        if "file" in op.args:
            text = self._file(skill, op.args["file"])
        else:
            body = front_matter(_text(skill.path, SKILL_FILE))[1].strip()
            files = _files(skill.folder)
            listed = "\n".join(f"  {path}" for path in files[:MAX_LISTED_FILES])
            if len(files) > MAX_LISTED_FILES:
                listed += f"\n  ... and {len(files) - MAX_LISTED_FILES} more"
            whose = "yours" if skill.source == YOURS else "the repository's"
            text = (f"Skill {skill.name} ({whose}, in {skill.folder})\n\n{body}\n\n"
                    + (f"Its other files, for {self.name} with file:\n{listed}"
                       if files else "It has no other files."))
        body, truncated = truncate_middle(text, ctx.max_output_bytes)
        artifact_id = ""
        if truncated and ctx.artifacts is not None:
            artifact_id = ctx.artifacts.put_text(text).artifact_id
            body += f"\n\n[full output saved; read_artifact artifact_id=\"{artifact_id}\"]"
        return ToolOutput(content=body, truncated=truncated, artifact_id=artifact_id,
                          metadata={"skill": skill.name, "source": skill.source})

    def _file(self, skill: Skill, relative: str) -> str:
        path = (skill.folder / relative).resolve()
        if not path.is_relative_to(skill.folder):
            raise ToolError(f"{relative} is not in the skill's folder")
        if not path.is_file():
            raise ToolError(f"the skill {skill.name} has no file {relative}")
        return _text(path, relative)


def _relative(file: Any) -> str:
    """A path inside a skill's folder, as ``references/api.md``."""
    if not isinstance(file, str) or not file.strip():
        raise InvalidToolCall("file is a path inside the skill's folder, as its "
                              "instructions list it")
    posix = PurePosixPath(file.strip().replace("\\", "/"))
    if (posix.is_absolute() or ":" in posix.as_posix() or ".." in posix.parts
            or posix.as_posix() in (".", "")):
        raise InvalidToolCall("file is a path inside the skill's folder, as its "
                              "instructions list it")
    return posix.as_posix()


def _text(path: Path, shown: str) -> str:
    try:
        raw = path.read_bytes()[:MAX_READ_BYTES]
    except OSError as exc:
        raise ToolError(f"{shown} cannot be read ({exc.strerror or exc})") from exc
    if b"\x00" in raw[:8192]:
        raise ToolError(f"{shown} is not text; run what uses it instead")
    return raw.decode("utf-8", errors="replace")


def _files(folder: Path) -> list[str]:
    """The skill's files but its SKILL.md, by their paths in it; hidden ones
    left out, and no link or junction followed: what one leads to is not the
    skill's, and load_skill would not read it (D-157)."""
    found: list[str] = []
    pending = [(folder, "")]
    walked = 0
    while pending and len(found) <= MAX_LISTED_FILES * 2 and walked < MAX_WALKED:
        directory, prefix = pending.pop(0)
        try:
            with os.scandir(directory) as listing:
                entries = sorted(itertools.islice(listing, MAX_WALKED - walked),
                                 key=lambda entry: entry.name.casefold())
        except OSError:
            continue
        for entry in entries:
            walked += 1
            if len(found) > MAX_LISTED_FILES * 2:
                break
            if entry.name.startswith("."):
                continue
            relative = prefix + entry.name
            try:
                if entry.is_symlink() or entry.is_junction():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append((Path(entry.path), relative + "/"))
                elif entry.is_file(follow_symlinks=False) and relative != SKILL_FILE:
                    found.append(relative)
            except OSError:
                continue
    return sorted(found, key=lambda path: [part.casefold() for part in path.split("/")])


def register(skills: Skills, registry) -> bool:
    """``load_skill``, when there is a skill to load."""
    if not skills.found:
        return False
    registry.register(SkillTool(skills))
    return True
