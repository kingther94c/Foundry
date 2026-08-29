"""AgentRuntime: the only loop.

    while the model asks for tools:
        validate -> policy -> (approve) -> execute -> record -> resample

Everything a provider could be tempted to own -- policy, tools, budgets,
termination -- lives here, so a second backend can never fork the runtime's
behaviour by accident.

Approval is a request event answered by a later op. The runtime asks its
approval callback, which the CLI wires to a prompt and headless mode wires to
DENY; no tool ever blocks on input itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from foundry.core.backends.base import ModelBackend, TextDelta, TurnFinished, UsageUpdate
from foundry.core.context import ContextManager
from foundry.core.conversation import Message, Role, ToolUseBlock, TurnRequest
from foundry.core.errors import (
    ApprovalDeclined,
    AuthError,
    BudgetExceeded,
    Cancelled,
    FatalError,
    FoundryError,
    InvalidToolCall,
    PolicyDenied,
    ToolError,
    TransientError,
)
from foundry.core.events import (
    ApprovalChoice,
    ApprovalKind,
    ApprovalRequest,
    ErrorEvent,
    EventSink,
    MessageDelta,
    Notice,
    TerminalStatus,
    Termination,
    TokenCount,
    ToolBegin,
    ToolEnd,
    TurnComplete,
    TurnStarted,
    new_id,
)
from foundry.core.policy.engine import Decision, PolicyEngine, Verdict
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.base import Operation, ToolContext, ToolKind, ToolOutput
from foundry.core.tools.finish import ValidationClaim, verify_claims
from foundry.core.tools.registry import ToolRegistry

ApprovalCallback = Callable[[ApprovalRequest], ApprovalChoice]


@dataclass(slots=True)
class Budget:
    max_tool_rounds: int = 40
    max_tool_calls: int = 200
    max_tokens: int | None = None
    max_consecutive_failures: int = 4

    rounds: int = 0
    calls: int = 0

    def check(self, usage_total: int) -> str | None:
        if self.rounds > self.max_tool_rounds:
            return f"tool-call rounds exceeded ({self.max_tool_rounds})"
        if self.calls > self.max_tool_calls:
            return f"tool calls exceeded ({self.max_tool_calls})"
        if self.max_tokens is not None and usage_total > self.max_tokens:
            return f"token budget exceeded ({self.max_tokens})"
        return None


@dataclass(slots=True)
class FailureTracker:
    """Counts repeats by normalized operation and error class.

    Keyed on the pair, not the message: a model that reworded its way around a
    counter would loop forever, which is the failure mode this guards.
    """

    counts: dict[tuple[str, str], int] = field(default_factory=dict)

    def record(self, op: Operation, error: Exception) -> int:
        key = (f"{op.tool}\x00{op.target}", type(error).__name__)
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def clear(self, op: Operation) -> None:
        prefix = f"{op.tool}\x00{op.target}"
        for key in [k for k in self.counts if k[0] == prefix]:
            del self.counts[key]


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    status: TerminalStatus | None
    text: str
    summary: str = ""
    claims: tuple[ValidationClaim, ...] = ()


@dataclass(slots=True)
class AgentRuntime:
    backend: ModelBackend
    registry: ToolRegistry
    policy: PolicyEngine
    context: ContextManager
    tool_ctx: ToolContext
    session: SessionStore | None = None
    events: EventSink = field(default_factory=EventSink)
    approval: ApprovalCallback | None = None
    budget: Budget = field(default_factory=Budget)
    model: str = "gpt-5"
    audit: object | None = None

    git_baseline: object | None = None   # tools.git.GitBaseline
    credentials: object | None = None    # auth.CredentialSource, for refresh
    failures: FailureTracker = field(default_factory=FailureTracker)
    _cancelled: bool = False
    _finish: TurnOutcome | None = None
    _refreshed: bool = False

    # -- helpers ----------------------------------------------------------

    def cancel(self) -> None:
        self._cancelled = True

    def _journal(self, event_type: str, payload: dict) -> int:
        return self.session.append(event_type, payload) if self.session else 0

    def _emit(self, event) -> None:
        self.events.emit(event)

    # -- the loop ---------------------------------------------------------

    def run_turn(self, user_input: str) -> TurnOutcome:
        self.context.append_user(user_input)
        self._finish = None

        while True:
            if self._cancelled:
                return self._terminate(TerminalStatus.CANCELLED, "interrupted by user")

            turn_index = self.context.start_turn()
            self.budget.rounds += 1
            self._emit(TurnStarted(turn_index))

            reason = self.budget.check(self.context.session_usage.total)
            if reason:
                return self._terminate(TerminalStatus.PARTIAL, reason)

            if self.context.context_exhausted():
                return self._terminate(TerminalStatus.PARTIAL, "context window exhausted")

            try:
                turn = self._sample()
            except AuthError as exc:
                # A gateway token that expires mid-task should cost one refresh,
                # not the session. Exactly one retry: a credential that is
                # rejected twice is not going to work on the third try.
                if self._refresh_credentials():
                    self._emit(Notice("credentials refreshed; retrying", level="info"))
                    try:
                        turn = self._sample()
                    except AuthError as retry_exc:
                        self._emit(ErrorEvent(str(retry_exc), category=retry_exc.category,
                                              fatal=True))
                        return self._terminate(TerminalStatus.BLOCKED,
                                               f"authentication: {retry_exc}")
                else:
                    self._emit(ErrorEvent(str(exc), category=exc.category, fatal=True))
                    return self._terminate(TerminalStatus.BLOCKED, f"authentication: {exc}")
            except TransientError as exc:
                self._emit(ErrorEvent(str(exc), category=exc.category, fatal=True))
                return self._terminate(TerminalStatus.BLOCKED, f"provider unavailable: {exc}")
            except FatalError as exc:
                self._emit(ErrorEvent(str(exc), category=exc.category, fatal=True))
                return self._terminate(TerminalStatus.FAILED, str(exc))
            except Cancelled:
                return self._terminate(TerminalStatus.CANCELLED, "interrupted by user")

            self.context.append(turn.as_message())
            self.context.record_usage(turn.usage)
            self._emit(TokenCount(turn.usage, self.context.session_usage))
            self._journal(EventType.TOKEN_USAGE, {
                "input_tokens": turn.usage.input_tokens,
                "output_tokens": turn.usage.output_tokens,
            })

            if not turn.tool_calls:
                self._emit(TurnComplete(turn_index, turn.text))
                return TurnOutcome(status=None, text=turn.text)

            for call in turn.tool_calls:
                self.budget.calls += 1
                self._dispatch(call)
                if self._finish is not None:
                    return self._finalize(self._finish)
                if self._cancelled:
                    return self._terminate(TerminalStatus.CANCELLED, "interrupted by user")

    def _sample(self):
        request = TurnRequest(
            messages=self.context.project(),
            tools=self.registry.schemas(),
            model=self.model,
        )
        # The journal has to hold enough to rebuild the request, or "the
        # transcript is the source of truth" is only a slogan: replay, resume,
        # and any audit of what was actually sent all read this record. The
        # Authorization header is the one thing deliberately absent.
        self._journal(EventType.MODEL_REQUEST, {
            "model": self.model,
            "message_count": len(request.messages),
            "tools": [t.name for t in request.tools],
            "messages": [
                {
                    "role": message.role.value,
                    "blocks": [self._block_payload(b) for b in message.blocks],
                }
                for message in request.messages
            ],
        })

        text_parts: list[str] = []
        finished = None
        for event in self.backend.stream_turn(request):
            if isinstance(event, TextDelta):
                text_parts.append(event.text)
                self._emit(MessageDelta(event.text))
            elif isinstance(event, UsageUpdate):
                pass  # carried on the finished turn
            elif isinstance(event, TurnFinished):
                finished = event.turn

        if finished is None:
            raise FatalError("backend produced no finished turn")

        self._journal(EventType.MODEL_RESPONSE, {
            "text": finished.text,
            "tool_calls": [{"call_id": c.call_id, "name": c.name, "arguments": c.arguments}
                           for c in finished.tool_calls],
            "stop_reason": finished.stop_reason.value,
        })
        return finished

    # -- one tool call ----------------------------------------------------

    def _dispatch(self, call: ToolUseBlock) -> None:
        try:
            tool = self.registry.get(call.name)
            args = call.parse_arguments()
            op = tool.validate(args)
        except (InvalidToolCall, ValueError) as exc:
            # A malformed call never reaches policy or an executor; the model is
            # told why so the loop can continue.
            self._reply(call, f"invalid tool call: {exc}", is_error=True)
            self._journal(EventType.TOOL_CALL, {"name": call.name, "rejected": str(exc)})
            return

        self._journal(EventType.TOOL_CALL, {
            "call_id": call.call_id, "name": op.tool, "target": op.target,
            "display": op.display,
        })

        decision, op = self.policy.evaluate(op)
        self._journal(EventType.POLICY_DECISION, {
            "call_id": call.call_id, "verdict": decision.verdict.value,
            "reason": decision.reason, "rule_id": decision.rule_id, "step": decision.step,
            "policy_digest": decision.policy_digest,
            "operation_digest": decision.operation_digest,
        })

        if decision.verdict is Verdict.DENY:
            self._audit(op, f"DENY:{decision.rule_id}", "blocked")
            self._reply(call, f"blocked by policy: {decision.reason}", is_error=True)
            return

        if decision.verdict is Verdict.ASK:
            choice = self._ask(op, decision)
            self._journal(EventType.APPROVAL, {
                "call_id": call.call_id, "display": op.display, "choice": choice.value,
            })
            if choice is ApprovalChoice.ABORT:
                self._cancelled = True
                self._audit(op, "ABORT", "aborted")
                self._reply(call, "the user aborted the task", is_error=True)
                return
            if choice is ApprovalChoice.DENY:
                self._audit(op, "DENY:user", "declined")
                self._reply(call, "the user declined this operation", is_error=True)
                return
            if choice is ApprovalChoice.SESSION:
                self.policy.grant_for_session(op)
            self._audit(op, f"ASK:{choice.value}", "approved")
        else:
            self._audit(op, f"ALLOW:{decision.rule_id}", "auto")

        self._execute(call, op)

    def _execute(self, call: ToolUseBlock, op: Operation) -> None:
        tool = self.registry.get(op.tool)
        self._emit(ToolBegin(call.call_id, op.tool, op.display))
        try:
            output: ToolOutput = tool.execute(op, self.tool_ctx)
        except ToolError as exc:
            count = self.failures.record(op, exc)
            if count >= self.budget.max_consecutive_failures:
                self._emit(ErrorEvent(f"{op.tool} failed {count} times", category=exc.category))
                self._reply(call, f"{exc} (this exact operation has failed {count} times; "
                                  "try a different approach)", is_error=True)
                self._emit(ToolEnd(call.call_id, op.tool, False, str(exc)))
                return
            self._reply(call, str(exc), is_error=True)
            self._emit(ToolEnd(call.call_id, op.tool, False, str(exc)))
            return
        except FoundryError as exc:
            self._reply(call, f"{exc}", is_error=True)
            self._emit(ToolEnd(call.call_id, op.tool, False, str(exc)))
            return
        except Exception as exc:  # noqa: BLE001
            # A tool raising something outside the taxonomy -- an OSError from a
            # locked file, a PathRejected (which is a ValueError) -- must become
            # a tool result the model can act on. Letting it escape kills the
            # session with a traceback and loses the turn's evidence.
            detail = f"{type(exc).__name__}: {exc}"
            self.failures.record(op, exc)
            self._journal(EventType.ERROR, {"call_id": call.call_id, "tool": op.tool,
                                            "error": detail})
            self._emit(ErrorEvent(detail, category="tool_unexpected"))
            self._reply(call, f"the tool failed unexpectedly: {detail}", is_error=True)
            self._emit(ToolEnd(call.call_id, op.tool, False, detail))
            return

        self.failures.clear(op)

        if op.tool == "finish":
            self._finish = TurnOutcome(
                status=TerminalStatus(output.metadata["status"]), text="",
                summary=output.metadata["summary"],
                claims=tuple(output.metadata["claims"]),
            )
            self._emit(ToolEnd(call.call_id, op.tool, True, "finalizing"))
            return

        content = output.content
        if output.metadata.get("event_ordinal"):
            content += f"\n[event_id={output.metadata['event_ordinal']}]"

        self._journal(EventType.TOOL_RESULT, {
            "call_id": call.call_id, "tool": op.tool, "is_error": output.is_error,
            "artifact_id": output.artifact_id, "truncated": output.truncated,
        })
        self._reply(call, content, is_error=output.is_error)
        self._emit(ToolEnd(call.call_id, op.tool, not output.is_error,
                           content.splitlines()[0] if content else ""))

    @staticmethod
    def _block_payload(block) -> dict:
        from foundry.core.conversation import TextBlock, ToolResultBlock, ToolUseBlock as TU

        if isinstance(block, TextBlock):
            return {"kind": "text", "text": block.text}
        if isinstance(block, TU):
            return {"kind": "tool_use", "call_id": block.call_id, "name": block.name,
                    "arguments": block.arguments}
        if isinstance(block, ToolResultBlock):
            return {"kind": "tool_result", "call_id": block.call_id,
                    "content": block.content, "is_error": block.is_error}
        return {"kind": "unknown"}

    def _reply(self, call: ToolUseBlock, content: str, *, is_error: bool = False) -> None:
        self.context.append_tool_result(call.call_id, content, is_error=is_error)

    def _ask(self, op: Operation, decision: Decision) -> ApprovalChoice:
        if self.approval is None:
            return ApprovalChoice.DENY  # fail closed
        kind = ApprovalKind.COMMAND if op.tool == "run_command" else (
            ApprovalKind.PATCH if op.tool == "apply_patch" else ApprovalKind.OTHER)
        request = ApprovalRequest(
            request_id=new_id("apr"), approval_kind=kind, tool=op.tool,
            display=op.display, reason=decision.reason,
            detail=op.args.get("patch", "") if op.tool == "apply_patch" else "",
        )
        self._emit(request)
        return self.approval(request)

    def _audit(self, op: Operation, decision: str, outcome: str) -> None:
        if self.audit is not None:
            self.audit.record(workspace=str(self.tool_ctx.workspace.root), tool=op.tool,
                              target=op.target, decision=decision, outcome=outcome)

    # -- termination ------------------------------------------------------

    def _finalize(self, outcome: TurnOutcome) -> TurnOutcome:
        """The gate: a claimed ``completed`` must survive the journal and Git."""
        run_command = self.registry.tools.get("run_command")
        history = getattr(run_command, "history", [])
        result = verify_claims(list(outcome.claims), history, outcome.status.value)

        for claim in outcome.claims:
            self._journal(EventType.VALIDATION_CLAIM, claim.as_payload())

        rejected = list(result.rejected)
        status, reason = result.status, result.reason
        summary = outcome.summary

        # Fresh Git evidence at finish. A model that moved HEAD -- committing
        # through a wrapper the breaker's argv scan cannot see -- would leave a
        # clean-looking diff, so the baseline comparison is what catches it.
        evidence = self._collect_git_evidence()
        if evidence is not None:
            self._journal(EventType.GIT_BASELINE, {
                "phase": "final",
                "head_now": evidence.head_now,
                "head_moved": evidence.head_moved,
                "session_changed": sorted(evidence.session_changed),
                "preexisting_changed": sorted(evidence.preexisting_changed),
            })
            if evidence.head_moved:
                rejected.append(
                    f"HEAD moved during the session ({self.git_baseline.head[:12]} -> "
                    f"{evidence.head_now[:12]}); the diff no longer reflects what changed"
                )
                status = TerminalStatus.PARTIAL
                reason = "HEAD moved during the session"
            if evidence.session_changed or evidence.preexisting_changed:
                summary += "\n\nFiles changed by this session:\n" + (
                    "\n".join(f"  - {p}" for p in sorted(evidence.session_changed))
                    or "  (none)")
                if evidence.preexisting_changed:
                    summary += ("\nAlready modified before this session (not claimed):\n"
                                + "\n".join(f"  - {p}" for p in sorted(evidence.preexisting_changed)))

        for problem in rejected:
            self._emit(Notice(f"claim rejected: {problem}", level="warning"))
        if rejected:
            summary += "\n\nRejected claims:\n" + "\n".join(f"  - {r}" for r in rejected)

        return self._terminate(status, reason, summary)

    def _refresh_credentials(self) -> bool:
        """Re-acquire once per session, and only retry if the value changed.

        Never invalidates first: for the gateway source that clears the stored
        credential, and nothing can re-acquire it yet -- one expired token would
        log the user out permanently. Re-acquiring is harmless for both sources
        and picks up a token refreshed out of band.
        """
        if self.credentials is None or self._refreshed:
            return False
        self._refreshed = True
        if not hasattr(self.backend, "api_key"):
            return False
        try:
            handle = self.credentials.acquire()
        except FoundryError:
            return False
        fresh = handle.reveal()
        if fresh == self.backend.api_key:
            return False  # same credential: a second request would fail identically
        self.backend.api_key = fresh
        return True

    def _collect_git_evidence(self):
        if self.git_baseline is None:
            return None
        from foundry.core.tools.git import collect_evidence

        try:
            return collect_evidence(self.tool_ctx.workspace.root, self.git_baseline)
        except FoundryError as exc:
            self._emit(Notice(f"could not collect final Git evidence: {exc}", level="warning"))
            return None

    def _terminate(self, status: TerminalStatus, reason: str, summary: str = "") -> TurnOutcome:
        self._journal(EventType.TERMINATION, {"status": status.value, "reason": reason,
                                              "summary": summary})
        if self.session is not None:
            self.session._terminated = True
        self._emit(Termination(status, reason, summary))
        # A budget or context termination has no model summary; the reason is
        # the only explanation the caller gets, so it must survive.
        text = summary or reason
        return TurnOutcome(status=status, text=text, summary=text)
