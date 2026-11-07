"""/goal (D-127): a goal the session works toward turn after turn, as
codex's /goal.

What carries the feature: while the goal is active, each task that ends
starts the next on its own, and the goal stops -- and says why -- when the
model marks it complete or blocked, its budget runs out, the user stops a
turn, or turns go by that do nothing. What it spends is counted as codex
counts it, and a resumed session has it back, paused.
"""

from __future__ import annotations

import json

import pytest

from foundry.cli import app
from foundry.cli.exporting import conversation_markdown
from foundry.cli.goals import IDLE_TURNS
from foundry.cli.repl import STOPPED_NOTE
from foundry.core.conversation import ModelTurn, StopReason, Usage
from foundry.core.errors import ToolError, TransientError
from foundry.core.events import Notice, TerminalStatus
from foundry.core.goal import (
    BUDGET_TURN,
    GOAL_TURN,
    MAX_BUDGET,
    MAX_OBJECTIVE_CHARS,
    Goal,
    budget_prompt,
    continuation_prompt,
    duration,
    notice,
    objective,
    spent,
    token_budget,
    view,
)
from foundry.core.policy.engine import READ_ONLY_TOOLS
from foundry.core.resume import read_journal
from foundry.core.session import EventType, SessionStore

from test_resume import _core, _wire, call, repo, turn  # noqa: F401  (repo is a fixture)
from test_slash_commands import _repl, _shown

PROMPT_START = "Keep working toward the session's goal."


def spend(*calls, text: str = "", used: tuple = (10, 5, 0)) -> ModelTurn:
    return ModelTurn(text=text, tool_calls=tuple(calls), usage=Usage(*used),
                     stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN)


def _records(wiring, kind: str) -> list[dict]:
    return [r.payload for r in SessionStore.read_records(wiring.session.path) if r.type == kind]


def _goals(wiring) -> list[tuple]:
    return [(g.get("status"), g["by"]) for g in _records(wiring, EventType.GOAL)]


def _asked(wiring) -> list[str]:
    """The last user message of each request."""
    return [r.messages[-1].blocks[0].text for r in wiring.runtime.backend.seen
            if r.messages[-1].role.value == "user"]


def _notices(wiring) -> list[str]:
    said: list[str] = []
    wiring.runtime.events.subscribe(
        lambda event: said.append(event.text) if isinstance(event, Notice) else None)
    return said


def _once(error: BaseException, then: ModelTurn):
    """A turn whose request first fails with ``error``, then answers."""
    raised: list[bool] = []

    def answer():
        if not raised:
            raised.append(True)
            raise error
        return then
    return answer


# --- the goal goes on -----------------------------------------------------------------


def test_a_goal_goes_on_until_the_model_marks_it_complete(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("r1", "read_file", {"path": "a.py"})),
        turn(text="A is 1; there is more to do"),
        turn(call("g1", "update_goal", {"status": "complete"})),
        turn(text="the goal is done"),
        turn(text="never asked"),
    ])
    shown = _shown(_repl(wiring, "/goal make A 2"))
    seen = wiring.runtime.backend.seen
    assert len(seen) == 4
    asked = _asked(wiring)
    assert asked[0].startswith(PROMPT_START) and "<objective>\nmake A 2\n</objective>" in asked[0]
    # The second task, started on its own, with what the first spent.
    assert asked[1] == asked[0].replace("so far: 0;", "so far: 30;")
    assert _goals(wiring) == [("active", "user"), ("complete", "model")]
    assert [p.get("typed") for p in _records(wiring, EventType.USER_INPUT)] == [
        "/goal make A 2", GOAL_TURN]
    reply = json.loads(seen[3].messages[-1].blocks[0].content)
    assert reply["goal"]["status"] == "complete" and "report" in reply
    assert "the goal goes on (30 tokens, 0s); Ctrl+C pauses it" in shown
    exported = conversation_markdown(SessionStore.read_records(wiring.session.path))
    assert "/goal make A 2" in exported and GOAL_TURN in exported


def test_its_tools_are_offered_once_there_is_a_goal(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="hi"), turn(text="")])
    assert "update_goal" not in wiring.runtime.registry.tools
    _repl(wiring, "hello", "/goal x")
    tools = [[t.name for t in r.tools] for r in wiring.runtime.backend.seen]
    assert "get_goal" not in tools[0] and {"get_goal", "update_goal"} <= set(tools[1])
    assert {"get_goal", "update_goal"} <= set(READ_ONLY_TOOLS)


