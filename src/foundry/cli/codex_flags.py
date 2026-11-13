"""codex's command-line flags that Foundry does not take, answered with what
to use instead (D-097).

None of them means the same thing here -- Foundry has no sandbox, so codex's
approval and sandbox pairs have no exact counterpart -- so they are not taken
as aliases, which would quietly run with a different meaning. The hints are
read only when argparse is already refusing the command line: nothing here can
change what a valid command does.
"""

from __future__ import annotations

# Each spelling of a flag, by the name the hints know it as.
_NAMES = {
    "--full-auto": "--full-auto",
    "-a": "--ask-for-approval",
    "--ask-for-approval": "--ask-for-approval",
    "-s": "--sandbox",
    "--sandbox": "--sandbox",
    "--dangerously-bypass-approvals-and-sandbox": "--yolo",
    "--yolo": "--yolo",
    "--skip-git-repo-check": "--skip-git-repo-check",
    "--search": "--search",
    "--oss": "--oss",
    "--add-dir": "--add-dir",
}
# Those that take a value, which may come as the next word.
_TAKES_VALUE = frozenset({"--ask-for-approval", "--sandbox", "--add-dir"})
_SHORT = ("-a", "-s")


def _flag(token: str) -> tuple[str, str | None] | None:
    """The flag ``token`` spells and the value written into it, if it is one
    of codex's: ``--sandbox=read-only``, ``-sread-only`` and ``-s`` alike."""
    name, eq, value = token.partition("=")
    if name in _NAMES:
        return _NAMES[name], (value if eq else None)
    if token[:2] in _SHORT and len(token) > 2:
        return _NAMES[token[:2]], token[2:]
    return None


def _advice(name: str, value: str | None, command: str) -> str:
    unattended = command == "exec"
    commands = "commands still need an allow rule" + ("" if unattended else " or your approval")
    patches = (("exec already applies patches without asking (accept_edits, its default)"
                if unattended else "use --mode accept_edits: patches go in without asking")
               + f", but {commands} (Foundry has no sandbox to run them in)")
    if name == "--full-auto":
        return patches
    if name == "--ask-for-approval":
        if unattended:
            return ("exec asks nobody: patches go in (accept_edits, its default) and a command "
                    "no rule allows is refused; --mode dont_ask refuses patches too")
        if value == "never":
            return "use --mode dont_ask: what no rule allows is refused rather than asked"
        if value in ("untrusted", "on-failure", "on-request"):
            return "use --mode default: Foundry asks before anything no rule allows"
        return ("use --mode: default asks before anything no rule allows, accept_edits also "
                "patches without asking, plan changes nothing, dont_ask refuses rather than "
                "asks")
    if name == "--sandbox":
        if value == "read-only":
            return "use --mode plan: nothing is changed"
        if value == "workspace-write":
            return patches
        return ("Foundry has no sandbox (docs/threat-model.md says what that leaves): what is "
                "asked depends on --mode, and what runs unasked on allow rules in your "
                "config.toml")
    if name == "--yolo":
        return ("no mode here runs every command unasked: allow the ones you trust in your "
                'config.toml, as [[permissions]] tool = "run_command" pattern = "pytest*" '
                'decision = "allow"')
    if name == "--skip-git-repo-check":
        return ("Foundry works only inside a Git repository, whose baseline tells your changes "
                "from its own; git init makes one")
    if name == "--search":
        return ("Foundry has no web search of its own; an MCP server that searches can be "
                "added with foundry mcp add")
    if name == "--oss":
        return ('point [backend] in your config.toml at the local server: protocol = '
                '"openai_compat", base_url = "http://localhost:11434/v1" (Ollama\'s) and its '
                'model')
    # --add-dir
    return "a session works in one repository, -C DIR"


def hints(argv: list[str], unrecognized: list[str]) -> list[str]:
    """One line for each of codex's flags among the ``unrecognized`` words of
    ``argv``, saying what Foundry takes instead.

    Only words before a ``--`` are flags: after it they are the task's.
    """
    words = argv[:argv.index("--")] if "--" in argv else list(argv)
    refused = set(unrecognized)
    command = words[0] if words else ""
    lines: list[str] = []
    told: set[str] = set()
    for i, token in enumerate(words):
        found = _flag(token) if token in refused else None
        if found is None or found[0] in told:
            continue
        name, value = found
        told.add(name)
        given = token
        if (value is None and name in _TAKES_VALUE and i + 1 < len(words)
                and not words[i + 1].startswith("-")):
            value = words[i + 1]
            given = f"{token} {value}"
        lines.append(f"  {given} is codex's; {_advice(name, value, command)}")
    return lines
