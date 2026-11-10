"""The runtime's part of a goal (D-127) and of the user's notifier (D-050):
the goal it pursues across turns and what each turn spends of it, and the
program told when a turn ends or a call waits for the user.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from foundry.core.conversation import Usage
from foundry.core.errors import ToolError
from foundry.core.events import Notice, TerminalStatus
from foundry.core.goal import ACTIVE, BUDGET_LIMITED, COMPLETE, Goal, spent
from foundry.core.goal import BLOCKED as BLOCKED_GOAL
from foundry.core.goal import notice as goal_notice
from foundry.core.goal import view as goal_view
from foundry.core.notify import APPROVAL_TYPE as NOTIFY_APPROVAL
from foundry.core.notify import EVENT_TYPE as NOTIFY_TURN
from foundry.core.notify import approval_payload
from foundry.core.notify import payload as notify_payload
from foundry.core.policy.engine import Decision
from foundry.core.session import EventType
from foundry.core.tools.base import Operation
from foundry.core.tools.goal import offer_goal_tools

if TYPE_CHECKING:
    from foundry.core.runtime_ending import TurnOutcome


class GoalAndNotices:
    """AgentRuntime's goal and notifier: a mixin of methods, no state of its
    own."""

    __slots__ = ()

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