def test_ctrl_c_pauses_it_and_resume_goes_on(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        _once(KeyboardInterrupt(), turn(call("g1", "update_goal", {"status": "complete"}))),
        turn(text="done"),
    ])
    said = _notices(wiring)
    shown = _shown(_repl(wiring, "/goal fix it", "/goal", "/goal resume"))
    assert _goals(wiring) == [("active", "user"), ("paused", "user"), ("active", "user"),
                              ("complete", "model")]
    assert _records(wiring, EventType.GOAL)[1]["reason"] == "the user stopped the turn"
    assert "stopped; the session is still open" in shown
    assert ("goal paused (0 tokens, 0s): the user stopped the turn; /goal resume goes on "
            "with it") in said
    assert "goal[/bold] paused (0 tokens, 0s)" in shown
    # The interrupted request never reached the model; /goal resume's did.
    assert len(wiring.runtime.backend.seen) == 2
    (asked,) = _asked(wiring)
    assert asked.startswith(STOPPED_NOTE) and f"\n\n{PROMPT_START}" in asked


def test_a_turn_that_cannot_go_on_blocks_it(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        _once(TransientError("the gateway is down"), turn(text="never asked")),
    ])
    _repl(wiring, "/goal fix it")
    assert _goals(wiring) == [("active", "user"), ("blocked", "runtime")]
    reason = _records(wiring, EventType.GOAL)[1]["reason"]
    assert reason.startswith("the turn could not go on: ") and "the gateway is down" in reason


def test_turns_that_call_no_tool_block_it(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text=f"thinking {n}") for n in range(5)])
    _repl(wiring, "/goal fix it")
    assert len(wiring.runtime.backend.seen) == IDLE_TURNS == 3
    assert _goals(wiring) == [("active", "user"), ("blocked", "runtime")]
    assert _records(wiring, EventType.GOAL)[1]["reason"] == "3 goal turns in a row called no tool"


def test_a_turn_with_a_call_starts_the_count_again(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(text="one"), turn(text="two"),
        turn(call("r1", "read_file", {"path": "a.py"})), turn(text="read it"),
        turn(text="one"), turn(text="two"), turn(text="three"), turn(text="never asked"),
    ])
    _repl(wiring, "/goal fix it")
    assert len(wiring.runtime.backend.seen) == 7
    assert _goals(wiring)[-1] == ("blocked", "runtime")


def test_an_empty_answer_blocks_it_at_once(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="  "), turn(text="never asked")])
    _repl(wiring, "/goal fix it")
    assert len(wiring.runtime.backend.seen) == 1
    assert _records(wiring, EventType.GOAL)[-1]["reason"] == "the model's answer was empty"


def test_a_full_context_blocks_it(repo, tmp_path, monkeypatch):
    wiring = _wire(repo, tmp_path / "home", [turn(text="one"), turn(text="never asked")])
    full = iter([False, True])
    monkeypatch.setattr(type(wiring.runtime.context), "context_exhausted",
                        lambda self: next(full, True))
    _repl(wiring, "/goal fix it")
    assert _records(wiring, EventType.GOAL)[-1]["reason"] == "the context window is full"


def test_a_finished_task_does_not_end_the_goal(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("f1", "finish", {"status": "partial", "summary": "half", "claims": []})),
        turn(call("g1", "update_goal", {"status": "blocked"})),
        turn(text="blocked on the user"),
    ])
    shown = _shown(_repl(wiring, "/goal fix it"))
    assert len(wiring.runtime.backend.seen) == 3
    assert _goals(wiring) == [("active", "user"), ("blocked", "model")]
    assert "ask more" not in shown       # the task ended; the goal did not


def test_a_failed_task_blocks_it(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("f1", "finish", {"status": "failed", "summary": "no way", "claims": []})),
        turn(text="never asked"),
    ])
    _repl(wiring, "/goal fix it")
    assert len(wiring.runtime.backend.seen) == 1
    assert _records(wiring, EventType.GOAL)[-1]["reason"] == "the turn could not go on: no way"


# --- what it spends ---------------------------------------------------------------------


