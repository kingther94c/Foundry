"""/goal (D-127): a goal the session pursues across turns, as codex's.

``/goal OBJECTIVE`` sets one and starts on it; while it is active, each time
a task ends the REPL starts the next on its own, until the model marks it
complete or blocked, its budget runs out, or the user stops it -- Ctrl+C
pauses it, as codex's. ``/goal`` alone shows it; ``pause``, ``resume``,
``clear`` and ``budget N|none`` change it.

Each function here returns the prompt of the turn to start, or None; the
REPL makes the task of it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Callable

from rich.console import Console
from rich.markup import escape

from foundry.core.events import TerminalStatus
from foundry.core.goal import (
    ACTIVE,
    BLOCKED,
    BUDGET_LIMITED,
    COMPLETE,
    HINTS,
    PAUSED,
    Goal,
    continuation_prompt,
    objective,
    token_budget,
    usage_line,
)

if TYPE_CHECKING:
    from foundry.cli.wiring import Wiring

# A goal turn that calls no tool did nothing; this many in a row block the
# goal, so a model that only talks cannot spend without end.
IDLE_TURNS = 3
NO_GOAL = "[yellow]there is no goal; /goal OBJECTIVE sets one[/yellow]"


def goal_command(wiring: "Wiring", console: Console, arguments: str,
                 ask: Callable[[Console, str], bool]) -> str | None:
    """``/goal``: ``ask`` asks the user before a goal is replaced."""
    runtime = wiring.runtime
    goal = runtime.goal_now()
    text = arguments.strip()
    if not text:
        _show(console, goal)
        return None
    word, _, rest = text.partition(" ")
    rest = rest.strip()
    action = word.lower()
    if action in ("pause", "resume", "clear") and not rest:
        if goal is None:
            console.print(NO_GOAL)
            return None
        return {"pause": _pause, "resume": _resume, "clear": _clear}[action](
            wiring, console, goal)
    if action == "budget" and rest:
        try:
            budget = token_budget(rest)
        except ValueError:
            pass        # not a budget: the words are an objective
        else:
            return _rebudget(wiring, console, goal, budget)
    budget = None
    if word == "--budget":
        value, _, text = rest.partition(" ")
        try:
            budget = token_budget(value)
        except ValueError as exc:
            console.print(f"[yellow]{escape(str(exc))}[/yellow]")
            return None
    try:
        aim = objective(text)
    except ValueError as exc:
        console.print(f"[yellow]{escape(str(exc))}[/yellow]")
        return None
    if goal is not None and goal.status != COMPLETE and not ask(
            console, f"replace the goal, which is {goal.status}?"):
        console.print("[dim]the goal is kept[/dim]")
        return None
    runtime.change_goal(Goal(aim, token_budget=budget), by="user")
    return continuation_prompt(runtime.goal_now())


def _show(console: Console, goal: Goal | None) -> None:
    if goal is None:
        console.print("[dim]no goal: /goal OBJECTIVE sets one, and the session works "
                      "toward it, turn after turn, until it is done[/dim]")
        return
    console.print(f"[bold]goal[/bold] {goal.status} ({usage_line(goal)})")
    console.print(escape(goal.objective), soft_wrap=True)
    if goal.status in HINTS:
        console.print(f"[dim]{HINTS[goal.status]}[/dim]")


def _pause(wiring: "Wiring", console: Console, goal: Goal) -> None:
    if goal.status not in (ACTIVE, BLOCKED):
        console.print(f"[dim]the goal is {goal.status}[/dim]")
        return None
    wiring.runtime.change_goal(replace(goal, status=PAUSED), by="user",
                               reason="the user paused it")
    return None


def _resume(wiring: "Wiring", console: Console, goal: Goal) -> str | None:
    if goal.status == BUDGET_LIMITED:
        console.print("[yellow]the goal has used its token budget; /goal budget N raises "
                      "it[/yellow]")
        return None
    if goal.status == COMPLETE:
        console.print("[dim]the goal is complete; /goal OBJECTIVE sets another[/dim]")
        return None
    if goal.status != ACTIVE:
        wiring.runtime.change_goal(replace(goal, status=ACTIVE), by="user")
    return continuation_prompt(wiring.runtime.goal_now())


def _clear(wiring: "Wiring", console: Console, goal: Goal) -> None:
    wiring.runtime.change_goal(None, by="user")
    return None


def _rebudget(wiring: "Wiring", console: Console, goal: Goal | None,
              budget: int | None) -> None:
    if goal is None:
        console.print("[yellow]there is no goal; /goal --budget N OBJECTIVE sets one with a "
                      "budget[/yellow]")
        return None
    changed = replace(goal, token_budget=budget)
    if changed.over_budget and changed.status in (ACTIVE, PAUSED, BLOCKED):
        changed = replace(changed, status=BUDGET_LIMITED)
    elif changed.status == BUDGET_LIMITED and not changed.over_budget:
        # Not active again on its own: a budget is not the word to go on.
        changed = replace(changed, status=PAUSED)
    wiring.runtime.change_goal(changed, by="user", reason=(
        "no token budget" if budget is None else f"a budget of {budget:,} tokens"))
    return None


@dataclass(slots=True)
class GoalTurns:
    """When a task ends with the goal active: the prompt of the next turn the
    goal starts, or None, having said why it stops."""

    idle: int = 0

    def after(self, wiring: "Wiring", outcome) -> str | None:
        runtime = wiring.runtime
        goal = runtime.goal_now()
        if goal is None or goal.status != ACTIVE:
            self.idle = 0
            return None
        if outcome.status is TerminalStatus.CANCELLED:
            # Stopped by the user: paused, as codex's interrupt pauses it.
            self.idle = 0
            runtime.change_goal(replace(goal, status=PAUSED), by="user",
                                reason="the user stopped the turn")
            return None
        why = ""
        if outcome.status in (TerminalStatus.BLOCKED, TerminalStatus.FAILED):
            why = f"the turn could not go on: {outcome.summary or outcome.text}"
        elif runtime.context.context_exhausted():
            why = "the context window is full"
        elif runtime.budget.calls:
            self.idle = 0
        elif not (outcome.text or outcome.summary).strip():
            why = "the model's answer was empty"
        else:
            self.idle += 1
            if self.idle >= IDLE_TURNS:
                why = f"{IDLE_TURNS} goal turns in a row called no tool"
        if why:
            self.idle = 0
            runtime.change_goal(replace(goal, status=BLOCKED), by="runtime", reason=why)
            return None
        return continuation_prompt(goal)
