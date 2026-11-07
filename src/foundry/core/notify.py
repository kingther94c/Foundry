"""A program the user names, run when a turn ends (D-050).

Long work is left to run unattended; this is how its end reaches the user --
a desktop notification, a chat message, a sound. Like an MCP server it is a
program Foundry starts, so only the user's own configuration may name one,
never a repository's. It is started without a shell, with the environment a
command gets, and Foundry neither waits for it nor reads what it prints: a
notifier that hangs or fails does not hold the session up.

What it is told arrives as one JSON argument after its own, as codex passes
it, so a script written for codex's ``notify`` reads it the same way.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from foundry.core.winapi import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    IS_WINDOWS,
    child_environment,
    find_program,
)

EVENT_TYPE = "agent-turn-complete"
# An approval waiting on the user (D-079), as codex's notifications name it.
APPROVAL_TYPE = "approval-requested"
# The payload is a single command-line argument. Windows caps a command line
# at 32,767 characters and its quoting can double a JSON string, so each text
# is cut well below that.
TEXT_LIMIT = 2_000


def clip(text: str, limit: int = TEXT_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def payload(*, session_id: str, cwd: str, status: str | None, user_input: str,
            last_message: str, scrub=None) -> str:
    """What the program is told: the JSON it receives as its last argument.

    ``status`` is the task's terminal status, or None when the model answered
    without ending it (the REPL then waits for the next message). The texts
    are scrubbed of the secrets Foundry knows before they leave it.
    """
    clean = scrub or (lambda text: text)
    return json.dumps({
        "type": EVENT_TYPE,
        "session-id": session_id,
        "cwd": cwd,
        "status": status,
        "input-messages": [clip(clean(user_input))],
        "last-assistant-message": clip(clean(last_message)),
    }, ensure_ascii=False)


def approval_payload(*, session_id: str, cwd: str, tool: str, display: str, reason: str,
                     scrub=None) -> str:
    """What the program is told when a question waits for the user (D-079):
    the tool, what it would do as the approval shows it, and why it asks --
    scrubbed and cut like a turn's texts. A script written for codex's
    ``agent-turn-complete`` tells the two apart by ``type``."""
    clean = scrub or (lambda text: text)
    return json.dumps({
        "type": APPROVAL_TYPE,
        "session-id": session_id,
        "cwd": cwd,
        "tool": tool,
        "display": clip(clean(display)),
        "reason": clip(clean(reason)),
    }, ensure_ascii=False)


@dataclass(slots=True)
class Notifier:
    argv: tuple[str, ...]
    cwd: Path
    # The environment to start from; the process's own when None.
    environ: dict[str, str] | None = None
    _running: list = field(default_factory=list)

    def send(self, data: str) -> int:
        """Start the program with ``data`` as its last argument and return its
        pid without waiting for it. OSError when it cannot be started,
        ValueError when it is a batch file."""
        # Children that have finished are collected here, so none lingers.
        self._running = [p for p in self._running if p.poll() is None]
        env = child_environment(self.environ)
        # Found on PATH only: the current directory, usually the repository,
        # is not searched (D-049).
        program = find_program(self.argv[0], env)
        if program is None:
            raise FileNotFoundError(f"{self.argv[0]!r} was not found on PATH")
        if IS_WINDOWS and (Path(program).suffix.lower() in (".bat", ".cmd")
                           or Path(program).name.lower() == "cmd.exe"):
            # cmd.exe reads its arguments again -- `"`, `&` and `%` included --
            # and the payload carries the model's words: it would run them.
            raise ValueError(f"{self.argv[0]} is run by cmd.exe, which would read the "
                             "payload as commands; name an interpreter and the script, "
                             'as notify = ["python", "notify.py"]')
        process = subprocess.Popen(
            [program, *self.argv[1:], data], cwd=self.cwd, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            # Its own process group: the user's Ctrl+C is for Foundry.
            creationflags=(CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW) if IS_WINDOWS else 0,
            start_new_session=not IS_WINDOWS,
        )
        self._running.append(process)
        return process.pid
