"""spawn_agent: a sub-agent for one well-scoped task, as codex's (D-141).

The sub-agent has a conversation of its own -- the model reads and searches
there, and only its report comes back -- and the session's rules, approvals
and hooks. Read-only by default: it changes nothing and runs no command. It
is offered only to the session's own agent, so a sub-agent starts none.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from foundry.core.conversation import ToolSchema
from foundry.core.errors import InvalidToolCall
from foundry.core.tools.base import Operation, ToolContext, ToolKind, ToolOutput

# Far more than a task needs; a whole file pasted in belongs in the workspace.
MAX_TASK_CHARS = 20_000

DESCRIPTION = (
    "Start a sub-agent on one well-scoped task. It works in a conversation of its own -- "
    "what it reads stays there -- and ends with a report, which is all you get back: "
    "say in the task everything it needs, and what the report should hold. Only when the "
    "user asks for a sub-agent, or for work to be split up or done apart; never on your "
    "own. read_only (the default, true): it reads and searches, and changes nothing and "
    "runs no command. read_only false: it may also patch files and run commands, each as "
    "this session's rules allow, asking the user what they would be asked; files it "
    "changed are to be read again before you patch them. Its commands cannot back your "
    "finish claims: run them again. It cannot start sub-agents of its own."
)


class AgentSpawner(Protocol):
    def spawn(self, task: str, *, read_only: bool, ctx: ToolContext) -> ToolOutput: ...


@dataclass(slots=True)
class SpawnAgent:
    spawner: AgentSpawner
    name: str = "spawn_agent"
    # A read-only one's kind: one that may change files is a mutator, put to
    # the rules as one (D-141).
    kind: ToolKind = ToolKind.READ_ONLY

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=DESCRIPTION,
            parameters={
                "type": "object",
                "properties": {
                    "task": {"type": "string",
                             "description": "What the sub-agent is to do, and report."},
                    "read_only": {"type": "boolean",
                                  "description": "true (the default): it changes nothing."},
                },
                "required": ["task"],
                "additionalProperties": False,
            },
        )

    def validate(self, args: dict[str, Any]) -> Operation:
        extra = set(args) - {"task", "read_only"}
        if extra:
            raise InvalidToolCall(f"unknown argument(s): {', '.join(sorted(extra))}")
        task = args.get("task")
        if not isinstance(task, str) or not task.strip():
            raise InvalidToolCall("task must be the sub-agent's task, as text")
        if len(task) > MAX_TASK_CHARS:
            raise InvalidToolCall(f"task is {len(task):,} characters; at most "
                                  f"{MAX_TASK_CHARS:,}: put the rest in a file it can read")
        read_only = args.get("read_only", True)
        if not isinstance(read_only, bool):
            raise InvalidToolCall("read_only must be true or false")
        first = task.strip().splitlines()[0]
        what = "a read-only sub-agent" if read_only else "a sub-agent that may change files"
        return Operation(tool=self.name,
                         kind=ToolKind.READ_ONLY if read_only else ToolKind.MUTATOR,
                         args={"task": task, "read_only": read_only},
                         display=f"spawn_agent: {what}: {first}",
                         # What a rule's pattern matches: a read-only one is
                         # allowed by a built-in rule (D-141).
                         target="read-only" if read_only else "may change files")

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        return self.spawner.spawn(op.args["task"], read_only=op.args["read_only"], ctx=ctx)
