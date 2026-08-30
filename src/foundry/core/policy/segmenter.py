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

_SEPARATORS = (";", "|", "&&", "||", "\n", "\r")

# A head we can reason about is a plain command name: letters, digits, and the
# punctuation that appears in real executable and cmdlet names. Anything else --
# a grouping paren, a script block, dot-sourcing, a variable -- is a construct
# whose effect is not readable from the token, so the segment is untrusted.
# Allowlisting the shape we can parse is the only version of this that does not
# need a new entry every time someone finds another spelling.
_PLAIN_HEAD = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*$")

# Shell wrappers that take another *command line* as an argument: `cmd /c git
# reset --hard` is a git command with a shell in front of it, and the breaker
# would otherwise only see "cmd".
#
# Interpreters (python, node, ...) are deliberately not here. `python -c
# "subprocess.run(['git','reset','--hard'])"` is equally destructive and no
# amount of parsing would catch it, while `python -m pytest` is the single most
# common legitimate command -- refusing to auto-allow it buys nothing and costs
# everything. That a command can do whatever the program does is a property of
# the trusted-host model, stated in the threat model, not something the
# segmenter pretends to solve.
_NESTED_SHELLS = frozenset({"cmd", "powershell", "pwsh", "bash", "sh", "wsl", "zsh", "dash"})


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


# Characters that can move where a *statement ends* or hide text from a reader:
# comments, block comments, redirection, escapes, expansion, the call operator.
# A command containing one is never auto-allowed, because whether Foundry's
# reading matches PowerShell's cannot be settled by inspection -- four review
# rounds each found a spelling where it did not.
#
# Parentheses and braces are deliberately absent: they group, but they cannot
# hide a statement boundary, and `python -c "print(1)"` is far too common to
# send to a prompt every time. A *grouped command* is still caught, by the
# plain-head check and by paranoid_segments.
_STRUCTURAL = frozenset("#<>$`^")

# A lone `&` is the call operator; `&&` is a separator this module splits on.
_LONE_AMPERSAND = re.compile(r"(?<!&)&(?!&)")


# Characters that separate statements, plus the grouping delimiters that can
# glue a statement onto a keyword (`if(1){git reset --hard}`).
_PARANOID_BREAKS = ";|\r\n(){}"

# Stripped from a token's edges so `(git`, `'git'`, `{git`, `&{git`, `. git` and
# `,--hard` all read as the thing PowerShell would pass along.
_PARANOID_EDGES = "'\"(){}[]&.,"


def paranoid_segments(text: str) -> list[tuple[str, ...]]:
    """A second, deliberately naive reading of the command.

    Honest framing: this is **not** "no lexer". It is a second lexer with its
    own blind spots, and round five demonstrated one (`git reset ,--hard`, where
    PowerShell's comma operator passes `--hard` to git while both readings
    emitted the literal token `,--hard`). What it buys is defence in depth: two
    independent readings, and the breaker denies on either, so a trick has to
    fool *both* to hide a command. That is weaker than a guarantee and stronger
    than one parser.

    It can only ever add denials: nothing consults it to permit anything, so a
    fragment it reads wrongly cannot authorize anything.
    """
    normalized = text.replace("&&", "\n").replace("||", "\n")
    for separator in _PARANOID_BREAKS:
        normalized = normalized.replace(separator, "\n")

    out: list[tuple[str, ...]] = []
    for line in normalized.split("\n"):
        # Every breaker entry anchors on argv[0], so `echo "git reset --hard"`
        # reads as head `echo` and is not a false denial.
        tokens = tuple(t.strip(_PARANOID_EDGES) for t in line.split()
                       if t.strip(_PARANOID_EDGES))
        if tokens:
            out.append(tokens)
    return out


def _outside_quotes(text: str) -> str:
    """The text with quoted spans blanked out.

    Used for the structural scan: a character inside a quoted argument is data,
    not syntax. An unterminated quote leaves the rest blanked, which is safe --
    the unbalanced-quote check refuses the command anyway.
    """
    out: list[str] = []
    quote: str | None = None
    for char in text:
        if quote:
            if char == quote:
                quote = None
            out.append(" ")
            continue
        if char in "'\"":
            quote = char
            out.append(" ")
            continue
        out.append(char)
    return "".join(out)


def strip_comments(text: str) -> str:
    """Remove PowerShell comments before anything else looks at the text.

    PowerShell strips ``# ...`` to end of line before lexing, so an apostrophe
    in a comment is inert there. To a quote-tracking splitter it *opens* a quote
    region that swallows every following newline, collapsing a whole script into
    one benign-looking statement -- verified to run `git reset --hard` past the
    breaker. Comments are removed first so both the splitter and the tokenizer
    see what PowerShell would.
    """
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            i += 1
            continue
        if char in "'\"":
            quote = char
            out.append(char)
            i += 1
            continue
        if char == "<" and text[i:i + 2] == "<#":
            end = text.find("#>", i + 2)
            i = len(text) if end == -1 else end + 2
            continue
        if char == "#" and (i == 0 or text[i - 1].isspace() or text[i - 1] in ";|&"):
            end = len(text)
            for terminator in ("\n", "\r"):
                found = text.find(terminator, i)
                if found != -1:
                    end = min(end, found)
            out.append("\n")  # the comment ended the statement
            i = end + 1 if end < len(text) else end
            continue
        out.append(char)
        i += 1
    return "".join(out)


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
        if char in (";", "|", "\n", "\r"):
            # A bare CR is a statement separator in PowerShell. Missing it
            # collapsed a whole chain into one segment whose head was the
            # harmless first command.
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
        if char.isspace() and char not in ("\r", "\n"):
            # CR and LF are separators, never token whitespace: treating them as
            # whitespace merged two statements into one argv.
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


