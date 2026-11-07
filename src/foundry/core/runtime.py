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

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from foundry.core.backends.base import (ModelBackend, ReasoningComplete, TextDelta,
                                        TurnFinished, UsageUpdate)
from foundry.core.commands import builtin_prompt
from foundry.core.context import ContextManager
from foundry.core.conversation import Message, Role, ToolUseBlock, TurnRequest
from foundry.core.errors import (
    AuthError,
    Cancelled,
    FatalError,
    FoundryError,
    InvalidToolCall,
    ToolError,
    TransientError,
)
from foundry.core.events import (
    ApprovalChoice,
    ApprovalKind,
    ApprovalRequest,
    Compacted,
    ErrorEvent,
    EventSink,
    MessageDelta,
    Notice,
    PlanStep,
    PlanUpdated,
    Reasoning,
    StepStatus,
    TerminalStatus,
    Termination,
    TokenCount,
    ToolBegin,
    ToolEnd,
    ToolOutputDelta,
    ToolRejected,
    TurnComplete,
    TurnStarted,
    new_id,
)
from foundry.core.notify import payload as notify_payload
from foundry.core.policy.engine import POLICY_VERSION, Decision, PolicyEngine, Verdict
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.base import Operation, ToolContext, ToolKind, ToolOutput
from foundry.core.tools.finish import ValidationClaim, verify_claims
from foundry.core.tools.plan import plan_payload
from foundry.core.tools.registry import ToolRegistry

ApprovalCallback = Callable[[ApprovalRequest], ApprovalChoice]


def _tool_end_summary(op: Operation, output: ToolOutput, content: str) -> str:
    """One line saying how a tool call ended.

    For run_command the first content line is ``$ <command>`` -- a repeat of
    tool_begin.display -- so the stream never said what the exit code was.
    The command's outcome comes from its structured metadata instead.
    """
    if op.tool == "run_command" and ("exit_code" in output.metadata
                                     or output.metadata.get("timed_out")):
        meta = output.metadata
        if meta.get("timed_out"):
            limit = meta.get("timeout_s")
            summary = f"timed out after {limit}s" if limit else "timed out"
        elif meta.get("exit_code") is None:
            summary = "exit code unknown"
        else:
            summary = f"exit code {meta['exit_code']}"
        if meta.get("output_incomplete"):
            summary += " (output incomplete)"
        return summary
    # A patch's first line is "Applied:"; what failed is what the person
    # watching needs (D-076). What landed is shown as its diff.
    if op.tool == "apply_patch" and output.metadata.get("rejections"):
        first, *rest = output.metadata["rejections"]
        return f"{first} (and {len(rest)} more)" if rest else first
    return content.splitlines()[0] if content else ""


def _explain(exc: Exception) -> str:
    """The error, plus the provider's own words about it.

    raise_for_status carefully keeps 500 bytes of the response body on
    ``payload`` and nothing ever read it, so every HTTP failure reached the user
    as a bare status line -- "request rejected (HTTP 400)" -- while the body
    sitting unused said exactly which field was wrong.
    """
    detail = (getattr(exc, "payload", "") or "").strip()
    if not detail:
        return str(exc)
    return f"{exc}: {detail[:300]}"


# What a call the user's Ctrl+C or abort left is answered with (D-062).
STOPPED_WHILE_RUNNING = ("Interrupted: the user stopped the task while this call was running, "
                         "so whether it finished, and what it changed, is not known. Check "
                         "before relying on it.")
STOPPED_BEFORE_RUNNING = ("Not run: the user stopped the task before this call was executed. "
                          "Nothing was changed by it; call it again if it is still needed.")


