"""PowerShell 5.1 command segmentation for policy matching.

Prefix-matching a command string is a known-bypassable pattern: `git status; rm -r ~`
matches an allowlisted prefix while doing something else entirely. So a command
is split at its operators and *every* segment must clear policy independently.

Two rules keep this honest rather than merely reassuring:

* anything this parser cannot decompose with confidence -- command substitution,
  redirection, the call operator, Invoke-Expression -- is marked untrusted, and
  an untrusted command can never be auto-allowed regardless of rules;
* aliases are canonicalized before matching, so a breaker entry for
  ``Remove-Item`` also catches ``rm``, ``ri``, ``del``, and ``erase``.

Windows PowerShell 5.1 (the preinstalled edition, D-018) has no ``&&``/``||``;
they are parse errors there. They are still treated as separators so that a
model emitting them is never mistaken for a single allowlisted command.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# One canonical name per family. Matching runs on the canonical form.
ALIASES: dict[str, str] = {
    "rm": "remove-item", "ri": "remove-item", "del": "remove-item",
    "erase": "remove-item", "rd": "remove-item", "rmdir": "remove-item",
    "ls": "get-childitem", "dir": "get-childitem", "gci": "get-childitem",
    "cat": "get-content", "type": "get-content", "gc": "get-content",
    "cp": "copy-item", "copy": "copy-item", "ci": "copy-item",
    "mv": "move-item", "move": "move-item", "mi": "move-item",
    "ps": "get-process", "gps": "get-process",
    "kill": "stop-process", "spps": "stop-process",
    "echo": "write-output", "write": "write-output",
    "pwd": "get-location", "gl": "get-location",
    "cd": "set-location", "chdir": "set-location", "sl": "set-location",
    "iex": "invoke-expression",
    "iwr": "invoke-webrequest", "curl": "invoke-webrequest", "wget": "invoke-webrequest",
    "sc": "set-content",
    "clear": "clear-host", "cls": "clear-host",
}

# Constructs whose effect cannot be read off the text.
_UNTRUSTED_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\$\("), "command substitution $(...)"),
    (re.compile(r"@\("), "array subexpression @(...)"),
    (re.compile(r"`"), "backtick escape"),
    # The call operator, with or without a space before its argument: `&'git'`
    # is as much an invocation as `& 'git'`, and only the spaced form used to
    # be caught.
    (re.compile(r"(?<!\S)&(?=\s|['\"]|$)"), "call operator &"),
    (re.compile(r"(?<![0-9<>])>{1,2}"), "output redirection"),
    (re.compile(r"(?<!\S)<(?!\S*>)"), "input redirection"),
    (re.compile(r"\binvoke-expression\b", re.IGNORECASE), "Invoke-Expression"),
    (re.compile(r"\biex\b", re.IGNORECASE), "Invoke-Expression alias"),
    (re.compile(r"\bstart-process\b", re.IGNORECASE), "Start-Process"),
    (re.compile(r"\bstart-job\b", re.IGNORECASE), "Start-Job"),
    (re.compile(r"-encodedcommand\b", re.IGNORECASE), "-EncodedCommand"),
    (re.compile(r"\$env:", re.IGNORECASE), "environment variable expansion"),
    (re.compile(r"\|\s*%"), "pipeline to ForEach-Object shorthand"),
)

_SEPARATORS = (";", "|", "&&", "||", "\n")


@dataclass(frozen=True, slots=True)
class Segment:
    text: str
    argv: tuple[str, ...]
    canonical: str  # canonical head + original arguments, lowercased head

    @property
    def head(self) -> str:
        return self.argv[0] if self.argv else ""


@dataclass(frozen=True, slots=True)
class SegmentedCommand:
    raw: str
    segments: tuple[Segment, ...] = ()
    untrusted_reason: str = ""

    @property
    def trusted(self) -> bool:
        """False means: never auto-allow, always ASK. The safety valve."""
        return not self.untrusted_reason and bool(self.segments)


def _split_top_level(text: str) -> list[str]:
    """Split on separators that are outside quotes."""
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            i += 1
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            i += 1
            continue
        two = text[i:i + 2]
        if two in ("&&", "||"):
            parts.append("".join(current))
            current = []
            i += 2
            continue
        if char in (";", "|", "\n"):
            parts.append("".join(current))
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    parts.append("".join(current))
    if quote:
        parts.append("__UNBALANCED_QUOTE__")
    return [p.strip() for p in parts if p.strip()]


def _tokenize(segment: str) -> tuple[str, ...]:
    """Split a segment into argv, honouring quotes. Not a full PowerShell parser
    -- anything it cannot read confidently has already been marked untrusted."""
    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in segment:
        if quote:
            if char == quote:
                quote = None
            else:
                current.append(char)
            continue
        if char in "'\"":
            quote = char
            continue
        if char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def canonicalize(head: str) -> str:
    """Map an alias to its canonical cmdlet name, stripping any path and .exe."""
    name = head.strip().lower().lstrip("&").strip("'\"")
    if "/" in name or "\\" in name:
        name = re.split(r"[\\/]", name)[-1]
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return ALIASES.get(name, name)


# Global options that may precede a subcommand. Without skipping these,
# `git -C . reset --hard` looks nothing like `git reset --hard`.
_GIT_GLOBAL_WITH_VALUE = frozenset({"-C", "-c", "--exec-path", "--git-dir", "--work-tree",
                                    "--namespace", "--config-env"})
_GIT_GLOBAL_FLAGS = frozenset({"--no-pager", "--paginate", "--bare", "--literal-pathspecs",
                               "--no-replace-objects", "--no-optional-locks", "-P"})


def effective_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    """Drop leading global options so the subcommand lands at index 1.

    Applied before any breaker comparison: a table that indexes ``argv[1]``
    is bypassed by a single ``-C .`` otherwise.
    """
    if not argv:
        return argv
    head = canonicalize(argv[0])
    if head != "git":
        return (head,) + argv[1:]

    rest = list(argv[1:])
    while rest:
        token = rest[0]
        if token in _GIT_GLOBAL_WITH_VALUE:
            del rest[:2]
            continue
        if token in _GIT_GLOBAL_FLAGS:
            del rest[:1]
            continue
        if any(token.startswith(f"{opt}=") for opt in _GIT_GLOBAL_WITH_VALUE):
            del rest[:1]
            continue
        break
    return (head, *rest)


def segment_command(command: str) -> SegmentedCommand:
    if not command or not command.strip():
        return SegmentedCommand(raw=command, untrusted_reason="empty command")

    for pattern, reason in _UNTRUSTED_PATTERNS:
        if pattern.search(command):
            return SegmentedCommand(raw=command, untrusted_reason=reason)

    parts = _split_top_level(command)
    if any(p == "__UNBALANCED_QUOTE__" for p in parts):
        return SegmentedCommand(raw=command, untrusted_reason="unbalanced quote")
    if not parts:
        return SegmentedCommand(raw=command, untrusted_reason="empty command")

    segments: list[Segment] = []
    for part in parts:
        argv = _tokenize(part)
        if not argv:
            return SegmentedCommand(raw=command, untrusted_reason="empty segment")
        canonical_head = canonicalize(argv[0])
        canonical = " ".join((canonical_head,) + argv[1:])
        segments.append(Segment(text=part, argv=argv, canonical=canonical))

    return SegmentedCommand(raw=command, segments=tuple(segments))
