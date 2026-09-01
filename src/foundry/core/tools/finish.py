"""finish: the only way a task reaches a terminal status.

Without a structured channel, "all validation claims must reference a real
command and exit code" degrades into parsing the model's prose -- exactly the
gentlemen's agreement the evidence chain exists to replace. Here a claim names a
command event by ordinal, and the runtime checks the journal before honouring a
``completed``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from foundry.core.conversation import ToolSchema
from foundry.core.errors import InvalidToolCall
from foundry.core.events import TerminalStatus
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


@dataclass(slots=True)
class Finish:
    name: str = "finish"
    kind: ToolKind = ToolKind.READ_ONLY  # produces no side effect of its own

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=(
                "End the task and report its outcome. Every validation you claim "
                "must cite the event_id printed by the run_command result that "
                "proves it; claims that do not match the recorded exit code are "
                "rejected. If you ran no validation, say so and pass an empty "
                "claims list -- that is a valid disclosure, an invented one is not. "
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
        extra = set(args) - {"status", "summary", "claims"}
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

        return Operation(
            tool=self.name, kind=self.kind,
            args={"status": status, "summary": summary, "claims": claims},
            display=f"finish: {status}", target=status,
        )

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        # Verification lives in the runtime, which owns the journal; this tool
        # only carries the request there.
        return ToolOutput(content="(finalizing)", metadata={
            "status": op.args["status"],
            "summary": op.args["summary"],
            "claims": op.args["claims"],
        })


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