def test_the_budget_stops_it_and_the_model_is_told_to_wrap_up(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        spend(call("r1", "read_file", {"path": "a.py"})),
        spend(call("r2", "read_file", {"path": "b.py"})),
        spend(text="wrapping up"),
        spend(text="never asked"),
    ])
    _repl(wiring, "/goal --budget 25 fix it")
    assert len(wiring.runtime.backend.seen) == 3
    assert _goals(wiring) == [("active", "user"), ("budget_limited", "budget")]
    assert _records(wiring, EventType.GOAL)[0]["token_budget"] == 25
    assert _asked(wiring)[-1].startswith(
        "The session's goal has used its token budget (30 of 25)")
    assert _records(wiring, EventType.USER_INPUT)[-1]["typed"] == BUDGET_TURN


def test_a_budget_spent_on_the_last_answer_tells_no_later_task(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [spend(text="all of it", used=(40, 5, 0)),
                                             spend(text="something else")])
    _repl(wiring, "/goal --budget 30 fix it", "and now?")
    assert _goals(wiring)[-1] == ("budget_limited", "budget")
    assert _asked(wiring)[-1] == "and now?"


def test_only_what_an_active_goal_spends_counts(tmp_path):
    runtime, _, _ = _core(tmp_path, [spend(text="a", used=(100, 20, 30)),
                                     spend(text="b", used=(100, 20, 30))])
    runtime.change_goal(Goal("x"), by="user")
    runtime.run_turn("go")
    assert runtime.goal.tokens_used == 90        # 100 in, 30 of them cached, 20 out
    runtime.change_goal(Goal("x", status="paused", tokens_used=90), by="user")
    runtime.run_turn("go on")
    assert runtime.goal.tokens_used == 90


def test_compaction_spends_too(tmp_path):
    runtime, _, _ = _core(tmp_path, [spend(text="an answer", used=(1, 1, 0)),
                                     spend(text="the summary", used=(50, 10, 0))])
    runtime.run_turn("something to summarize")
    runtime.change_goal(Goal("x"), by="user")
    assert runtime.compact(auto=False) == ""
    assert runtime.goal.tokens_used == 60


def test_the_time_spent_counts_while_it_is_active(tmp_path):
    runtime, _, _ = _core(tmp_path, [])
    now = [100.0]
    runtime.clock = lambda: now[0]
    runtime.change_goal(Goal("x"), by="user")
    now[0] = 165.5
    assert runtime.goal_now().seconds_used == 65
    runtime.change_goal(Goal("x", status="paused", seconds_used=70), by="user")
    now[0] = 500.0
    assert runtime.goal_now().seconds_used == 70


# --- the model's tools ------------------------------------------------------------------


def test_what_the_model_may_mark(tmp_path):
    runtime, _, _ = _core(tmp_path, [])
    with pytest.raises(ToolError, match="there is no goal"):
        runtime.goal_update("complete")
    runtime.change_goal(Goal("x", status="paused"), by="user")
    with pytest.raises(ToolError, match="the goal is paused, not active"):
        runtime.goal_update("blocked")
    runtime.change_goal(Goal("x", status="budget_limited", token_budget=5, tokens_used=9),
                        by="user")
    with pytest.raises(ToolError, match="budget_limited, not active"):
        runtime.goal_update("paused")
    done = json.loads(runtime.goal_update("complete"))
    assert done["goal"]["status"] == "complete" and "report" in done
    with pytest.raises(ToolError, match="already complete"):
        runtime.goal_update("complete")
    runtime.change_goal(Goal("y"), by="user")
    paused = json.loads(runtime.goal_update("paused"))
    assert paused["goal"]["status"] == "paused" and "report" not in paused
    assert runtime.goal.status == "paused"


def test_get_goal_says_what_is_left(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("g1", "get_goal", {})),
        turn(call("g2", "update_goal", {"status": "complete"})),
        turn(text="done"),
    ])
    _repl(wiring, "/goal --budget 100 fix it")
    said = json.loads(wiring.runtime.backend.seen[1].messages[-1].blocks[0].content)
    assert said == {"goal": {"objective": "fix it", "status": "active", "token_budget": 100,
                             "tokens_used": 15, "seconds_used": 0}, "remaining_tokens": 85}


