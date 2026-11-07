"""get_goal and update_goal: the session's goal, for the model (D-127).

Offered once the session has a goal, as codex's: the model reads the goal and
marks it complete, blocked or paused; setting one, resuming one and its budget
are the user's, with /goal. Both are read-only: they change nothing in the
workspace, and the runtime that holds the goal journals each change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from foundry.core.conversation import ToolSchema
from foundry.core.errors import InvalidToolCall
from foundry.core.goal import MODEL_STATUSES, Goal, view
from foundry.core.tools.base import Operation, ToolContext, ToolKind, ToolOutput


class GoalKeeper(Protocol):
    def goal_now(self) -> Goal | None: ...

    def goal_update(self, status: str) -> str: ...


@dataclass(slots=True)
class GetGoal:
    keeper: GoalKeeper
    name: str = "get_goal"
    kind: ToolKind = ToolKind.READ_ONLY

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=("The session's goal: its objective and status, the tokens and "
                         "seconds spent on it, its token budget and what is left of it."),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        )

    def validate(self, args: dict[str, Any]) -> Operation:
        if args:
            raise InvalidToolCall(f"unknown argument(s): {', '.join(sorted(args))}")
        return Operation(tool=self.name, kind=self.kind, args={}, display="get_goal",
                         target="goal")

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        return ToolOutput(content=view(self.keeper.goal_now()))


@dataclass(slots=True)
class UpdateGoal:
    keeper: GoalKeeper
    name: str = "update_goal"
    kind: ToolKind = ToolKind.READ_ONLY

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=(
                "Mark the session's goal complete, blocked or paused. complete: only when "
                "every part of the objective is done and checked against current evidence, "
                "never because the budget is nearly spent or you are stopping. blocked: only "
                "when the same blocker has stopped you for three goal turns in a row and only "
                "the user can get past it. paused: only when the user asks. Resuming a goal "
                "and its budget are the user's, with /goal. When you mark a goal with a budget "
                "complete, tell the user the tokens it used."),
            parameters={
                "type": "object",
                "properties": {"status": {"type": "string", "enum": list(MODEL_STATUSES)}},
                "required": ["status"],
                "additionalProperties": False,
            },
        )

    def validate(self, args: dict[str, Any]) -> Operation:
        extra = set(args) - {"status"}
        if extra:
            raise InvalidToolCall(f"unknown argument(s): {', '.join(sorted(extra))}")
        status = args.get("status")
        if status not in MODEL_STATUSES:
            raise InvalidToolCall(f"status must be one of {', '.join(MODEL_STATUSES)}; "
                                  "resuming a goal is the user's")
        return Operation(tool=self.name, kind=self.kind, args={"status": status},
                         display=f"update_goal: {status}", target="goal")

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        return ToolOutput(content=self.keeper.goal_update(op.args["status"]))


def offer_goal_tools(registry, keeper: GoalKeeper) -> None:
    """Once a session has a goal, the model is offered its tools -- and keeps
    them: a tool that went away would leave its calls in the history
    unexplained."""
    if "update_goal" not in registry.tools:
        registry.register(GetGoal(keeper))
        registry.register(UpdateGoal(keeper))