# The subcommands the breaker reasons about, plus the common harmless ones.
# `effective_argv` scans for the first of these rather than counting past global
# options, so an option nobody enumerated cannot shift the subcommand out of the
# slot every breaker entry inspects.
GIT_SUBCOMMANDS = frozenset({
    # destructive or history-rewriting: the ones the breaker names
    "reset", "clean", "checkout", "restore", "switch", "stash", "rm", "mv",
    "commit", "push", "rebase", "merge", "filter-branch", "checkout-index",
    "read-tree", "worktree", "branch", "update-ref", "symbolic-ref", "reflog",
    "gc", "prune", "am", "apply", "cherry-pick", "revert", "tag", "notes",
    "sparse-checkout", "submodule", "replace", "update-index", "fetch", "pull",
    "remote", "clone", "init",
    # read-only, listed so a scan stops at them rather than running past
    "status", "diff", "log", "show", "rev-parse", "config", "add", "ls-files",
    "blame", "describe", "shortlog", "grep", "cat-file", "for-each-ref",
    "rev-list", "bisect", "help", "version", "whatchanged", "archive",
})


def effective_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    """Drop leading global options so the subcommand lands at index 1.

    Skips *any* leading option rather than an enumerated list. Enumerating was
    the bug: git has dozens of global flags, and the first one missing from the
    list (``--no-advice``, ``--icase-pathspecs``, ...) stopped the scan and left
    the whole breaker table looking at the wrong index.
    """
    if not argv:
        return argv
    head = canonicalize(argv[0])
    if head != "git":
        return (head,) + argv[1:]

    # Skip leading options *and* anything that is not a git subcommand. Deciding
    # which options take a separate value is the same enumeration trap in
    # reverse: `--attr-source HEAD reset --hard` left "HEAD" at index 1 and the
    # whole breaker table read the wrong slot. Scanning for the first token that
    # is actually a subcommand cannot be shifted by an option nobody listed.
    rest = list(argv[1:])
    while rest and (rest[0].startswith("-") or rest[0].lower() not in GIT_SUBCOMMANDS):
        del rest[:1]
    return (head, *rest)


def segment_command(command: str) -> SegmentedCommand:
    if not command or not command.strip():
        return SegmentedCommand(raw=command, untrusted_reason="empty command")

    untrusted_reason = ""

    # Comments change where statements end, so every later pass sees the text
    # with them removed.
    text = strip_comments(command)

    # A structural character means Foundry's reading of the command and
    # PowerShell's may differ, and every round of review found a spelling where
    # they did. Such a command can still be denied -- see paranoid_segments --
    # but it can never be auto-allowed.
    #
    # Scanned outside quotes only: `git log --grep='#123'` and
    # `Select-String -Pattern '# TODO'` contain no structure, and forcing them
    # to a prompt made a large slice of ordinary commands unallowable for no
    # security gain.
    #
    # Scanned on the RAW command, not the comment-stripped text: the stripper is
    # itself a lexer this module does not trust, and scanning its output would
    # let it hide the very characters this check exists to catch -- `echo a<# ;
    # git reset --hard #> b` strips to `echo a b`, which looks structure-free.
    visible = _outside_quotes(command)
    structural = sorted(set(visible) & _STRUCTURAL)
    if _LONE_AMPERSAND.search(visible):
        structural.append("&")
    if structural:
        untrusted_reason = (
            f"command contains shell syntax that changes its structure "
            f"({' '.join(structural)})"
        )

    if not untrusted_reason:
        for pattern, reason in _UNTRUSTED_PATTERNS:
            if pattern.search(text):
                untrusted_reason = reason
                break

    parts = _split_top_level(text)
    if any(p == "__UNBALANCED_QUOTE__" for p in parts):
        # The split itself is unreliable here, so there is nothing to hand the
        # breaker.
        return SegmentedCommand(raw=command, untrusted_reason="unbalanced quote")
    if not parts:
        return SegmentedCommand(raw=command,
                                untrusted_reason=untrusted_reason or "empty command")

    # Parsing continues even for an untrusted command. The segments are only
    # ever used to *deny*: an untrusted command can never be auto-allowed, so a
    # partial read can add a categorical denial but never permit anything.
    # Returning early instead meant `Remove-Item -Recurse $env:USERPROFILE`
    # reached an approvable prompt rather than the breaker.
    segments: list[Segment] = []

    for part in parts:
        argv = _tokenize(part)
        if not argv:
            untrusted_reason = untrusted_reason or "empty segment"
            continue

        canonical_head = canonicalize(argv[0])
        # The head must look like a plain command name. A grouping paren, a
        # script block, dot-sourcing, or a variable hides what actually runs,
        # and no breaker entry can match a head it cannot read.
        #
        # An unreadable segment makes the whole command untrusted -- it can
        # never be auto-allowed -- but the *readable* segments are still
        # returned. Discarding them let `git reset --hard; (foo)` fall through
        # to an approvable "cannot be parsed" prompt instead of the breaker's
        # categorical denial, turning a hard guarantee into a soft one.
        if not _PLAIN_HEAD.match(canonical_head):
            untrusted_reason = untrusted_reason or (
                f"command name is not a plain executable: {argv[0]!r}")
            continue
        if canonical_head in _NESTED_SHELLS and len(argv) > 1:
            untrusted_reason = untrusted_reason or (
                f"{canonical_head} runs a command given as an argument")
            continue

        canonical = " ".join((canonical_head,) + argv[1:])
        segments.append(Segment(text=part, argv=argv, canonical=canonical))

    if not segments and not untrusted_reason:
        return SegmentedCommand(raw=command, untrusted_reason="empty command")
    return SegmentedCommand(raw=command, segments=tuple(segments),
                            untrusted_reason=untrusted_reason)