def test_update_goal_takes_only_what_the_model_may_set(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("g1", "update_goal", {"status": "active"})),
        turn(call("g2", "update_goal", {"status": "complete", "why": "x"})),
        turn(text="hm"), turn(text="hm"), turn(text="hm"),
    ])
    _repl(wiring, "/goal fix it")
    replies = [r.messages[-1].blocks[0].content for r in wiring.runtime.backend.seen[1:3]]
    assert "status must be one of complete, blocked, paused" in replies[0]
    assert "unknown argument(s): why" in replies[1]
    assert _goals(wiring)[-1] == ("blocked", "runtime")


# --- /goal --------------------------------------------------------------------------------


def test_goal_alone_shows_it(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    assert "no goal: /goal OBJECTIVE sets one" in _shown(_repl(wiring, "/goal"))
    wiring = _wire(repo, tmp_path / "home2", [])
    wiring.runtime.change_goal(Goal("fix [the] parser", status="blocked", token_budget=1000,
                                    tokens_used=250, seconds_used=75), by="user")
    shown = _shown(_repl(wiring, "/goal"))
    assert "goal[/bold] blocked (250 of 1,000 tokens, 1m 15s)" in shown
    assert "fix \\[the] parser" in shown and "/goal resume tries again" in shown


@pytest.mark.parametrize("line", ["/goal pause", "/goal resume", "/goal clear"])
def test_without_a_goal_there_is_nothing_to_change(repo, tmp_path, line):
    wiring = _wire(repo, tmp_path / "home", [])
    assert "there is no goal; /goal OBJECTIVE sets one" in _shown(_repl(wiring, line))
    assert _records(wiring, EventType.GOAL) == []


def test_pause_and_clear(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime.change_goal(Goal("x", status="blocked"), by="runtime")
    shown = _shown(_repl(wiring, "/goal pause", "/goal pause", "/goal clear", "/goal"))
    assert _goals(wiring)[1:] == [("paused", "user"), (None, "user")]
    assert _records(wiring, EventType.GOAL)[-1] == {"cleared": True, "by": "user"}
    assert "the goal is paused" in shown and wiring.runtime.goal is None


@pytest.mark.parametrize("status, said", [
    ("budget_limited", "the goal has used its token budget; /goal budget N raises it"),
    ("complete", "the goal is complete; /goal OBJECTIVE sets another"),
])
def test_what_cannot_be_resumed(repo, tmp_path, status, said):
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime.change_goal(Goal("x", status=status, token_budget=5, tokens_used=9),
                               by="user")
    assert said in _shown(_repl(wiring, "/goal resume"))
    assert wiring.runtime.goal.status == status


@pytest.mark.parametrize("before, line, after", [
    (("paused", None, 50), "/goal budget 10k", ("paused", 10_000)),
    (("paused", None, 50), "/goal budget 40", ("budget_limited", 40)),
    (("blocked", 100, 50), "/goal budget 40", ("budget_limited", 40)),
    (("budget_limited", 40, 50), "/goal budget 1,000", ("paused", 1_000)),
    (("budget_limited", 40, 50), "/goal budget none", ("paused", None)),
    (("budget_limited", 40, 50), "/goal budget 45", ("budget_limited", 45)),
    (("paused", None, 50), "/goal budget 50", ("budget_limited", 50)),
    (("complete", 40, 50), "/goal budget 100", ("complete", 100)),
])
def test_its_budget_changes(repo, tmp_path, before, line, after):
    wiring = _wire(repo, tmp_path / "home", [])
    status, budget, used = before
    wiring.runtime.change_goal(Goal("x", status=status, token_budget=budget, tokens_used=used),
                               by="user")
    _repl(wiring, line)
    goal = wiring.runtime.goal
    assert (goal.status, goal.token_budget, goal.tokens_used) == (*after, 50)


def test_budget_without_a_goal_and_words_that_are_not_one(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="sure"), turn(text="")])
    shown = _shown(_repl(wiring, "/goal budget 5k", "/goal budget the trip"))
    assert "there is no goal; /goal --budget N OBJECTIVE sets one" in shown
    assert wiring.runtime.goal.objective == "budget the trip"


