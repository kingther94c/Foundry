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
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from foundry.core.backends.base import (ModelBackend, ReasoningComplete, TextDelta,
                                        TurnFinished, UsageUpdate)
from foundry.core.commands import builtin_prompt
from foundry.core.context import ContextManager
from foundry.core.conversation import Message, Role, ToolUseBlock, TurnRequest, Usage
from foundry.core.errors import (
    AuthError,
    Cancelled,
    FatalError,
    FoundryError,
    InvalidToolCall,
    QuotaExceeded,
    StreamDropped,
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
from foundry.core.goal import (
    ACTIVE,
    BUDGET_LIMITED,
    BUDGET_TURN,
    COMPLETE,
    Goal,
    budget_prompt,
    spent,
)
from foundry.core.goal import BLOCKED as BLOCKED_GOAL
from foundry.core.goal import notice as goal_notice
from foundry.core.goal import view as goal_view
from foundry.core.hooks import (
    INTERRUPT,
    PERMISSION_MODES,
    PERMISSION_REQUEST,
    POST_COMPACT,
    POST_MODEL_REQUEST,
    POST_TOOL_USE,
    PRE_COMPACT,
    PRE_MODEL_REQUEST,
    PRE_TOOL_USE,
    READERS,
    SESSION_END,
    SESSION_END_REASON,
    SESSION_START,
    STOP,
    USER_PROMPT_SUBMIT,
    HookVerdict,
    hook_tool_input,
    hook_tool_name,
    matcher_names,
    rewritten_args,
    scrubbed,
)
from foundry.core.notify import APPROVAL_TYPE as NOTIFY_APPROVAL
from foundry.core.notify import EVENT_TYPE as NOTIFY_TURN
from foundry.core.notify import approval_payload
from foundry.core.notify import payload as notify_payload
from foundry.core.policy.engine import POLICY_VERSION, Decision, PolicyEngine, Verdict
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.base import Operation, ToolContext, ToolKind, ToolOutput
from foundry.core.tools.finish import ValidationClaim, verify_claims
from foundry.core.tools.goal import offer_goal_tools
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


def _seconds(delay: float) -> str:
    """``4s``, ``1.5s``, ``0.2s``: a wait as the person watching reads it."""
    return f"{delay:.1f}".rstrip("0").rstrip(".") + "s"


# What a call the user's Ctrl+C or abort left is answered with (D-062).
STOPPED_WHILE_RUNNING = ("Interrupted: the user stopped the task while this call was running, "
                         "so whether it finished, and what it changed, is not known. Check "
                         "before relying on it.")
STOPPED_BEFORE_RUNNING = ("Not run: the user stopped the task before this call was executed. "
                          "Nothing was changed by it; call it again if it is still needed.")
# The rule id a PreToolUse hook's refusal is journaled and audited under (D-115).
HOOK_RULE = "hook.PreToolUse"


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
    # backend.max_output_tokens (D-096): every request's cap, None the
    # backend's own.
    max_output_tokens: int | None = None
    # backend.stream_max_retries (D-099): how often a turn whose stream was
    # cut is asked again, and the wait before each.
    stream_max_retries: int = 5
    sleep: Callable[[float], None] = time.sleep
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
    # What a compaction asks for, as codex's compact_prompt (D-142); "" for
    # Foundry's own.
    compact_prompt: str = ""
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
    # The user's lifecycle hooks (D-115): hooks.HookSet, None for none.
    hooks: object | None = None
    # What hooks said for the model about a call, by its id, added to its
    # result whichever way the call ends.
    _hook_notes: dict = field(default_factory=dict)
    # The task a hook is told of, as codex's turn_id (D-116): the round it
    # began in, the same for each of its hooks.
    _turn_id: str = ""
    # The SessionStart hooks owed, by source, as codex's pending starts:
    # how the session began ("startup" or "resume", D-117), run at its first
    # prompt, and "compact" after each compaction, run before the next
    # request (D-118).
    start_sources: list = field(default_factory=list)
    # Why a PreCompact or PostCompact hook stopped the compaction or the task
    # after it, for the loop to end the task with; "" when none did.
    _compact_stop: str = ""
    # The goal the session pursues across turns (D-127), None for none: the
    # REPL starts its turns, the runtime counts what they spend.
    goal: Goal | None = None
    clock: Callable[[], float] = time.monotonic
    # When the goal last became active, by the clock; None while it is not.
    _goal_since: float | None = None
    # The goal's budget ran out in this task: the model is told so before its
    # next request.
    _goal_wrap_up: bool = False
    # The sub-agent running for this runtime's spawn_agent call (D-141), None
    # otherwise: a cancel reaches it too.
    child: "AgentRuntime | None" = None

    def __post_init__(self) -> None:
        # A backend that retries says so (D-085). Without this a 429 or a
        # dropped connection was waited out in silence -- up to a minute a
        # try -- which looked exactly like a hang.
        if hasattr(self.backend, "on_retry"):
            self.backend.on_retry = self._retrying

    # -- helpers ----------------------------------------------------------

    def cancel(self) -> None:
        self._cancelled = True
        if self.child is not None:
            self.child.cancel()

    def spent_by_agent(self, usage: Usage, number: int) -> None:
        """What sub-agent ``number`` spent (D-141), the session's: in its
        tokens, its budget and its goal's, and in its journal, as its own
        turns' are -- a resume counts it again."""
        self.context.session_usage = self.context.session_usage + usage
        self._journal(EventType.TOKEN_USAGE, {
            "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
            "cached_input_tokens": usage.cached_input_tokens, "agent": number,
        })
        self._goal_spent(usage)

    def _retrying(self, exc: Exception, attempt: int, attempts: int, delay: float,
                  *, cut: bool = False, shown: bool = False) -> None:
        """A model request failed in a way worth trying again; the backend
        waits ``delay`` seconds and makes try ``attempt`` of ``attempts``.
        ``cut``: the turn's stream broke part-way and the turn is asked again
        (D-099); ``shown``: some of its answer was already on screen."""
        payload = {"attempt": attempt, "attempts": attempts, "delay": round(delay, 3),
                   "reason": _explain(exc)}
        if cut:
            payload["cut"] = True
        self._journal(EventType.MODEL_RETRY, payload)
        again = "asking again" if cut else "retrying"
        dropped = "; the answer above was cut off and is not kept" if shown else ""
        self._emit(Notice(f"{exc}; {again} in {_seconds(delay)} (try {attempt} of {attempts})"
                          f"{dropped}", level="warning"))
        if not cut:
            # The backend tries again within one request: to the user's
            # hooks each try is an attempt, as codex's (D-122).
            self._model_hooks(POST_MODEL_REQUEST, status="failed", error=_explain(exc))
            self._model_hooks(PRE_MODEL_REQUEST)

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

    # -- the goal (D-127) ---------------------------------------------------

    def goal_now(self) -> Goal | None:
        """The goal, with the time spent on it up to now."""
        if self.goal is None or self._goal_since is None:
            return self.goal
        return replace(self.goal, seconds_used=self.goal.seconds_used
                       + int(self.clock() - self._goal_since))

    def change_goal(self, goal: Goal | None, *, by: str, reason: str = "") -> None:
        """The goal set, changed or cleared (None) -- by the user, the model,
        the runtime or its budget: journaled whole, told to the user, and its
        tools offered to the model. Build ``goal`` from goal_now(), so the time
        spent so far is kept."""
        self.goal = goal
        self._goal_since = self.clock() if goal is not None and goal.status == ACTIVE else None
        payload = goal.payload() if goal is not None else {"cleared": True}
        payload["by"] = by
        if reason:
            payload["reason"] = reason
        self._journal(EventType.GOAL, payload)
        if goal is not None:
            offer_goal_tools(self.registry, self)
        warn = goal is not None and goal.status in (BLOCKED_GOAL, BUDGET_LIMITED)
        self._emit(Notice(goal_notice(goal, reason), level="warning" if warn else "info"))

    def restore_goal(self, goal: Goal) -> None:
        """A resumed session's goal, as its journal has it: not journaled again."""
        self.goal = goal
        self._goal_since = self.clock() if goal.status == ACTIVE else None
        offer_goal_tools(self.registry, self)

    def goal_update(self, status: str) -> str:
        """update_goal: the model marks the goal complete, blocked or paused.
        Only an active goal goes to blocked or paused -- a spent budget comes
        before a pause, as codex's -- and any unfinished one to complete."""
        goal = self.goal_now()
        if goal is None:
            raise ToolError("there is no goal; the user sets one with /goal")
        if goal.status == COMPLETE:
            raise ToolError("the goal is already complete")
        if status != COMPLETE and goal.status != ACTIVE:
            raise ToolError(f"the goal is {goal.status}, not active; resuming it is the "
                            "user's")
        self.change_goal(replace(goal, status=status), by="model")
        return goal_view(self.goal_now(), completed=status == COMPLETE)

    def _goal_spent(self, usage: Usage) -> None:
        """A response's tokens, counted toward an active goal; past its budget
        the goal stops, and the model is told to wrap up."""
        goal = self.goal
        if goal is None or goal.status != ACTIVE:
            return
        self.goal = replace(goal, tokens_used=goal.tokens_used + spent(usage))
        if self.goal.over_budget:
            self.change_goal(replace(self.goal_now(), status=BUDGET_LIMITED), by="budget")
            self._goal_wrap_up = True

    def _journal(self, event_type: str, payload: dict) -> int:
        return self.session.append(event_type, payload) if self.session else 0

    def _emit(self, event) -> None:
        self.events.emit(event)

    # -- the loop ---------------------------------------------------------

    def run_turn(self, user_input: str, images: tuple = (), *,
                 typed: str | None = None) -> TurnOutcome:
        """``images`` are ImageBlocks the user attached to this message (D-045);
        ``typed`` is what the user typed for it, when the message says more --
        a command's prompt, a note -- journaled for a resume to show (D-108)."""
        outcome = self._run_turn(user_input, images, typed)
        self._notify(user_input, outcome)
        return outcome

    def _notify(self, user_input: str, outcome: TurnOutcome) -> None:
        """Tell the user's program the turn has ended (D-050). Not when the
        user stopped it: they are at the keyboard."""
        if self.notifier is None or outcome.status is TerminalStatus.CANCELLED:
            return
        self._send_notification(NOTIFY_TURN, notify_payload(
            session_id=self.session.session_id if self.session is not None else "",
            cwd=str(self.tool_ctx.workspace.root),
            status=outcome.status.value if outcome.status is not None else None,
            user_input=user_input, last_message=outcome.text, scrub=self.scrub))

    def _notify_approval(self, op: Operation, decision: Decision) -> None:
        """Tell the user's program a question waits for them (D-079), before
        it blocks: a session left running stops at a question nobody sees."""
        if self.notifier is None:
            return
        self._send_notification(NOTIFY_APPROVAL, approval_payload(
            session_id=self.session.session_id if self.session is not None else "",
            cwd=str(self.tool_ctx.workspace.root), tool=op.tool, display=op.display,
            reason=decision.reason, scrub=self.scrub))

    def _send_notification(self, kind: str, data: str) -> None:
        """Start the program and journal it; a program that cannot start is
        a warning the first time and a record each time, never a failure."""
        program = self.notifier.argv[0]
        try:
            pid = self.notifier.send(data)
        except (OSError, ValueError) as exc:
            self._journal(EventType.NOTIFIED, {"type": kind, "program": program,
                                               "error": str(exc)})
            if not self._notify_failed:
                self._notify_failed = True
                self._emit(Notice(f"notify: could not start {program}: {exc}",
                                  level="warning"))
            return
        self._journal(EventType.NOTIFIED, {"type": kind, "program": program, "pid": pid})

    def _run_turn(self, user_input: str, images: tuple, typed: str | None) -> TurnOutcome:
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
        self._goal_wrap_up = False
        self._turn_id = str(self.context.turn + 1)

        message = user_input
        if self.hooks is not None:
            notes, refused = self._prompt_hooks(user_input)
            if refused:
                # Not sent, so not recorded as input: a resume owes it nothing.
                return self._terminate(TerminalStatus.BLOCKED, refused)
            # What background hooks said since the last request, first: it
            # came before this prompt's own hooks (D-120). Taken only for a
            # prompt that is sent, so a refused one loses none of it.
            for event, note in [*self._background_notes(), *notes]:
                message += f"\n\nFrom a {event} hook: {note}"
        # The request records carry every message the model was sent; this is
        # for input that never reached one, which a resumed session still owes
        # an answer to.
        record: dict[str, Any] = {"text": message}
        if images:
            record["images"] = [self._block_payload(i, self._store_image) for i in images]
        if typed is None and message != user_input:
            typed = user_input
        if typed is not None and typed != message:
            record["typed"] = typed
        self._journal(EventType.USER_INPUT, record)
        self.context.append_user(message, tuple(images))
        # Whether a Stop hook has sent this task on (codex's stop_hook_active).
        continued = False

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
                if self._compact_stop:
                    # A hook's word, as codex ends the turn on it (D-118).
                    return self._terminate(TerminalStatus.BLOCKED, self._compact_stop)
                if failed:
                    self._compact_failed = True
                    self._emit(Notice(f"{failed}; carrying on without it", level="warning"))
                if self._cancelled:
                    return self._terminate(TerminalStatus.CANCELLED, "interrupted by user")
                if self.hooks is not None and self.start_sources:
                    # codex's SessionStart for a compaction, before the next
                    # request: its notes go to the work in hand (D-118).
                    notes, stops = self._start_hooks()
                    if stops is not None:
                        return self._terminate(TerminalStatus.BLOCKED, self._said(
                            "a SessionStart hook stopped the task after compaction", stops))
                    for note in notes:
                        self._hook_message(SESSION_START, note)

            if self.hooks is not None:
                # codex's safe point: what background hooks said once the
                # last request and its calls were done goes to the next
                # request (D-120).
                for event, note in self._background_notes():
                    self._hook_message(event, note)

            if self._goal_wrap_up and self.goal is not None:
                # The goal's budget ran out on the last response (D-127): the
                # model hears it before the next, as codex steers the turn.
                self._goal_wrap_up = False
                steer = budget_prompt(self.goal_now())
                self._journal(EventType.USER_INPUT, {"text": steer, "typed": BUDGET_TURN})
                self.context.append_user(steer, ())

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
            except QuotaExceeded as exc:
                # Blocked, not failed: nothing about the task went wrong, and
                # the same task goes on once the account has quota again.
                self._emit(ErrorEvent(_explain(exc), category=exc.category, fatal=True))
                return self._terminate(TerminalStatus.BLOCKED, _explain(exc))
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
            self._goal_spent(turn.usage)

            if not turn.tool_calls:
                self._emit(TurnComplete(turn_index, turn.text))
                again = self._stop_hooks(turn.text, continued) if self.hooks is not None else ""
                if not again:
                    return TurnOutcome(status=None, text=turn.text)
                continued = True
                message = f"From a {STOP} hook: {again}"
                self._journal(EventType.USER_INPUT, {"text": message, "hook": STOP})
                self.context.append_user(message, ())
                continue

            for call in turn.tool_calls:
                # Before each call, not only after one: a cancel that came
                # while the model was answering (from foundry mcp-server's
                # client, D-103) stops the task before its first call runs.
                if self._cancelled:
                    return self._terminate(TerminalStatus.CANCELLED, "interrupted by user")
                self.budget.calls += 1
                # Checked per call, not once per round: a backend returning
                # thousands of tool calls in a single turn executed all of them
                # while the limit was only inspected at the top of the loop.
                over = self.budget.check(self.context.session_usage.total)
                if over:
                    return self._terminate(TerminalStatus.PARTIAL, over)
                self._dispatch(call)
                if self._finish is not None:
                    again = (self._stop_hooks(self._finish.summary or turn.text, continued)
                             if self.hooks is not None else "")
                    if not again:
                        return self._finalize(self._finish)
                    # The task goes on: the call is answered, as one refused.
                    continued = True
                    self._finish = None
                    self._reply(call, f"finish was not accepted: a {STOP} hook sent the task "
                                f"on:\n{again}", is_error=True)

    def compact(self, *, auto: bool, focus: str = "") -> str:
        """Replace the history with the user's messages and the model's summary
        of the work (D-040). Returns why it did not, or "" when it did.

        The summary request carries the same tools as any other, since a
        history holding tool calls is refused without them, and says not to
        use them; a call it makes anyway is ignored. Files read before are
        read again before a patch: their contents are no longer in context.
        """
        self._compact_stop = ""
        if not self.context.compactable():
            return "nothing to compact yet"
        trigger = "auto" if auto else "manual"
        verdict = self._compact_hooks(PRE_COMPACT, trigger)
        if verdict is not None and verdict.stops:
            self._compact_stop = self._said("a PreCompact hook stopped compaction",
                                            verdict.stops)
            return self._compact_stop
        before = self.context.estimated_context_tokens()
        self._emit(Notice("compacting the conversation to free context", level="info"))
        prompt = (self.compact_prompt or builtin_prompt("compact")).strip()
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
        self._goal_spent(turn.usage)
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
        if self.hooks is not None:
            # Owed before the next request, whatever PostCompact says.
            self.start_sources.append("compact")
        verdict = self._compact_hooks(POST_COMPACT, trigger)
        if verdict is not None and verdict.stops:
            self._compact_stop = self._said(
                "a PostCompact hook stopped the task after compaction", verdict.stops)
            if not auto:
                self._emit(Notice(self._compact_stop, level="warning"))
        return ""

    def _compact_hooks(self, event: str, trigger: str) -> HookVerdict | None:
        """PreCompact or PostCompact hooks (D-118), told what started the
        compaction and not the permission mode, as codex's."""
        if self.hooks is None:
            return None
        return self._run_hooks(event, (trigger,), omit=("permission_mode",), trigger=trigger)

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

    def aside(self) -> tuple:
        """Where the conversation is before a side question (/side, D-129):
        what going back to it restores."""
        return (len(self.context.history), self.tool_ctx.read_tracker.snapshot(),
                self.plan, self.plan_explanation)

    def back_from_aside(self, point: tuple) -> None:
        """Back where the conversation was before the side question: the
        question and its answer dropped, journaled as a rewind is, and what
        the model had read, and its plan, as they were."""
        length, read, plan, explanation = point
        self.rewind(length)
        self.tool_ctx.read_tracker.restore(read)
        self.plan, self.plan_explanation = plan, explanation

    def _history_payload(self) -> list[dict[str, Any]]:
        return [
            {"role": m.role.value,
             "blocks": [self._block_payload(b, self._store_image) for b in m.blocks]}
            for m in self.context.history
        ]

    def _sample(self, messages: tuple[Message, ...] | None = None, purpose: str = ""):
        """One turn of the model's, asked again whole while its stream breaks
        part-way (D-099): nothing of a turn is acted on before it finishes, so
        the same request is safe to send again. The connection's own failures
        had the backend's retries before they reached here."""
        attempts = 1 + max(0, self.stream_max_retries)
        for attempt in range(1, attempts + 1):
            try:
                return self._sample_once(messages, purpose)
            except StreamDropped as exc:
                if attempt == attempts:
                    raise
                # A provider that said how long to wait is waited for (D-147).
                delay = min(max(2.0 ** (attempt - 1), exc.retry_after or 0.0), 60.0)
                self._retrying(exc, attempt + 1, attempts, delay, cut=True,
                               shown=bool(getattr(exc, "shown", "")))
                self.sleep(delay)
                # A task stopped while it waited is not asked again.
                if self._cancelled:
                    raise Cancelled("interrupted by user") from exc
        raise AssertionError("unreachable")  # pragma: no cover

    def _sample_once(self, messages: tuple[Message, ...] | None, purpose: str):
        """One try of a model request, told to the user's model-request
        hooks before and after (D-122), as codex tells them of each."""
        self._model_hooks(PRE_MODEL_REQUEST)
        try:
            finished = self._attempt(messages, purpose)
        except (Cancelled, KeyboardInterrupt):
            self._model_hooks(POST_MODEL_REQUEST, status="cancelled")
            raise
        except Exception as exc:
            self._model_hooks(POST_MODEL_REQUEST, status="failed", error=_explain(exc))
            raise
        self._model_hooks(POST_MODEL_REQUEST, status="completed", usage=finished.usage)
        return finished

    def _model_hooks(self, event: str, *, status: str = "", error: str | None = None,
                     usage: Usage | None = None) -> None:
        """The PreModelRequest or PostModelRequest hooks (D-122): told, never
        asked, with codex's fields. After a try, they run to their end even
        when a cancel ended it."""
        if self.hooks is None:
            return
        fields: dict[str, Any] = {}
        if event == POST_MODEL_REQUEST:
            fields = {"status": status, "error": error, "token_usage": None if usage is None else {
                "input_tokens": usage.input_tokens,
                "cached_input_tokens": usage.cached_input_tokens,
                "output_tokens": usage.output_tokens,
                # Not counted apart from the output here.
                "reasoning_output_tokens": 0,
                "total_tokens": usage.total}}
        self._run_hooks(event, omit=("permission_mode",), stoppable=event == PRE_MODEL_REQUEST,
                        **fields)

    def _attempt(self, messages: tuple[Message, ...] | None, purpose: str):
        request = TurnRequest(
            messages=self.context.project() if messages is None else messages,
            tools=self.registry.schemas(),
            model=self.model,
            max_output_tokens=self.max_output_tokens,
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
        try:
            for event in self.backend.stream_turn(request):
                # A cancel from another thread (D-103) ends the answer at its
                # next piece, rather than when the model is done writing it.
                if self._cancelled:
                    raise Cancelled("interrupted by user")
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
        except StreamDropped as exc:
            # What of it the user saw, so the notice can say it is not kept.
            exc.shown = "".join(text_parts) if not purpose else ""
            raise

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
        original = op
        refused = ""
        if self.hooks is not None:
            op, refused = self._pre_tool_use(call, op)

        record = {"call_id": call.call_id, "name": op.tool, "target": op.target,
                  "display": op.display}
        if op is not original:
            # What the model asked for, which a hook changed (D-115).
            record["rewritten_from"] = original.target
        # The paths a change was asked for, so a resumed session can tell the
        # files it changed itself from changes made outside it.
        if op.kind is ToolKind.MUTATOR and op.args.get("paths"):
            record["paths"] = list(op.args["paths"])
        self._journal(EventType.TOOL_CALL, record)

        if refused:
            # A hook's refusal is the pipeline's step 1, before any rule.
            self._journal(EventType.POLICY_DECISION, {
                "call_id": call.call_id, "verdict": Verdict.DENY.value, "reason": refused,
                "rule_id": HOOK_RULE, "step": 1,
                "policy_digest": self.policy.policy_digest(), "operation_digest": "",
                "mode": self.policy.mode.value, "policy_version": POLICY_VERSION,
            })
            self._audit(op, f"DENY:{HOOK_RULE}", "blocked")
            self._reply(call, f"blocked by a PreToolUse hook: {refused}", is_error=True)
            self._emit(ToolRejected(call_id=call.call_id, tool=op.tool, display=op.display,
                                    reason=refused, rule_id=HOOK_RULE, step=1))
            return

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
            allowed, denied = self._permission_request(call, op, decision)
            if denied:
                # The hook answered for the user (D-119): a denial, as theirs.
                self._journal(EventType.APPROVAL, {
                    "call_id": call.call_id, "display": op.display,
                    "choice": ApprovalChoice.DENY.value, "hook": PERMISSION_REQUEST,
                })
                self._audit(op, f"DENY:hook.{PERMISSION_REQUEST}", "declined")
                self._reply(call, f"denied by a {PERMISSION_REQUEST} hook: {denied}",
                            is_error=True)
                self._emit(ToolRejected(call_id=call.call_id, tool=op.tool, display=op.display,
                                        reason=denied, rule_id=f"hook.{PERMISSION_REQUEST}",
                                        step=decision.step))
                return
            choice = ApprovalChoice.ONCE if allowed else self._ask(op, decision)
            approval = {"call_id": call.call_id, "display": op.display, "choice": choice.value}
            if allowed:
                approval["hook"] = PERMISSION_REQUEST
                self._emit(Notice(f"{PERMISSION_REQUEST} hook approved: {op.display}"))
            self._journal(EventType.APPROVAL, approval)
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
        if output.metadata.get("agent"):
            # Which sub-agent it was, and where its own journal is (D-141).
            result["agent"] = output.metadata["agent"]
        planned = op.tool == "update_plan"
        if planned:
            # Kept with the call, so a resumed session shows the plan again.
            self.plan, self.plan_explanation = op.args["steps"], op.args["explanation"]
            result["plan"] = plan_payload(self.plan, self.plan_explanation)
        ordinal = self._journal(EventType.TOOL_RESULT, result)
        images = tuple(output.images)
        reply = content
        if self.hooks is not None:
            reply, images = self._post_tool_use(call, op, content, images)
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
        self._reply(call, reply, is_error=output.is_error, images=images)
        if planned:
            self._emit(PlanUpdated(call.call_id, self.plan, self.plan_explanation))
        self._emit(ToolEnd(call.call_id, op.tool, not output.is_error,
                           _tool_end_summary(op, output, content),
                           duration_ms=duration_ms,
                           output=(output.metadata.get("output_tail", "")
                                   or output.metadata.get("diff", ""))))

    # -- hooks (D-115) -----------------------------------------------------

    def _hook_payload(self, event: str, omit: tuple[str, ...] = (),
                      **fields: Any) -> dict[str, Any]:
        """What every hook is told, codex's names, then the event's own;
        less ``omit``, the common fields codex does not tell this event."""
        payload = {
            "session_id": self.session.session_id if self.session is not None else "",
            "transcript_path": str(self.session.path) if self.session is not None else None,
            "cwd": str(self.tool_ctx.workspace.root),
            "hook_event_name": event,
            "model": self.model,
            "turn_id": self._turn_id or str(self.context.turn),
            "permission_mode": PERMISSION_MODES.get(self.policy.mode.value,
                                                    self.policy.mode.value),
            **fields,
        }
        for name in omit:
            payload.pop(name)
        return payload

    def _run_hooks(self, event: str, names: tuple[str, ...] = (), call_id: str | None = None,
                   *, omit: tuple[str, ...] = (), stoppable: bool = True,
                   **fields: Any) -> HookVerdict | None:
        """Run the hooks of ``event`` whose matcher takes one of ``names``,
        and say what they said: to the user, the warnings and messages; to
        the journal, each run. What a hook said for the model comes back
        scrubbed, in the verdict's notes; at a call, ``call_id``, they are
        kept for its reply too. None when no hook matches. ``stoppable``
        False: a cancel already honoured does not end them as they start."""
        hooks = self.hooks.matching(event, names)
        if not hooks:
            return None
        for hook in hooks:
            if hook.status_message:
                self._emit(Notice(f"{event} hook: {hook.status_message}"))
        payload = self._hook_payload(event, omit, **fields)
        runs = self.hooks.run(hooks, scrubbed(payload, self.scrub),
                              cancelled=(lambda: self._cancelled) if stoppable else None)
        if not runs:
            # Each runs in the background: heard from later (D-120).
            return None
        verdict = READERS[event](runs)
        self._heard(event, verdict, call_id)
        return verdict

    def _heard(self, event: str, verdict: HookVerdict, call_id: str | None = None, *,
               background: bool = False) -> None:
        """Say what ``event``'s hooks said: to the user, the warnings and
        messages; to the journal, each run. Their notes are scrubbed in
        place and, at a call, kept for its reply."""
        clean = self.scrub or (lambda text: text)
        for message in verdict.messages:
            self._emit(Notice(f"{event} hook: {clean(message)}", level="warning"))
        for failure in verdict.failures:
            self._emit(Notice(f"{event} hook {clean(failure)}", level="warning"))
        verdict.context = [clean(note) for note in verdict.context]
        record: dict[str, Any] = {"event": event}
        if call_id is not None:
            self._hook_notes.setdefault(call_id, []).extend(
                (event, note) for note in verdict.context)
            record["call_id"] = call_id
        if background:
            record["async"] = True
        record["runs"] = scrubbed(verdict.records, self.scrub)
        self._journal(EventType.HOOK, record)

    def _background_notes(self) -> list[tuple[str, str]]:
        """What the background hooks that ended since said (D-120), heard
        now, at a safe point, as codex delivers it: their warnings to the
        user and their runs to the journal; their notes returned, by event."""
        if self.hooks is None:
            return []
        runs = self.hooks.finished()
        notes: list[tuple[str, str]] = []
        for event in dict.fromkeys(run.hook.event for run in runs):
            verdict = READERS[event]([run for run in runs if run.hook.event == event])
            self._heard(event, verdict, background=True)
            notes += [(event, note) for note in verdict.context]
        return notes

    def _hook_message(self, event: str, note: str) -> None:
        """A hook's note for the work in hand, as a message of its own."""
        message = f"From a {event} hook: {note}"
        self._journal(EventType.USER_INPUT, {"text": message, "hook": event})
        self.context.append_user(message, ())

    def _tool_hooks(self, event: str, call: ToolUseBlock, op: Operation,
                    **fields: Any) -> HookVerdict | None:
        """The hooks of a tool call's event, told of the call as codex
        tells them."""
        return self._run_hooks(event, matcher_names(op.tool), call.call_id,
                               tool_name=hook_tool_name(op.tool), tool_use_id=call.call_id,
                               tool_input=hook_tool_input(op.tool, op.args), **fields)

    def _said(self, what: str, reasons: list[str]) -> str:
        """``what`` a hook did, with the reasons it gave, scrubbed."""
        clean = self.scrub or (lambda text: text)
        given = [clean(reason) for reason in reasons if reason]
        return what + (": " + "; ".join(given) if given else "")

    def _start_hooks(self) -> tuple[list[str], list[str] | None]:
        """The SessionStart hooks owed (D-117, D-118), each source once, as
        codex's pending starts: their notes, and the reasons one stopped
        with, or None. A source is spent whatever its hooks said."""
        notes: list[str] = []
        while self.start_sources:
            source = self.start_sources.pop(0)
            verdict = self._run_hooks(SESSION_START, (source,), omit=("turn_id",),
                                      source=source)
            if verdict is None:
                continue
            if verdict.stops:
                return notes, verdict.stops
            notes += verdict.context
        return notes, None

    def _prompt_hooks(self, prompt: str) -> tuple[list[tuple[str, str]], str]:
        """The hooks a prompt meets before it is sent: the SessionStart
        hooks owed (D-117), then UserPromptSubmit's (D-116). Their notes, by
        event, and why one stopped it, or ""."""
        started, stops = self._start_hooks()
        if stops is not None:
            return [], self._said("a SessionStart hook stopped this prompt, so it was not sent",
                                  stops)
        notes = [(SESSION_START, note) for note in started]
        verdict = self._run_hooks(USER_PROMPT_SUBMIT, prompt=prompt)
        if verdict is None:
            return notes, ""
        if verdict.block or verdict.stops:
            return [], self._said("a UserPromptSubmit hook stopped this prompt, so it was not "
                                  "sent", verdict.block + verdict.stops)
        return notes + [(USER_PROMPT_SUBMIT, note) for note in verdict.context], ""

    def interrupt_hooks(self) -> None:
        """The Interrupt hooks (D-117): the task was stopped. Told, never
        asked; a cancel already honoured does not cut them short."""
        if self.hooks is not None:
            self._run_hooks(INTERRUPT, stoppable=False)

    def end_hooks(self) -> None:
        """The SessionEnd hooks (D-117), as the session closes, before its
        journal does. Told, never asked; a Ctrl+C meanwhile ends them, not
        the close."""
        if self.hooks is None:
            return
        try:
            self._run_hooks(SESSION_END, (SESSION_END_REASON,), stoppable=False,
                            omit=("model", "turn_id", "permission_mode"),
                            reason=SESSION_END_REASON)
        except KeyboardInterrupt:
            self._emit(Notice(f"{SESSION_END} hooks stopped", level="warning"))
        finally:
            # Background hooks still running have no one left to tell (D-120).
            self.hooks.close()

    def _stop_hooks(self, last_message: str, active: bool) -> str:
        """The Stop hooks (D-116), as the model is done: what the task goes
        on with, when one sends it on, or "". A ``continue: false`` ends it
        whatever another asked, as codex has it."""
        verdict = self._run_hooks(STOP, stop_hook_active=active,
                                  last_assistant_message=last_message or None)
        if verdict is None:
            return ""
        clean = self.scrub or (lambda text: text)
        if verdict.stops:
            for reason in verdict.stops:
                if reason:
                    self._emit(Notice(f"{STOP} hook: {clean(reason)}"))
            return ""
        if not verdict.block:
            return ""
        again = clean("\n\n".join(verdict.block))
        self._emit(Notice(f"a {STOP} hook sent the task on: {again}"))
        return again

    def _pre_tool_use(self, call: ToolUseBlock, op: Operation) -> tuple[Operation, str]:
        """The call the PreToolUse hooks leave, and why they refuse it, or
        "". A rewrite is validated as the model's call was and then goes
        through policy like any call: a hook can refuse or change a call,
        never let one skip a rule or a question."""
        verdict = self._tool_hooks(PRE_TOOL_USE, call, op)
        if verdict is None:
            return op, ""
        clean = self.scrub or (lambda text: text)
        if verdict.block:
            return op, clean("; ".join(verdict.block))
        rewrites = []
        for rewrite in verdict.rewrites:
            if rewrite not in rewrites:
                rewrites.append(rewrite)
        if len(rewrites) > 1:
            return op, "two PreToolUse hooks rewrote this call differently, so it was not run"
        if not rewrites:
            return op, ""
        args = rewritten_args(op.tool, op.args, rewrites[0])
        if args is None:
            self._emit(Notice(f"{PRE_TOOL_USE} hook returned an updatedInput that is not "
                              "this tool's input (an object; a string command for a command "
                              "or a patch); the call runs as the model made it",
                              level="warning"))
            return op, ""
        try:
            changed = self._canonicalize_paths(self.registry.get(op.tool).validate(args))
        except (InvalidToolCall, ValueError) as exc:
            self._emit(Notice(f"{PRE_TOOL_USE} hook's rewrite is not a valid call "
                              f"({clean(str(exc))}); the call runs as the model made it",
                              level="warning"))
            return op, ""
        if changed == op:
            return op, ""
        self._hook_notes.setdefault(call.call_id, []).append(
            (PRE_TOOL_USE, f"this call was changed before it ran; it ran as: {changed.display}"))
        return changed, ""

    def _permission_request(self, call: ToolUseBlock, op: Operation,
                            decision: Decision) -> tuple[bool, str]:
        """What the PermissionRequest hooks (D-119) say of a call policy
        would ask the user about: whether one allowed it, and why one denied
        it, which wins, or "". Neither: the user is asked. They run only
        where someone would be asked; an unattended run asks no one."""
        if self.hooks is None or self.approval is None:
            return False, ""
        tool_input = hook_tool_input(op.tool, op.args)
        tool_input.setdefault("description", decision.reason)
        verdict = self._run_hooks(PERMISSION_REQUEST, matcher_names(op.tool), call.call_id,
                                  tool_name=hook_tool_name(op.tool), tool_input=tool_input)
        if verdict is None:
            return False, ""
        if verdict.block:
            # Scrubbed where it goes: the reply, the journal, the event.
            return False, "; ".join(verdict.block)
        return verdict.allow, ""

    def _post_tool_use(self, call: ToolUseBlock, op: Operation, content: str,
                       images: tuple) -> tuple[str, tuple]:
        """The result the model is given once the PostToolUse hooks have
        seen it: their feedback in its place when one blocks it."""
        verdict = self._tool_hooks(POST_TOOL_USE, call, op, tool_response=content)
        if verdict is None or not verdict.block:
            return content, images
        clean = self.scrub or (lambda text: text)
        return (f"The call ran; a {POST_TOOL_USE} hook gave this in place of its result:\n"
                + clean("\n\n".join(verdict.block))), ()

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
        # What a hook said for the model about this call, however it ended.
        for event, note in self._hook_notes.pop(call.call_id, ()):
            content += f"\n\nFrom a {event} hook: {note}"
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
        self._notify_approval(op, decision)
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
            # A second Ctrl+C while they run still ends the session (D-062).
            self.interrupt_hooks()
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
