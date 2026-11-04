"""update_plan: the model's plan for a task, kept where the user can see it (D-037).

Each call replaces the whole plan. The runtime holds it for the session, the
CLI draws it as a checklist, ``exec --json`` streams it as a ``plan_updated``
event, and the journal keeps it in the call's ``tool_result`` so a resumed
session shows it again.

The plan is the model's own account, so it is never evidence: ``finish``
lists the steps not marked completed and changes no status over them. Its text
is shown to a person, so each step is reduced to one line of plain text before
anything sees it -- a newline or a control character in a step could otherwise
draw extra lines, or a different terminal, where the checklist is drawn.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

from foundry.core.conversation import ToolSchema
from foundry.core.errors import InvalidToolCall
from foundry.core.events import PlanStep, StepStatus
from foundry.core.tools.base import Operation, ToolContext, ToolKind, ToolOutput

MAX_STEPS = 20
MAX_STEP_CHARS = 200
MAX_EXPLANATION_CHARS = 500
STATUSES = tuple(s.value for s in StepStatus)


def one_line(text: str) -> str:
    """Plain text on one line: whitespace runs (line breaks included) become one
    space, and control and format characters -- escape sequences, bidi
    overrides -- are dropped."""
    kept = "".join(c for c in text if unicodedata.category(c) not in ("Cc", "Cf")
                   or c.isspace())
    return " ".join(kept.split())


def plan_payload(steps: tuple[PlanStep, ...], explanation: str) -> dict[str, Any]:
    """The journal's form, in the call's tool_result record."""
    return {"explanation": explanation,
            "steps": [{"step": s.step, "status": s.status.value} for s in steps]}


def steps_from_payload(payload: Any) -> tuple[tuple[PlanStep, ...], str]:
    """A recorded plan, read back for a resume. Never raises: a journal is a
    file anyone could have edited, so a step that does not read as one is left
    out, and text is normalized again as if the model had just sent it."""
    if not isinstance(payload, dict):
        return (), ""
    raw = payload.get("steps")
    steps: list[PlanStep] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("status") not in STATUSES:
            continue
        text = item.get("step")
        if isinstance(text, str) and one_line(text):
            steps.append(PlanStep(one_line(text)[:MAX_STEP_CHARS], StepStatus(item["status"])))
    explanation = payload.get("explanation")
    explanation = one_line(explanation)[:MAX_EXPLANATION_CHARS] if isinstance(explanation, str) else ""
    return tuple(steps[:MAX_STEPS]), explanation


def describe(steps: tuple[PlanStep, ...]) -> str:
    """Counts only, so nothing the model wrote is repeated back to it."""
    if not steps:
        return "Plan cleared."
    done = sum(s.status is StepStatus.COMPLETED for s in steps)
    doing = sum(s.status is StepStatus.IN_PROGRESS for s in steps)
    return (f"Plan updated: {len(steps)} step(s), {done} completed, {doing} in progress, "
            f"{len(steps) - done - doing} pending.")


@dataclass(slots=True)
class UpdatePlan:
    name: str = "update_plan"
    kind: ToolKind = ToolKind.READ_ONLY  # it changes nothing in the workspace

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=(
                "Show the user your plan for a task with several steps, and your "
                "progress through it. Each call replaces the whole plan: at most "
                f"{MAX_STEPS} short steps, each pending, in_progress or completed, with "
                "at most one in_progress. Mark a step completed once it is done, in the "
                "same turn as the work rather than in a round of its own. Skip the plan "
                "for a task of one or two steps; an empty plan clears it. The plan is "
                "not evidence: finish lists the steps you did not mark completed. "
                "Example: {\"explanation\": \"the failing test points at add()\", "
                "\"plan\": [{\"step\": \"Reproduce the failure\", \"status\": "
                "\"completed\"}, {\"step\": \"Fix add() in src/calc.py\", \"status\": "
                "\"in_progress\"}, {\"step\": \"Run the test suite\", \"status\": "
                "\"pending\"}]}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "explanation": {"type": "string"},
                    "plan": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step": {"type": "string"},
                                "status": {"type": "string", "enum": list(STATUSES)},
                            },
                            "required": ["step", "status"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["plan"],
                "additionalProperties": False,
            },
        )

    def validate(self, args: dict[str, Any]) -> Operation:
        extra = set(args) - {"explanation", "plan"}
        if extra:
            raise InvalidToolCall(f"unknown argument(s): {', '.join(sorted(extra))}")

        raw = args.get("plan")
        if not isinstance(raw, list):
            raise InvalidToolCall("plan must be an array of steps (an empty one clears the plan)")
        if len(raw) > MAX_STEPS:
            raise InvalidToolCall(f"plan has {len(raw)} steps; keep it to {MAX_STEPS} or fewer "
                                  "by merging small ones")

        steps: list[PlanStep] = []
        for number, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                raise InvalidToolCall(f"step {number} must be an object with step and status")
            unknown = set(item) - {"step", "status"}
            if unknown:
                raise InvalidToolCall(
                    f"step {number} has unknown field(s): {', '.join(sorted(unknown))}")
            text = item.get("step")
            if not isinstance(text, str) or not one_line(text):
                raise InvalidToolCall(f"step {number} needs a non-empty step text")
            text = one_line(text)
            if len(text) > MAX_STEP_CHARS:
                raise InvalidToolCall(f"step {number} is {len(text)} characters; keep each "
                                      f"step to one line of at most {MAX_STEP_CHARS}")
            status = item.get("status")
            if status not in STATUSES:
                raise InvalidToolCall(
                    f"step {number} status must be one of {', '.join(STATUSES)}")
            steps.append(PlanStep(text, StepStatus(status)))

        doing = [n for n, s in enumerate(steps, start=1) if s.status is StepStatus.IN_PROGRESS]
        if len(doing) > 1:
            raise InvalidToolCall(
                f"steps {', '.join(map(str, doing))} are all in_progress; at most one step "
                "is in progress at a time")

        explanation = args.get("explanation", "")
        if not isinstance(explanation, str):
            raise InvalidToolCall("explanation must be a string")
        explanation = one_line(explanation)
        if len(explanation) > MAX_EXPLANATION_CHARS:
            raise InvalidToolCall(f"explanation is {len(explanation)} characters; keep it to "
                                  f"{MAX_EXPLANATION_CHARS}")

        plan = tuple(steps)
        done = sum(s.status is StepStatus.COMPLETED for s in plan)
        display = (f"update_plan: {len(plan)} step(s), {done} completed" if plan
                   else "update_plan: clear the plan")
        return Operation(tool=self.name, kind=self.kind,
                         args={"steps": plan, "explanation": explanation},
                         display=display, target="plan")

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        # The runtime keeps the plan and tells the user; the tool only checks it.
        return ToolOutput(content=describe(op.args["steps"]))