@dataclass(slots=True)
class Budget:
    max_tool_rounds: int = 40
    max_tool_calls: int = 200
    max_tokens: int | None = None
    max_consecutive_failures: int = 4

    rounds: int = 0
    calls: int = 0

    def reset(self) -> None:
        """Start a new task. The limits are per task, not per session."""
        self.rounds = 0
        self.calls = 0

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
    # finish's `result` as JSON text under exec --output-schema (D-051);
    # None when there is none.
    result_json: str | None = None


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
    persist_rule: object | None = None   # callable(Operation) -> None, for "always"
    failures: FailureTracker = field(default_factory=FailureTracker)
    _cancelled: bool = False
    # The call whose tool is running, with its operation: what a Ctrl+C in
    # the middle of it leaves unknown (D-062).
    _running: tuple[ToolUseBlock, Operation] | None = None
    # How the last task ended, for the calls it left (D-063).
    _ended: tuple[TerminalStatus, str] | None = None
    _finish: TurnOutcome | None = None
    _refreshed: bool = False
    # Journal ordinal of the last successful change to the workspace.
    _last_mutation: int = 0
    # Each file a tool changed, with the history's length when it did: what
    # a rewind to before that point leaves changed (D-056).
    _patched_at: list[tuple[int, str]] = field(default_factory=list)
    # The model's plan (D-037): the session's, replaced whole by update_plan.
    plan: tuple[PlanStep, ...] = ()
    plan_explanation: str = ""
    # Summary compaction before the context fills (D-040). One failed attempt
    # stops it for the rest of the task: each attempt is a paid call.
    auto_compact: bool = True
    _compact_failed: bool = False
    # Whether the model's reasoning reaches subscribers (D-044); the journal
    # keeps it either way.
    show_reasoning: bool = True
    # Each image's artifact id, by its data: every request repeats the
    # history, and an image is stored once, not once a round (D-045).
    _image_ids: dict = field(default_factory=dict)
    # The user's program told when a turn ends (D-050): notify.Notifier.
    notifier: object | None = None
    scrub: Callable[[str], str] | None = None   # what leaves for the notifier
    _notify_failed: bool = False

    # -- helpers ----------------------------------------------------------

    def cancel(self) -> None:
        self._cancelled = True

    def interrupted(self) -> TurnOutcome:
        """Ctrl+C reached the task (D-062): the calls it left are answered and
        it ends as cancelled -- the task, not the session, which the caller
        may go on with."""
        if self.session is not None and self.session._terminated:
            # The task had already ended; the Ctrl+C came as that was said.
            return TurnOutcome(status=TerminalStatus.CANCELLED, text="interrupted by user",
                               summary="interrupted by user")
        return self._terminate(TerminalStatus.CANCELLED, "interrupted by user")

    def files_changed(self, ordinal: int, paths: list[str]) -> None:
        """Files changed outside the tools -- by an undo, or a command the user
        ran (whose files are not known) -- at journal ``ordinal``.

        A command run before then can no longer vouch for the workspace, and
        each of these files is read again before it is patched.
        """
        self._last_mutation = max(self._last_mutation, ordinal)
        for path in paths:
            self.tool_ctx.read_tracker.forget(path)

    def _journal(self, event_type: str, payload: dict) -> int:
        return self.session.append(event_type, payload) if self.session else 0

    def _emit(self, event) -> None:
        self.events.emit(event)

    # -- the loop ---------------------------------------------------------

    def run_turn(self, user_input: str, images: tuple = ()) -> TurnOutcome:
        """``images`` are ImageBlocks the user attached to this message (D-045)."""
        outcome = self._run_turn(user_input, images)
        self._notify(user_input, outcome)
        return outcome

    def _notify(self, user_input: str, outcome: TurnOutcome) -> None:
        """Tell the user's program the turn has ended (D-050). Not when the
        user stopped it: they are at the keyboard."""
        if self.notifier is None or outcome.status is TerminalStatus.CANCELLED:
            return
        data = notify_payload(
            session_id=self.session.session_id if self.session is not None else "",
            cwd=str(self.tool_ctx.workspace.root),
            status=outcome.status.value if outcome.status is not None else None,
            user_input=user_input, last_message=outcome.text, scrub=self.scrub)
        program = self.notifier.argv[0]
        try:
            pid = self.notifier.send(data)
        except (OSError, ValueError) as exc:
            self._journal(EventType.NOTIFIED, {"program": program, "error": str(exc)})
            if not self._notify_failed:
                self._notify_failed = True
                self._emit(Notice(f"notify: could not start {program}: {exc}",
                                  level="warning"))
            return
        self._journal(EventType.NOTIFIED, {"program": program, "pid": pid})

    def _run_turn(self, user_input: str, images: tuple) -> TurnOutcome:
        # The request records carry every message the model was sent; this is
        # for input that never reached one, which a resumed session still owes
        # an answer to.
        record: dict[str, Any] = {"text": user_input}
        if images:
            record["images"] = [self._block_payload(i, self._store_image) for i in images]
        self._journal(EventType.USER_INPUT, record)
        self.context.append_user(user_input, tuple(images))
        self._finish = None
        # Each task gets its own termination record. Left set by an earlier
        # task that ended blocked, it stopped the REPL from recording how the
        # session actually ended.
        if self.session is not None:
            self.session._terminated = False
        # Per-task limits, as the requirements specify. Left accumulating, an
        # interactive session silently died: once the running total passed the
        # limit, a later question produced no work at all and the REPL exited.
        self.budget.reset()
        self._compact_failed = False

        while True:
            if self._cancelled:
                return self._terminate(TerminalStatus.CANCELLED, "interrupted by user")

            turn_index = self.context.start_turn()
            self.budget.rounds += 1
            self._emit(TurnStarted(turn_index))

            reason = self.budget.check(self.context.session_usage.total)
            if reason:
                return self._terminate(TerminalStatus.PARTIAL, reason)

            if (self.auto_compact and not self._compact_failed
                    and self.context.needs_compaction()):
                try:
                    failed = self.compact(auto=True)
                except Cancelled:
                    return self._terminate(TerminalStatus.CANCELLED, "interrupted by user")
                if failed:
                    self._compact_failed = True
                    self._emit(Notice(f"{failed}; carrying on without it", level="warning"))
                if self._cancelled:
                    return self._terminate(TerminalStatus.CANCELLED, "interrupted by user")

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
                        self._emit(ErrorEvent(_explain(retry_exc),
                                              category=retry_exc.category, fatal=True))
                        return self._terminate(TerminalStatus.BLOCKED,
                                               f"authentication: {_explain(retry_exc)}")
                else:
                    self._emit(ErrorEvent(_explain(exc), category=exc.category, fatal=True))
                    return self._terminate(TerminalStatus.BLOCKED,
                                           f"authentication: {_explain(exc)}")
            except TransientError as exc:
                self._emit(ErrorEvent(_explain(exc), category=exc.category, fatal=True))
                return self._terminate(TerminalStatus.BLOCKED,
                                       f"provider unavailable: {_explain(exc)}")
            except FatalError as exc:
                self._emit(ErrorEvent(_explain(exc), category=exc.category, fatal=True))
                return self._terminate(TerminalStatus.FAILED, _explain(exc))
            except Cancelled:
                return self._terminate(TerminalStatus.CANCELLED, "interrupted by user")

            self.context.append(turn.as_message())
            self.context.record_usage(turn.usage)
            self._emit(TokenCount(turn.usage, self.context.session_usage))
            self._journal(EventType.TOKEN_USAGE, {
                "input_tokens": turn.usage.input_tokens,
                "output_tokens": turn.usage.output_tokens,
                # Already included in input_tokens; recorded separately so the
                # report can tell cache reads from full-price input.
                "cached_input_tokens": turn.usage.cached_input_tokens,
            })

            if not turn.tool_calls:
                self._emit(TurnComplete(turn_index, turn.text))
                return TurnOutcome(status=None, text=turn.text)

            for call in turn.tool_calls:
                self.budget.calls += 1
                # Checked per call, not once per round: a backend returning
                # thousands of tool calls in a single turn executed all of them
                # while the limit was only inspected at the top of the loop.
                over = self.budget.check(self.context.session_usage.total)
                if over:
                    return self._terminate(TerminalStatus.PARTIAL, over)
                self._dispatch(call)
                if self._finish is not None:
                    return self._finalize(self._finish)
                if self._cancelled:
                    return self._terminate(TerminalStatus.CANCELLED, "interrupted by user")

    def compact(self, *, auto: bool, focus: str = "") -> str:
        """Replace the history with the user's messages and the model's summary
        of the work (D-040). Returns why it did not, or "" when it did.

        The summary request carries the same tools as any other, since a
        history holding tool calls is refused without them, and says not to
        use them; a call it makes anyway is ignored. Files read before are
        read again before a patch: their contents are no longer in context.
        """
        if not self.context.compactable():
            return "nothing to compact yet"
        before = self.context.estimated_context_tokens()
        self._emit(Notice("compacting the conversation to free context", level="info"))
        prompt = builtin_prompt("compact").strip()
        if focus:
            prompt += f"\n\nThe user asks the summary to give particular attention to: {focus}"
        messages = (*self.context.project(), Message.text(Role.USER, prompt))
        try:
            turn = self._sample(messages, purpose="compact")
        except Cancelled:
            raise
        except FoundryError as exc:
            return f"compaction failed: {_explain(exc)}"
        self.context.record_usage(turn.usage)
        self._emit(TokenCount(turn.usage, self.context.session_usage))
        self._journal(EventType.TOKEN_USAGE, {
            "input_tokens": turn.usage.input_tokens,
            "output_tokens": turn.usage.output_tokens,
            "cached_input_tokens": turn.usage.cached_input_tokens,
            "purpose": "compact",
        })
        summary = turn.text.strip()
        if not summary:
            return "compaction failed: the model wrote no summary"
        if self.plan:
            summary += "\n\nYour plan, as update_plan last set it:\n" + "\n".join(
                f"- [{step.status.value}] {step.step}" for step in self.plan)
        replaced = len(self.context.history)
        self.context.replace_history(self.context.compacted_history(summary))
        self.tool_ctx.read_tracker.clear()
        # Positions in the old history mean nothing in the new one.
        self._patched_at = []
        after = self.context.estimated_context_tokens()
        self._journal(EventType.COMPACTED, {
            "reason": "auto" if auto else "user",
            "turn": self.context.turn,
            "replaced": replaced,
            "tokens_before": before,
            "tokens_after": after,
            "messages": self._history_payload(),
        })
        self._emit(Compacted(tokens_before=before, tokens_after=after, auto=auto))
        return ""

    def rewind(self, length: int) -> list[str]:
        """Go back to the conversation's first ``length`` messages (/rewind,
        D-056) and return the files changed after that point.

        Nothing in the workspace is reverted. The model no longer sees what
        it read after the point, so every file is read again before it is
        patched, as after a compaction. The history kept is journaled whole,
        as a compaction's is, so a resume rebuilds it exactly.
        """
        removed = len(self.context.history) - length
        self.context.truncate(length)
        self.tool_ctx.read_tracker.clear()
        changed = list(dict.fromkeys(path for at, path in self._patched_at if at > length))
        # Those changes now come before the next message, which tells of them:
        # going back past that message again forgets it too.
        self._patched_at = [(min(at, length + 1), path) for at, path in self._patched_at]
        self._journal(EventType.REWOUND, {
            "keep": length,
            "removed": removed,
            "turn": self.context.turn,
            "turns": list(self.context._turn_of_message),
            "messages": self._history_payload(),
        })
        return changed

    def _history_payload(self) -> list[dict[str, Any]]:
        return [
            {"role": m.role.value,
             "blocks": [self._block_payload(b, self._store_image) for b in m.blocks]}
            for m in self.context.history
        ]

    def _sample(self, messages: tuple[Message, ...] | None = None, purpose: str = ""):
        request = TurnRequest(
            messages=self.context.project() if messages is None else messages,
            tools=self.registry.schemas(),
            model=self.model,
        )
        if messages is None:
            # The conversation's own request: the next is compared with it
            # (D-048). A compaction's request is outside the conversation.
            self.context.mark_sent()
        # The journal has to hold enough to rebuild the request, or "the
        # transcript is the source of truth" is only a slogan: replay, resume,
        # and any audit of what was actually sent all read this record. The
        # Authorization header is the one thing deliberately absent.
        payload: dict[str, Any] = {
            "model": self.model,
            # The round, so a resumed session masks exactly what this one would.
            "turn": self.context.turn,
            "message_count": len(request.messages),
            "tools": [t.name for t in request.tools],
            "messages": [
                {
                    "role": message.role.value,
                    "blocks": [self._block_payload(b, self._store_image)
                               for b in message.blocks],
                }
                for message in request.messages
            ],
        }
        # A request outside the conversation -- a compaction's -- says so, and
        # neither it nor its response is part of the history a resume rebuilds.
        if purpose:
            payload["purpose"] = purpose
        self._journal(EventType.MODEL_REQUEST, payload)

        text_parts: list[str] = []
        reasoning: list[str] = []
        finished = None
        for event in self.backend.stream_turn(request):
            if isinstance(event, TextDelta):
                text_parts.append(event.text)
                # A summary is the model talking to itself, not to the user.
                if not purpose:
                    self._emit(MessageDelta(event.text))
            elif isinstance(event, ReasoningComplete):
                if event.text:
                    reasoning.append(event.text)
                    if not purpose and self.show_reasoning:
                        self._emit(Reasoning(event.text))
            elif isinstance(event, UsageUpdate):
                pass  # carried on the finished turn
            elif isinstance(event, TurnFinished):
                finished = event.turn

        if finished is None:
            raise FatalError("backend produced no finished turn")

        response = {
            "text": finished.text,
            "tool_calls": [{"call_id": c.call_id, "name": c.name, "arguments": c.arguments}
                           for c in finished.tool_calls],
            "stop_reason": finished.stop_reason.value,
            "notices": list(finished.notices),
        }
        # The next request replays it, so it reaches the journal anyway -- except
        # after the last response, which is the one a resumed session needs.
        if finished.opaque is not None:
            response["opaque"] = self._block_payload(finished.opaque)
        if reasoning:
            response["reasoning"] = reasoning
        if purpose:
            response["purpose"] = purpose
        self._journal(EventType.MODEL_RESPONSE, response)
        for notice in finished.notices:
            self._emit(Notice(notice, level="warning"))
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
            self._emit(ToolRejected(call_id=call.call_id, tool=call.name,
                                    display=call.name, reason=f"invalid tool call: {exc}"))
            return

        op = self._canonicalize_paths(op)

        record = {"call_id": call.call_id, "name": op.tool, "target": op.target,
                  "display": op.display}
        # The paths a change was asked for, so a resumed session can tell the
        # files it changed itself from changes made outside it.
        if op.kind is ToolKind.MUTATOR and op.args.get("paths"):
            record["paths"] = list(op.args["paths"])
        self._journal(EventType.TOOL_CALL, record)

        decision, op = self.policy.evaluate(op)
        self._journal(EventType.POLICY_DECISION, {
            "call_id": call.call_id, "verdict": decision.verdict.value,
            "reason": decision.reason, "rule_id": decision.rule_id, "step": decision.step,
            "policy_digest": decision.policy_digest,
            "operation_digest": decision.operation_digest,
            # The digest covers the rules, not the mode, and the mode can change
            # within a session (D-038).
            "mode": self.policy.mode.value,
            # The digest identifies which rules were in force; the version says
            # how to read them. Without it a replay tool comparing two journals
            # cannot tell a rule change from an evaluator change.
            "policy_version": POLICY_VERSION,
        })

        if decision.verdict is Verdict.DENY:
            self._audit(op, f"DENY:{decision.rule_id}", "blocked")
            self._reply(call, f"blocked by policy: {decision.reason}", is_error=True)
            self._emit(ToolRejected(call_id=call.call_id, tool=op.tool, display=op.display,
                                    reason=decision.reason, rule_id=decision.rule_id,
                                    step=decision.step))
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
                self._emit(ToolRejected(call_id=call.call_id, tool=op.tool,
                                        display=op.display, reason="aborted by the user"))
                return
            if choice is ApprovalChoice.DENY:
                self._audit(op, "DENY:user", "declined")
                if self.approval is None:
                    # Nobody was asked, so saying the user declined is a false
                    # statement about a decision that never happened -- and it
                    # discards the real reason, which is often something the
                    # model could act on (a stray '#', an unparseable form).
                    self._reply(call, f"blocked: {decision.reason} (this run is "
                                      "unattended, so nobody could be asked)",
                                is_error=True)
                else:
                    self._reply(call, "the user declined this operation", is_error=True)
                self._emit(ToolRejected(call_id=call.call_id, tool=op.tool,
                                        display=op.display, reason=decision.reason,
                                        rule_id=decision.rule_id, step=decision.step))
                return
            if choice is ApprovalChoice.SESSION:
                self.policy.grant_for_session(op)
            elif choice is ApprovalChoice.ALWAYS:
                # Persisted as a rule in the user's own config, keyed to this
                # workspace. Answering "always" used to be strictly worse than
                # "session": it granted nothing at all.
                self.policy.grant_for_session(op)
                if self.persist_rule is not None:
                    try:
                        self.persist_rule(op)
                        self._emit(Notice(
                            f"remembered: {op.tool} ({op.target}) will not be asked again"))
                    except OSError as exc:
                        self._emit(Notice(
                            f"could not save the rule ({exc}); it applies to this "
                            "session only", level="warning"))
            self._audit(op, f"ASK:{choice.value}", "approved")
        else:
            self._audit(op, f"ALLOW:{decision.rule_id}", "auto")

        # Not cleared on the way out of a Ctrl+C: that is when it is read.
        self._running = (call, op)
        self._execute(call, op)
        self._running = None

    def _execute(self, call: ToolUseBlock, op: Operation) -> None:
        tool = self.registry.get(op.tool)
        self._emit(ToolBegin(call.call_id, op.tool, op.display))
        # Wall-clock time of the tool's own execution, measured here so every
        # tool_end carries it -- including the ones for tools that raised.
        # perf_counter, not monotonic: on Windows monotonic ticks every ~15.6 ms,
        # so a 200 ms call could be reported as 187.
        started = time.perf_counter()

        def elapsed_ms() -> int:
            return int((time.perf_counter() - started) * 1000)

        def on_output(line: str, stream: str) -> None:
            self._emit(ToolOutputDelta(call.call_id, line, stream))

        self.tool_ctx.on_output = on_output
        try:
            output: ToolOutput = tool.execute(op, self.tool_ctx)
        except ToolError as exc:
            duration_ms = elapsed_ms()
            count = self.failures.record(op, exc)
            if count >= self.budget.max_consecutive_failures:
                self._emit(ErrorEvent(f"{op.tool} failed {count} times", category=exc.category))
                self._reply(call, f"{exc} (this exact operation has failed {count} times; "
                                  "try a different approach)", is_error=True)
                self._emit(ToolEnd(call.call_id, op.tool, False, str(exc),
                                   duration_ms=duration_ms))
                return
            self._reply(call, str(exc), is_error=True)
            self._emit(ToolEnd(call.call_id, op.tool, False, str(exc),
                               duration_ms=duration_ms))
            return
        except FoundryError as exc:
            duration_ms = elapsed_ms()
            self._reply(call, f"{exc}", is_error=True)
            self._emit(ToolEnd(call.call_id, op.tool, False, str(exc),
                               duration_ms=duration_ms))
            return
        except Exception as exc:  # noqa: BLE001
            # A tool raising something outside the taxonomy -- an OSError from a
            # locked file, a PathRejected (which is a ValueError) -- must become
            # a tool result the model can act on. Letting it escape kills the
            # session with a traceback and loses the turn's evidence.
            duration_ms = elapsed_ms()
            detail = f"{type(exc).__name__}: {exc}"
            self.failures.record(op, exc)
            self._journal(EventType.ERROR, {"call_id": call.call_id, "tool": op.tool,
                                            "error": detail})
            self._emit(ErrorEvent(detail, category="tool_unexpected"))
            self._reply(call, f"the tool failed unexpectedly: {detail}", is_error=True)
            self._emit(ToolEnd(call.call_id, op.tool, False, detail,
                               duration_ms=duration_ms))
            return
        finally:
            self.tool_ctx.on_output = None

        duration_ms = elapsed_ms()
        self.failures.clear(op)

        if op.tool == "finish":
            self._finish = TurnOutcome(
                status=TerminalStatus(output.metadata["status"]), text="",
                summary=output.metadata["summary"],
                claims=tuple(output.metadata["claims"]),
                result_json=(json.dumps(output.metadata["result"], ensure_ascii=False)
                             if "result" in output.metadata else None),
            )
            self._emit(ToolEnd(call.call_id, op.tool, True, "finalizing",
                               duration_ms=duration_ms))
            return

        content = output.content
        if output.metadata.get("event_ordinal"):
            content += f"\n[event_id={output.metadata['event_ordinal']}]"

        result: dict[str, Any] = {
            "call_id": call.call_id, "tool": op.tool, "is_error": output.is_error,
            "artifact_id": output.artifact_id, "truncated": output.truncated,
        }
        if output.metadata.get("changes"):
            # Each file's content before and after: what undo restores (D-036).
            result["changes"] = output.metadata["changes"]
        planned = op.tool == "update_plan"
        if planned:
            # Kept with the call, so a resumed session shows the plan again.
            self.plan, self.plan_explanation = op.args["steps"], op.args["explanation"]
            result["plan"] = plan_payload(self.plan, self.plan_explanation)
        ordinal = self._journal(EventType.TOOL_RESULT, result)
        # When the workspace last changed. A claim citing a command older than
        # this cannot vouch for the change -- see verify_claims. A patch that
        # landed some files and rejected others is an error *and* a change:
        # counting only clean results let a green run from before it vouch for
        # the files that did land.
        changed = not output.is_error or bool(output.metadata.get("applied"))
        if op.kind is ToolKind.MUTATOR and op.tool != "run_command" and changed:
            self._last_mutation = max(self._last_mutation, ordinal)
            at = len(self.context.history)
            self._patched_at.extend((at, path) for path in op.args.get("paths") or ())
        self._reply(call, content, is_error=output.is_error, images=tuple(output.images))
        if planned:
            self._emit(PlanUpdated(call.call_id, self.plan, self.plan_explanation))
        self._emit(ToolEnd(call.call_id, op.tool, not output.is_error,
                           _tool_end_summary(op, output, content),
                           duration_ms=duration_ms,
                           output=(output.metadata.get("output_tail", "")
                                   or output.metadata.get("diff", ""))))

    def _canonicalize_paths(self, op: Operation) -> Operation:
        """Give policy the spelling the filesystem agrees with.

        Textual normalization cannot see that AVERYL~1.PY and
        averylongfilename.py are one file, so an 8.3 alias skipped the
        dirty-file guard -- the rule that exists to outrank accept_edits -- and
        an uncommitted file was rewritten with no prompt. The tool layer already
        resolves through the workspace; policy has to see the same names.
        """
        paths = op.args.get("paths")
        if not paths:
            return op
        workspace = getattr(self.tool_ctx, "workspace", None)
        if workspace is None:
            return op

        resolved: list[str] = []
        for path in paths:
            try:
                resolved.append(workspace.resolve(path).relative)
            except Exception:  # noqa: BLE001 - an invalid path is the tool's to reject
                resolved.append(path)
        if resolved == list(paths):
            return op

        from dataclasses import replace as _replace

        return _replace(op, args={**op.args, "paths": resolved})

    @staticmethod
    def _block_payload(block, store=None) -> dict:
        """A block as the journal keeps it. An image is kept by the id of the
        artifact ``store`` put it in, never inline: every request repeats the
        history, and a screenshot a round would swamp the journal."""
        from foundry.core.conversation import (ImageBlock, OpaqueBlock, TextBlock,
                                               ToolResultBlock, ToolUseBlock as TU)

        if isinstance(block, TextBlock):
            return {"kind": "text", "text": block.text}
        if isinstance(block, TU):
            return {"kind": "tool_use", "call_id": block.call_id, "name": block.name,
                    "arguments": block.arguments}
        if isinstance(block, ToolResultBlock):
            payload = {"kind": "tool_result", "call_id": block.call_id,
                       "content": block.content, "is_error": block.is_error}
            if block.images:
                payload["images"] = [AgentRuntime._block_payload(i, store)
                                     for i in block.images]
            return payload
        if isinstance(block, OpaqueBlock):
            return {"kind": "opaque", "provider": block.provider, "data": block.data}
        if isinstance(block, ImageBlock):
            return {"kind": "image", "media_type": block.media_type, "name": block.name,
                    "artifact": store(block) if store is not None else None}
        return {"kind": "unknown"}

    def _store_image(self, block) -> str | None:
        """The artifact holding this image's bytes, written the first time.
        None when there is no session, or when the bytes hold a credential
        Foundry knows: an image cannot be scrubbed, so it is not kept."""
        if self.session is None:
            return None
        if block.data not in self._image_ids:
            self._image_ids[block.data] = self.session.artifacts.put_exact(
                base64.b64decode(block.data))
        return self._image_ids[block.data]

    def _reply(self, call: ToolUseBlock, content: str, *, is_error: bool = False,
               images: tuple = ()) -> int:
        self.context.append_tool_result(call.call_id, content, is_error=is_error,
                                        images=images)
        # What the model will read, word for word. The next request carries it
        # too, but a session that stops first has no next request.
        record: dict[str, Any] = {"call_id": call.call_id, "tool": call.name,
                                  "content": content, "is_error": is_error}
        if images:
            record["images"] = [self._block_payload(i, self._store_image) for i in images]
        return self._journal(EventType.TOOL_REPLY, record)

    def _answer_left(self) -> None:
        """Give each call the stopped task left a result (D-062), so the
        conversation can go on: a request with a call left unanswered is
        refused by every provider. One stopped while its tool ran may have
        changed something, so no command before it vouches for the workspace.
        """
        running, self._running = self._running, None
        for call in self.context.unanswered_calls():
            if running is None or running[0].call_id != call.call_id:
                self._reply(call, STOPPED_BEFORE_RUNNING, is_error=True)
                continue
            op = running[1]
            ordinal = self._reply(call, STOPPED_WHILE_RUNNING, is_error=True)
            # As a result would (run_command is evidence, not a change).
            if op.kind is ToolKind.MUTATOR and op.tool != "run_command":
                self.files_changed(ordinal, list(op.args.get("paths") or ()))
            self._emit(ToolEnd(call.call_id, op.tool, False, "interrupted"))

    def answer_left_calls(self) -> None:
        """Answer the calls the last task left as it ended -- finish, and any
        after it or after a budget stopped it -- when the conversation goes on
        (D-063): a request with a call left unanswered is refused by every
        provider. An unattended run ends instead, and a resume answers them
        as it rebuilds the history.
        """
        status, reason = self._ended or (TerminalStatus.INTERRUPTED, "")
        ended = status.value + ("" if reason in ("", "reported by the agent") else f": {reason}")
        for call in self.context.unanswered_calls():
            if call.name == "finish":
                self._reply(call, f"finish was accepted: the task ended with status {ended}.")
            else:
                self._reply(call, f"Not run: the task ended ({ended}) before this call was "
                                  "executed. Nothing was changed by it; call it again if it is "
                                  "still needed.", is_error=True)

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
        result = verify_claims(list(outcome.claims), history, outcome.status.value,
                               last_mutation_ordinal=self._last_mutation)

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

        # The plan is the model's own account, so it decides nothing: a step it
        # forgot to tick is not a failed task, and ticking every step proves
        # nothing was done. What it can do is show where the account and the
        # outcome disagree.
        unfinished = [s for s in self.plan if s.status is not StepStatus.COMPLETED]
        if unfinished:
            summary += "\n\nPlan steps not marked completed:\n" + "\n".join(
                f"  - [{s.status.value.replace('_', ' ')}] {s.step}" for s in unfinished)
            if status is TerminalStatus.COMPLETED:
                self._emit(Notice(f"finish reported completed with {len(unfinished)} plan "
                                  "step(s) not marked completed", level="warning"))

        return self._terminate(status, reason, summary, result_json=outcome.result_json)

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

    def _terminate(self, status: TerminalStatus, reason: str, summary: str = "",
                   result_json: str | None = None) -> TurnOutcome:
        if status is TerminalStatus.CANCELLED:
            self._answer_left()
            # Honoured, so spent (D-062): a Ctrl+C or an abort stops the task
            # it came in, and the next one starts afresh.
            self._cancelled = False
        # The sink holds a tail of streamed text so a credential cannot straddle
        # two deltas unseen. Every path here is followed by an event that would
        # release it anyway; flushing first makes that independent of ordering.
        flush = getattr(self.events, "flush", None)
        if callable(flush):
            flush()

        # A journal that could not be written means the evidence for this run is
        # incomplete; the report must say so rather than reading as a clean
        # record of what happened.
        degraded = getattr(self.session, "degraded", "") if self.session else ""
        if degraded:
            summary += (f"\n\nThe session journal could not be written ({degraded}); "
                        "this run's recorded evidence is incomplete.")
            self._emit(Notice(f"session journal degraded: {degraded}", level="warning"))

        audit_degraded = getattr(self.audit, "degraded", "") if self.audit else ""
        if audit_degraded:
            summary += (f"\n\nThe audit log could not be written ({audit_degraded}); "
                        "the record of what was allowed and denied is incomplete.")
            self._emit(Notice(f"audit log degraded: {audit_degraded}", level="warning"))

        record: dict[str, Any] = {"status": status.value, "reason": reason, "summary": summary}
        result = json.loads(result_json) if result_json is not None else None
        if result_json is not None:
            record["result"] = result
        self._journal(EventType.TERMINATION, record)
        self._ended = (status, reason)
        if self.session is not None:
            self.session._terminated = True
        self._emit(Termination(status, reason, summary, result=result))
        # A budget or context termination has no model summary; the reason is
        # the only explanation the caller gets, so it must survive.
        text = summary or reason
        return TurnOutcome(status=status, text=text, summary=text, result_json=result_json)
