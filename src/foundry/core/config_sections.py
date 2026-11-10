"""The configuration's smaller sections, each read and checked on its own:
the shell environment commands get, the terminal title, the project docs'
names, memories, skills, the notify program and permission rules.
"""

from __future__ import annotations

import dataclasses
import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from foundry.core.config_mcp import _strings
from foundry.core.errors import ConfigError
from foundry.core.winapi import looks_like_credential
from foundry.core.policy.engine import Layer, Rule, Verdict

if TYPE_CHECKING:
    from foundry.core.config import Config


INHERIT = ("core", "all", "none")

_SHELL_KEYS = frozenset({"inherit", "include", "exclude", "include_only", "set"})


@dataclass(frozen=True, slots=True)
class ShellEnvironmentPolicy:
    """Which variables a command gets (D-083), as codex's
    [shell_environment_policy]: run_command's and the user's !COMMAND.

    ``inherit`` is where it starts: ``core``, the variables a build needs
    (the default, D-049's list); ``all``; or ``none``. ``include`` adds
    variables by name or pattern, ``exclude`` takes them out, ``set`` gives
    plain values, and ``include_only``, if given, keeps only what it names.
    A credential-shaped name is never passed by inheritance or a pattern:
    only when ``include`` names it exactly, and its value is then scrubbed
    from whatever the command prints.
    """

    inherit: str = "core"
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    include_only: tuple[str, ...] | None = None
    set: tuple[tuple[str, str], ...] = ()


def _names(value: Any, what: str) -> tuple[str, ...]:
    names = _strings(value, what)
    if not all(name.strip() and "=" not in name and "\0" not in name for name in names):
        raise ConfigError(f"{what} must name variables, or patterns of them such as UV_*")
    return names


def _parse_shell_environment(raw: Any, current: ShellEnvironmentPolicy,
                             where: str = "shell_environment_policy") -> ShellEnvironmentPolicy:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a table")
    if "ignore_default_excludes" in raw:
        # codex's way to let a secret through every command; here a secret is
        # passed only by its own name.
        raise ConfigError(f"{where}.ignore_default_excludes is not offered: a variable "
                          "whose name looks like a credential is passed only when include "
                          "names it exactly")
    unknown = sorted(set(raw) - _SHELL_KEYS)
    if unknown:
        raise ConfigError(f"{where}: no setting {unknown[0]!r} "
                          f"(known: {', '.join(sorted(_SHELL_KEYS))})")
    changes: dict[str, Any] = {}
    if "inherit" in raw:
        if raw["inherit"] not in INHERIT:
            raise ConfigError(f"{where}.inherit must be one of {', '.join(INHERIT)}")
        changes["inherit"] = raw["inherit"]
    for key in ("include", "exclude", "include_only"):
        if key in raw:
            changes[key] = _names(raw[key], f"{where}.{key}")
    if "set" in raw:
        values = raw["set"]
        if not isinstance(values, dict) or not all(
                isinstance(k, str) and k.strip() and "=" not in k and isinstance(v, str)
                for k, v in values.items()):
            raise ConfigError(f"{where}.set must be a table of strings")
        for name in values:
            if looks_like_credential(name):
                raise ConfigError(f"{name} looks like a secret, and {where}.set writes its "
                                  "value into config.toml; keep it in your environment and "
                                  f"name it in {where}.include")
        changes["set"] = tuple(sorted(values.items()))
    return dataclasses.replace(current, **changes)


# The terminal title's items (D-139), by codex's tui.terminal_title ids, and
# what each shows; then the other ids codex reads for them.
TERMINAL_TITLE_ITEMS = {
    "app-name": "Foundry",
    "project-name": "the workspace folder's name",
    "current-dir": "the workspace folder",
    "activity": "Working while a task runs, [ ! ] Action Required while it waits on you",
    "run-state": "Ready, Working, or Waiting for your answer",
    "thread-name": "the session's name, from /rename (left out while it has none)",
    "thread-id": "the session's id",
    "model": "the model",
}

_TITLE_ALIASES = {"project": "project-name", "spinner": "activity", "status": "run-state",
                  "session-name": "thread-name", "session-id": "thread-id",
                  "model-name": "model"}

DEFAULT_TERMINAL_TITLE = ("activity", "thread-name", "project-name")