@pytest.mark.parametrize("line, said", [
    ("/goal --budget lots fix it", "not a token budget: 'lots'"),
    ("/goal --budget 0 fix it", "a token budget must be more than 0"),
    ("/goal --budget 5k", "a goal needs an objective"),
])
def test_a_goal_that_is_not_one_is_refused(repo, tmp_path, line, said):
    wiring = _wire(repo, tmp_path / "home", [])
    assert said in _shown(_repl(wiring, line))
    assert wiring.runtime.goal is None


def test_replacing_a_goal_asks_first(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="")])
    wiring.runtime.change_goal(Goal("old", status="paused"), by="user")
    shown = _shown(_repl(wiring, "/goal new", "n", "/goal new", "y"))
    assert "the goal is kept" in shown
    assert wiring.runtime.goal.objective == "new"
    assert len(wiring.runtime.backend.seen) == 1


def test_a_complete_goal_is_replaced_without_asking(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="")])
    wiring.runtime.change_goal(Goal("old", status="complete"), by="model")
    _repl(wiring, "/goal new")
    assert wiring.runtime.goal.objective == "new"


def test_status_and_help_show_it(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime.change_goal(Goal("fix the parser\nand its tests", status="paused",
                                    tokens_used=1234), by="user")
    shown = _shown(_repl(wiring, "/status", "/help"))
    assert "[dim]goal     [/dim] paused (1,234 tokens, 0s): fix the parser" in shown
    assert "/goal [OBJECTIVE]" in shown


# --- a resumed session --------------------------------------------------------------------


def test_a_resumed_session_has_its_goal_back_paused(repo, tmp_path):
    home = tmp_path / "home"
    wiring = _wire(repo, home, [spend(text="partly", used=(30, 7, 10))])
    wiring.runtime.change_goal(Goal("ship it", token_budget=1000, tokens_used=3), by="user")
    wiring.runtime.run_turn("go")         # as if the process were killed after it
    app._close(wiring, TerminalStatus.CANCELLED, "killed")
    resumed = _wire(repo, home, [], resume="last")
    said = _notices(resumed)
    assert resumed.runtime.goal == Goal("ship it", status="paused", token_budget=1000,
                                        tokens_used=30)
    assert "update_goal" in resumed.runtime.registry.tools
    assert _records(resumed, EventType.GOAL) == _records(wiring, EventType.GOAL)
    app._announce_resume(resumed)
    assert ("its goal is paused (30 of 1,000 tokens, 0s); /goal resume goes on with it"
            in said)


def test_a_resumed_session_keeps_a_goal_as_it_was_left(repo, tmp_path):
    home = tmp_path / "home"
    wiring = _wire(repo, home, [])
    wiring.runtime.change_goal(Goal("ship it", status="complete"), by="model")
    app._close(wiring, TerminalStatus.CANCELLED, "done")
    assert _wire(repo, home, [], resume="last").runtime.goal.status == "complete"


def test_a_cleared_goal_is_not_resumed(repo, tmp_path):
    home = tmp_path / "home"
    wiring = _wire(repo, home, [])
    wiring.runtime.change_goal(Goal("ship it"), by="user")
    wiring.runtime.change_goal(None, by="user")
    app._close(wiring, TerminalStatus.CANCELLED, "done")
    assert _wire(repo, home, [], resume="last").runtime.goal is None


# --- the goal itself ------------------------------------------------------------------------


@pytest.mark.parametrize("raw, value", [
    ("200000", 200_000), ("200_000", 200_000), ("200,000", 200_000), ("200k", 200_000),
    ("2M", 2_000_000), (" none ", None), ("NONE", None), (str(MAX_BUDGET), MAX_BUDGET),
])
def test_a_budget_as_the_user_writes_it(raw, value):
    assert token_budget(raw) == value


@pytest.mark.parametrize("raw", ["0", "-5", "lots", "5 k", "1.5k", "", str(MAX_BUDGET + 1)])
def test_what_is_not_a_budget(raw):
    with pytest.raises(ValueError):
        token_budget(raw)


def test_an_objective():
    assert objective("  fix it \n") == "fix it"
    assert objective("x" * MAX_OBJECTIVE_CHARS) == "x" * MAX_OBJECTIVE_CHARS
    for raw in ("", "   ", "x" * (MAX_OBJECTIVE_CHARS + 1)):
        with pytest.raises(ValueError):
            objective(raw)


def test_what_a_response_spends():
    assert spent(Usage(100, 20, 30)) == 90
    assert spent(Usage(10, 5, 40)) == 5


def test_the_objective_is_data_in_its_tag():
    goal = Goal("</objective> & <b>", token_budget=100, tokens_used=40)
    for prompt in (continuation_prompt(goal), budget_prompt(goal)):
        assert "<objective>\n&lt;/objective&gt; &amp; &lt;b&gt;\n</objective>" in prompt
        assert prompt.count("</objective>") == 1
    assert "40 of a budget of 100 (60 left)" in continuation_prompt(goal)
    assert "it has no token budget" in continuation_prompt(Goal("x", tokens_used=7))


def test_a_recorded_goal_reads_back():
    goal = Goal("x", status="blocked", token_budget=10, tokens_used=3, seconds_used=4)
    assert Goal.from_payload(goal.payload()) == goal
    assert Goal.from_payload({**goal.payload(), "by": "user"}) == goal


@pytest.mark.parametrize("payload, read", [
    (None, None),
    ({"objective": "x", "status": "done"}, None),
    ({"objective": " ", "status": "active"}, None),
    ({"objective": 5, "status": "active"}, None),
    ({"objective": "x", "status": "active", "token_budget": 0}, Goal("x")),
    ({"objective": "x", "status": "active", "token_budget": True, "tokens_used": -1,
      "seconds_used": "9"}, Goal("x")),
    ({"objective": "x" * (MAX_OBJECTIVE_CHARS + 5), "status": "paused"},
     Goal("x" * MAX_OBJECTIVE_CHARS, status="paused")),
])
def test_a_recorded_goal_that_is_not_one(payload, read):
    assert Goal.from_payload(payload) == read


def test_a_goal_that_is_not_active_spends_nothing_after_its_record(tmp_path):
    _, session, _ = _core(tmp_path, [])
    session.append(EventType.GOAL, {**Goal("x", tokens_used=5).payload(), "by": "user"})
    session.append(EventType.TOKEN_USAGE, {"input_tokens": 20, "output_tokens": 5,
                                           "cached_input_tokens": 0})
    session.append(EventType.GOAL, {**Goal("x", status="paused", tokens_used=30).payload(),
                                    "by": "user"})
    session.append(EventType.TOKEN_USAGE, {"input_tokens": 100, "output_tokens": 0})
    session.close()
    assert read_journal(session.path).goal == Goal("x", status="paused", tokens_used=30)


def test_a_goal_that_stopped_is_a_warning(tmp_path):
    runtime, _, _ = _core(tmp_path, [])
    levels: list[str] = []
    runtime.events.subscribe(lambda event: levels.append(event.level))
    for status in ("active", "paused", "blocked", "budget_limited", "complete"):
        runtime.change_goal(Goal("x", status=status), by="user")
    runtime.change_goal(None, by="user")
    assert levels == ["info", "info", "warning", "warning", "info", "info"]


def test_a_journal_s_goal(tmp_path):
    runtime, session, _ = _core(tmp_path, [spend(text="a", used=(20, 5, 0))])
    session.append(EventType.GOAL, {"objective": "x", "status": "active", "by": "user"})
    session.append(EventType.GOAL, {"objective": 7, "by": "user"})
    session.close()
    assert read_journal(session.path).goal is None


@pytest.mark.parametrize("seconds, said", [(0, "0s"), (59, "59s"), (61, "1m 1s"),
                                           (3600, "1h 0m"), (3725, "1h 2m")])
def test_durations(seconds, said):
    assert duration(seconds) == said


def test_what_the_user_is_told():
    assert notice(None) == "goal cleared"
    assert notice(Goal("fix it\nand more", tokens_used=5)) == "goal active (5 tokens, 0s): fix it"
    assert notice(Goal("x", status="blocked"), "stuck") == (
        "goal blocked (0 tokens, 0s): stuck; /goal resume tries again")
    assert notice(Goal("x", status="complete", token_budget=9, tokens_used=5)) == (
        "goal complete (5 of 9 tokens, 0s)")


def test_the_view():
    assert json.loads(view(None)) == {"goal": None}
    said = json.loads(view(Goal("x", token_budget=10, tokens_used=12), completed=True))
    assert said["remaining_tokens"] == 0 and "report" in said
