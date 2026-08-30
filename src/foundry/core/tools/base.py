"""Tool contract: validate, then execute.

Validation runs before policy so a malformed call is rejected without ever
prompting the user about it. ``kind`` drives the default policy treatment:
read-only tools carry a built-in ALLOW rule, mutators fall through to the
interactive approval step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from foundry.core.conversation import ToolSchema


class ToolKind(str, Enum):
    READ_ONLY = "read_only"
    MUTATOR = "mutator"


@dataclass(frozen=True, slots=True)
class Operation:
    """A validated, normalized call. Policy, the approval display, and the
    executor all bind to this same object: what you approve is what runs."""

    tool: str
    kind: ToolKind
    args: dict[str, Any]
    display: str          # exactly what the user is shown
    target: str           # policy match key: path for file tools, command text
    digest: str = ""      # stable identity for approval binding


@dataclass(frozen=True, slots=True)
class ToolOutput:
    """Result of an execution.

    ``content`` is what the model sees, already truncated. The full output, if
    it was larger, lives in an artifact the model can page through.
    """

    content: str
    is_error: bool = False
    artifact_id: str = ""
    truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class Tool(Protocol):
    name: str
    kind: ToolKind

    def schema(self) -> ToolSchema: ...

    def validate(self, args: dict[str, Any]) -> Operation:
        """Raise ``InvalidToolCall`` for anything malformed or out of bounds."""
        ...

    def execute(self, op: Operation, ctx: "ToolContext") -> ToolOutput: ...


@dataclass(slots=True)
class ToolContext:
    """What tools may reach. Deliberately narrow: no credentials, no session
    writer, no policy engine -- those stay above the tool layer."""

    workspace: Any                     # foundry.core.workspace.Workspace
    artifacts: Any                     # foundry.core.session.ArtifactStore
    read_tracker: "ReadTracker"
    emit: Any = None                   # callable(Event) | None
    max_output_bytes: int = 32_000
    env_policy: Any = None
    # Polled by long-running tools so a cancel reaches a running child rather
    # than only being noticed between calls.
    cancelled: Any = None              # callable() -> bool | None


@dataclass(slots=True)
class ReadTracker:
    """Read-before-edit state.

    An edit to a file the model has not read in this session is refused; an edit
    to a file that changed since that read must still match its anchor uniquely,
    which the patch tool checks. Claude Code enforces the same rule in the
    harness rather than trusting the model to remember.
    """

    _digests: dict[str, str] = field(default_factory=dict)

    def record(self, relative: str, digest: str) -> None:
        self._digests[relative] = digest

    def digest_at_read(self, relative: str) -> str | None:
        return self._digests.get(relative)

    def has_read(self, relative: str) -> bool:
        return relative in self._digests

    def forget(self, relative: str) -> None:
        self._digests.pop(relative, None)


def truncate_middle(text: str, limit: int) -> tuple[str, bool]:
    """Keep the head and tail, drop the middle, and say how much was dropped.

    Head-and-tail beats a plain head cut: a failing test's summary is usually at
    the end of the output, and the command that produced it at the start.
    """
    if len(text) <= limit:
        return text, False
    keep = limit // 2 - 40
    head, tail = text[:keep], text[-keep:]
    dropped = len(text) - len(head) - len(tail)
    return f"{head}\n\n[... {dropped} characters elided ...]\n\n{tail}", True