def title_items(raw: Any) -> tuple[str, ...]:
    """The terminal title's items (D-139), by codex's ids or the others it
    reads for them, each once; one that is not an item is refused."""
    if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
        raise ConfigError('terminal_title must be a list of items, as terminal_title = '
                          '["activity", "project-name"] ([] for no title)')
    items: list[str] = []
    for item in raw:
        name = _TITLE_ALIASES.get(item, item)
        if name not in TERMINAL_TITLE_ITEMS:
            raise ConfigError(f"terminal_title: no item {item!r}; the items are "
                              f"{', '.join(TERMINAL_TITLE_ITEMS)}")
        if name not in items:
            items.append(name)
    return tuple(items)


# A file's own name: no folder, no drive, no stream (``name:stream`` is an
# NTFS alternate data stream), nothing a directory could resolve elsewhere.
_DOC_NAME = re.compile(r"[^\\/:*?\"<>|\x00-\x1f]{1,255}\Z")


def _parse_doc_names(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list) or len(raw) > 16 or not all(
            isinstance(n, str) and _DOC_NAME.match(n) and n.strip(" .") for n in raw):
        raise ConfigError('project_doc_fallback_filenames must be a list of file names, '
                          'as ["CLAUDE.md"] (at most 16, no folders)')
    return tuple(dict.fromkeys(raw))


@dataclass(frozen=True, slots=True)
class MemorySettings:
    """codex's ``[memories]`` (D-145), by what each does here."""

    generate: bool = True              # generate_memories
    use: bool = True                   # use_memories
    disable_on_external_context: bool = False
    max_raw_memories: int = 256        # max_raw_memories_for_consolidation
    max_age_days: int = 30             # max_rollout_age_days
    max_sessions_per_pass: int = 16    # max_rollouts_per_startup
    min_idle_hours: int = 6            # min_rollout_idle_hours
    extract_model: str = ""
    consolidation_model: str = ""


# codex's [memories] names: its switches, its numbers with the range codex
# takes each one to (a number outside it is taken as its nearer end, as
# codex takes it), and its models.
_MEMORY_SWITCHES = {"generate_memories": "generate", "use_memories": "use",
                    "disable_on_external_context": "disable_on_external_context",
                    "no_memories_if_mcp_or_web_search": "disable_on_external_context"}

_MEMORY_NUMBERS = {"max_raw_memories_for_consolidation": ("max_raw_memories", 1, 4096),
                   "max_rollout_age_days": ("max_age_days", 0, 90),
                   "max_rollouts_per_startup": ("max_sessions_per_pass", 1, 128),
                   "min_rollout_idle_hours": ("min_idle_hours", 1, 48)}

_MEMORY_MODELS = ("extract_model", "consolidation_model")

_ONE_STORE = "Foundry keeps one store of memories, in a folder of each workspace's own"

_MEMORY_ELSEWHERE = {
    "version": _ONE_STORE,
    "dual_write": _ONE_STORE,
    "max_unused_days": ("Foundry does not track when a memory was last used: the newest "
                        "max_raw_memories_for_consolidation sessions' memories are kept"),
    "min_rate_limit_remaining_percent": "Foundry sees no rate-limit windows",
    "consolidation_service_tier": "Foundry asks for no service tier",
    "dedicated_tools": ("the model reads the notes with read_memories whenever memories are "
                        "used"),
}


def _parse_memories(raw: Any, current: MemorySettings) -> MemorySettings:
    if not isinstance(raw, dict):
        raise ConfigError("memories must be a table, as [memories]")
    changes: dict[str, Any] = {}
    named: dict[str, str] = {}
    for key, value in raw.items():
        if key in _MEMORY_SWITCHES:
            field_name = _MEMORY_SWITCHES[key]
            if field_name in named:
                raise ConfigError(f"memories.{named[field_name]} and memories.{key} are the "
                                  f"same setting; set {named[field_name]} alone")
            named[field_name] = key
            if not isinstance(value, bool):
                raise ConfigError(f"memories.{key} must be true or false, got {value!r}")
            changes[field_name] = value
        elif key in _MEMORY_NUMBERS:
            field_name, low, high = _MEMORY_NUMBERS[key]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(f"memories.{key} must be a whole number, got {value!r}")
            changes[field_name] = min(max(value, low), high)
        elif key in _MEMORY_MODELS:
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"memories.{key} must be a model's name, got {value!r}")
            changes[key] = value.strip()
        elif key in _MEMORY_ELSEWHERE:
            raise ConfigError(f"no setting memories.{key}; {_MEMORY_ELSEWHERE[key]}")
        else:
            close = difflib.get_close_matches(
                key, [*_MEMORY_SWITCHES, *_MEMORY_NUMBERS, *_MEMORY_MODELS], n=1)
            raise ConfigError(f"no setting memories.{key}"
                              + (f"; did you mean {close[0]!r}?" if close else ""))
    return dataclasses.replace(current, **changes)


