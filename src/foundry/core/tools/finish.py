"""finish: the only way a task reaches a terminal status.

Without a structured channel, "all validation claims must reference a real
command and exit code" degrades into parsing the model's prose -- exactly the
gentlemen's agreement the evidence chain exists to replace. Here a claim names a
command event by ordinal, and the runtime checks the journal before honouring a
``completed``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from foundry.core.conversation import ToolSchema
from foundry.core.errors import InvalidToolCall
from foundry.core.events import TerminalStatus
from foundry.core.json_schema import embedded, problems
from foundry.core.tools.base import Operation, ToolContext, ToolKind, ToolOutput

CLAIMABLE = ("completed", "partial", "blocked", "failed")


@dataclass(frozen=True, slots=True)
class ValidationClaim:
    claim_text: str
    command_event_id: int
    expected_exit_code: int = 0

    def as_payload(self) -> dict[str, Any]:
        return {
            "claim_text": self.claim_text,
            "command_event_id": self.command_event_id,
            "expected_exit_code": self.expected_exit_code,
        }


RESULT_NOTE = (
    " This run asks for a result: pass it as `result`, a value matching the "
    "schema given for it, whenever status is completed (and with any other "
    "status if you have one). It is the run's output, read by a program."
)


@dataclass(slots=True)
class Finish:
    name: str = "finish"
    kind: ToolKind = ToolKind.READ_ONLY  # produces no side effect of its own
    # exec --output-schema (D-051): the shape the run's result must have,
    # already checked by json_schema.check_schema; None asks for no result.
    result_schema: Any = None

    def schema(self) -> ToolSchema:
        schema = self._schema()
        if self.result_schema is None:
            return schema
        result, definitions = embedded(self.result_schema, "#/properties/result")
        parameters = {**schema.parameters,
                      "properties": {**schema.parameters["properties"], "result": result},
                      **definitions}
        return ToolSchema(name=schema.name, description=schema.description + RESULT_NOTE,
                          parameters=parameters)

    def _schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=(
                "End the task and report its outcome. Every validation you claim "
                "must cite the event_id printed by the run_command result that "
                "proves it; claims that do not match the recorded exit code are "
                "rejected, and so are claims citing a command that ran before your "
                "last successful apply_patch -- describe a pre-fix reproduction in "
                "the summary, not as a claim. If you ran no validation, say so and "
                "pass an empty claims list -- that is a valid disclosure, an "
                "invented one is not. The summary is for the user, who never "
                "sees event ids: name the command, not its event_id. "
                "Example: {\"status\": \"completed\", \"summary\": \"fixed the "
                "failing test\", \"claims\": [{\"claim_text\": \"pytest passes\", "
                "\"command_event_id\": 12, \"expected_exit_code\": 0}]}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": list(CLAIMABLE)},
                    "summary": {"type": "string"},
                    "claims": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "claim_text": {"type": "string"},
                                "command_event_id": {"type": "integer"},
                                "expected_exit_code": {"type": "integer"},
                            },
                            "required": ["claim_text", "command_event_id"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["status", "summary"],
                "additionalProperties": False,
            },
        )

    def validate(self, args: dict[str, Any]) -> Operation:
        known = {"status", "summary", "claims"} | ({"result"} if self.result_schema is not None
                                                   else set())
        extra = set(args) - known
        if extra:
            raise InvalidToolCall(f"unknown argument(s): {', '.join(sorted(extra))}")

        status = args.get("status")
        if status not in CLAIMABLE:
            raise InvalidToolCall(f"status must be one of {', '.join(CLAIMABLE)}")
        summary = args.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise InvalidToolCall("summary must be a non-empty string")

        raw_claims = args.get("claims", [])
        if not isinstance(raw_claims, list):
            raise InvalidToolCall("claims must be an array")
        claims: list[ValidationClaim] = []
        for item in raw_claims:
            if not isinstance(item, dict):
                raise InvalidToolCall("each claim must be an object")
            text = item.get("claim_text")
            event_id = item.get("command_event_id")
            if not isinstance(text, str) or not text.strip():
                raise InvalidToolCall("claim_text must be a non-empty string")
            if isinstance(event_id, bool) or not isinstance(event_id, int):
                raise InvalidToolCall("command_event_id must be an integer")
            exit_code = item.get("expected_exit_code", 0)
            if isinstance(exit_code, bool) or not isinstance(exit_code, int):
                raise InvalidToolCall("expected_exit_code must be an integer")
            claims.append(ValidationClaim(text, event_id, exit_code))

        checked: dict[str, Any] = {"status": status, "summary": summary, "claims": claims}
        if self.result_schema is not None:
            checked.update(self._result(args, status))
        return Operation(
            tool=self.name, kind=self.kind, args=checked,
            display=f"finish: {status}", target=status,
        )

    def _result(self, args: dict[str, Any], status: str) -> dict[str, Any]:
        """The run's result, checked against its schema (D-051); required for
        completed, since a script reads it."""
        if "result" not in args:
            if status == "completed":
                raise InvalidToolCall("this run asks for a result: pass `result`, matching "
                                      "the schema given for it, with status completed")
            return {}
        value = args["result"]
        try:
            json.dumps(value, allow_nan=False)
        except ValueError as exc:
            raise InvalidToolCall("result must be plain JSON (no NaN or Infinity)") from exc
        wrong = problems(value, self.result_schema)
        if wrong:
            raise InvalidToolCall("result does not match the schema given for it: "
                                  + "; ".join(wrong))
        return {"result": value}

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        # Verification lives in the runtime, which owns the journal; this tool
        # only carries the request there.
        metadata = {
            "status": op.args["status"],
            "summary": op.args["summary"],
            "claims": op.args["claims"],
        }
        if "result" in op.args:
            metadata["result"] = op.args["result"]
        return ToolOutput(content="(finalizing)", metadata=metadata)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: TerminalStatus
    reason: str
    rejected: tuple[str, ...] = ()


def verify_claims(claims: list[ValidationClaim], command_history: list[dict[str, Any]],
                  requested: str, last_mutation_ordinal: int = 0) -> VerificationResult:
    """Downgrade a ``completed`` whose claims the journal does not support.

    ``last_mutation_ordinal`` is the journal ordinal of the most recent change
    to the workspace. A claim must cite a command that ran *after* it: asking
    only "did this command exit 0" let a green run from before the edit vouch
    for the edit. The model runs the suite, then changes the code and never
    re-runs it, cites the earlier ordinal, and the exit code matches -- so a
    session that never verified anything reported ``completed``.
    """
    by_ordinal = {entry["event_ordinal"]: entry for entry in command_history}
    rejected: list[str] = []

    for claim in claims:
        entry = by_ordinal.get(claim.command_event_id)
        if entry is None:
            rejected.append(
                f"{claim.claim_text!r} cites event {claim.command_event_id}, "
                "which is not a recorded command"
            )
            continue
        actual = entry.get("exit_code")
        if actual != claim.expected_exit_code:
            rejected.append(
                f"{claim.claim_text!r} expects exit {claim.expected_exit_code} from "
                f"{entry['command']!r} but it exited {actual}"
            )
            continue
        if claim.command_event_id < last_mutation_ordinal:
            rejected.append(
                f"{claim.claim_text!r} cites {entry['command']!r} (event "
                f"{claim.command_event_id}), which ran before the last change to the "
                f"workspace (event {last_mutation_ordinal}); it cannot vouch for it"
            )

    if requested == TerminalStatus.COMPLETED.value and rejected:
        return VerificationResult(
            status=TerminalStatus.PARTIAL,
            reason="validation claims were not supported by recorded evidence",
            rejected=tuple(rejected),
        )
    return VerificationResult(status=TerminalStatus(requested), reason="reported by the agent",
                              rejected=tuple(rejected))