# The most the model's list of skills may take (D-114), as codex's.
MAX_SKILL_LIST_TOKENS = 10_000

_SKILLS_KEYS = frozenset({"config", "max_context_tokens"})

_SWITCH_KEYS = frozenset({"path", "name", "enabled"})


def _parse_skills(raw: Any, config: "Config", *, managed: bool) -> None:
    """codex's ``[skills]``: ``[[skills.config]]`` entries, each a ``path``
    (a SKILL.md's, absolute) or a ``name`` and whether it is ``enabled``;
    and ``max_context_tokens``, the model's list's budget."""
    if not isinstance(raw, dict):
        raise ConfigError("skills must be a table, as [skills] or [[skills.config]]")
    for key in sorted(raw):
        if key not in _SKILLS_KEYS:
            hint = ("; Foundry bundles no skills" if key == "bundled"
                    else f" (it takes {', '.join(sorted(_SKILLS_KEYS))})")
            raise ConfigError(f"skills: no setting {key!r}{hint}")
    tokens = raw.get("max_context_tokens")
    if tokens is not None:
        if (isinstance(tokens, bool) or not isinstance(tokens, int)
                or not 0 < tokens <= MAX_SKILL_LIST_TOKENS):
            raise ConfigError("skills.max_context_tokens must be a whole number of tokens "
                              f"above 0 and at most {MAX_SKILL_LIST_TOKENS:,}")
        config.skills_max_context_tokens = tokens
    entries = raw.get("config", [])
    if not isinstance(entries, list):
        raise ConfigError("skills.config must be tables, as [[skills.config]]")
    switches = []
    for index, entry in enumerate(entries):
        where = f"skills.config {index + 1}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be a table")
        unknown = sorted(set(entry) - _SWITCH_KEYS)
        if unknown:
            raise ConfigError(f"{where}: no setting {unknown[0]!r} (it takes "
                              f"{', '.join(sorted(_SWITCH_KEYS))})")
        chosen = [key for key in ("path", "name") if key in entry]
        if len(chosen) != 1:
            raise ConfigError(f"{where} names a skill by path or by name, one of the two")
        kind = chosen[0]
        value = entry[kind]
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{where}.{kind} must be a string")
        if kind == "path" and not Path(value).is_absolute():
            raise ConfigError(f"{where}.path must be the absolute path of a SKILL.md")
        if not isinstance(entry.get("enabled"), bool):
            raise ConfigError(f"{where}.enabled must be true or false")
        switches.append((kind, value.strip(), entry["enabled"]))
    if managed:
        config.managed_skill_switches = (*config.managed_skill_switches, *switches)
    else:
        config.skill_switches = (*config.skill_switches, *switches)


def _parse_notify(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(a, str) and a.strip() and "\0" not in a
                                            for a in raw):
        raise ConfigError('notify must be a list of strings, the program and its arguments, '
                          'as notify = ["notify-send", "Foundry"] ([] for none)')
    return tuple(raw)


def _parse_rules(raw: Any, layer: Layer, *, allow_permitted: bool) -> list[Rule]:
    if not isinstance(raw, list):
        raise ConfigError("permissions must be a list of tables")
    rules: list[Rule] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigError("each permission entry must be a table")
        unknown = sorted(set(item) - _RULE_KEYS)
        if unknown:
            # A misspelt pattern left the rule matching everything: an allow
            # rule written `patern = "git *"` allowed every command (D-091).
            raise ConfigError(f"permission {index}: no setting {unknown[0]!r} (a rule takes "
                              f"{', '.join(sorted(_RULE_KEYS))})")
        try:
            verdict = Verdict(str(item.get("decision", "")).lower())
        except ValueError as exc:
            raise ConfigError(
                f"permission {index}: decision must be allow, ask, or deny"
            ) from exc
        if verdict is Verdict.ALLOW and not allow_permitted:
            # The self-privilege-escalation guard: a cloned repository cannot
            # grant itself permissions, only give up ones it does not need.
            raise ConfigError(
                "a repository config may only add 'ask' or 'deny' rules; "
                "'allow' must come from your user configuration"
            )
        rules.append(Rule(
            tool=str(item.get("tool", "*")),
            pattern=str(item.get("pattern", "*")),
            verdict=verdict,
            layer=layer,
            rule_id=f"{layer.value}.{index}",
            reason=str(item.get("reason", "")),
        ))
    return rules


_RULE_KEYS = frozenset({"tool", "pattern", "decision", "reason"})
