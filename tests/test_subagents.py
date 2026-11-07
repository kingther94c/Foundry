"""Sub-agents (D-141), as codex's spawn_agent: the model starts one on a task
the user asked to have done apart; it works in a conversation of its own,
under the session's rules, and only its report comes back.
"""

from __future__ import annotations

import json

import pytest

from foundry.cli.agents import BRIEF, READ_ONLY_BRIEF, SUBAGENT_READ_ONLY
from foundry.core.errors import InvalidToolCall
from foundry.core.events import ApprovalChoice
from foundry.core.policy.engine import Mode, PolicyEngine
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.agent import MAX_TASK_CHARS, SpawnAgent

from test_resume import PATCH_A, _wire, call, repo, turn  # noqa: F401  (fixtures)
from test_slash_commands import _repl


def _report(text: str = "A is 1, in a.py"):
    return turn(call("f9", "finish", {"status": "completed", "summary": text, "claims": []}))


def _spawn(task="Find where A is set, and say its value.", **more):
    return turn(call("s1", "spawn_agent", {"task": task, **more}))


def _results(wiring) -> dict:
    """The session's tool results, by call id."""
    return {r.payload["call_id"]: r.payload
            for r in SessionStore.read_records(wiring.session.path)
            if r.type == EventType.TOOL_RESULT}


def _replies(request) -> dict:
    """The tool results a request sends back, by call id."""
    return {block.call_id: block for message in request.messages for block in message.blocks
            if getattr(block, "call_id", None) and hasattr(block, "is_error")}


def _agents(wiring) -> list:
    folder = wiring.session.dir / "agents"
    return sorted(folder.iterdir()) if folder.is_dir() else []


# --- the tool -----------------------------------------------------------------


@pytest.mark.parametrize("args, said", [
    ({}, "task must be the sub-agent's task"),
    ({"task": "  "}, "task must be the sub-agent's task"),
    ({"task": "x" * (MAX_TASK_CHARS + 1)}, "at most"),
    ({"task": "t", "read_only": "no"}, "read_only must be true or false"),
    ({"task": "t", "model": "gpt-5"}, "unknown argument(s): model"),
])
def test_refused(args, said):
    with pytest.raises(InvalidToolCall, match=said.replace("(", r"\(").replace(")", r"\)")):
        SpawnAgent(spawner=None).validate(args)


def test_read_only_by_default_and_a_writer_is_a_mutator():
    tool = SpawnAgent(spawner=None)
    reader = tool.validate({"task": "look\nmore"})
    assert reader.kind.value == "read_only" and reader.args["read_only"] is True
    assert reader.display == "spawn_agent: a read-only sub-agent: look"
    writer = tool.validate({"task": "fix it", "read_only": False})
    assert writer.kind.value == "mutator"
    assert "may change files" in writer.display


# --- a read-only one ------------------------------------------------------------


def test_a_read_only_one_is_started_unasked_even_by_exec(repo, tmp_path):
    asked = []
    wiring = _wire(repo, tmp_path / "home", [
        _spawn(), turn(call("r1", "read_file", {"path": "a.py"})), _report(),
        turn(text="ok")], approve=None)
    wiring.runtime.approval = lambda request: asked.append(request.display)
    wiring.runtime.policy.interactive = False          # as exec's is
    wiring.runtime.run_turn("go")
    assert asked == [] and len(_agents(wiring)) == 1
    assert _results(wiring)["s1"]["is_error"] is False
    # One that may change files is not.
    assert not PolicyEngine(interactive=False).evaluate(SpawnAgent(spawner=None).validate(
        {"task": "t", "read_only": False}))[0].verdict.value == "allow"


def test_it_works_apart_and_only_its_report_comes_back(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        _spawn(),
        turn(call("r1", "read_file", {"path": "a.py"})),
        _report(),
        turn(text="A is 1."),
    ])
    _repl(wiring, "use a sub-agent to find A")
    seen = wiring.runtime.backend.seen
    child_first = seen[1]
    # Its own conversation: the session's system prompt, then its task alone.
    assert child_first.messages[0].role.value == "system"
    assert child_first.messages[0] == seen[0].messages[0]
    [asked] = child_first.messages[1:]
    text = asked.blocks[0].text
    assert text.startswith(BRIEF + READ_ONLY_BRIEF)
    assert text.endswith("Find where A is set, and say its value.")
    assert "use a sub-agent" not in json.dumps(
        [b.text for m in child_first.messages for b in m.blocks if hasattr(b, "text")])
    # What comes back is the report; what it read stays with it.
    reply = _replies(seen[3])["s1"]
    assert reply.content.startswith("sub-agent 1 (read-only) ended: completed")
    assert "A is 1, in a.py" in reply.content and not reply.is_error
    assert "r1" not in _replies(seen[3])
    # Its tokens are the session's too: two turns of its own, two of the session's.
    assert wiring.runtime.context.session_usage.total == 4 * 15


def test_it_changes_nothing(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        _spawn(task="Set A to 2."),
        turn(call("p1", "apply_patch", {"patch": PATCH_A})),
        turn(call("c1", "run_command", {"command": "python -V"})),
        _report("I could not: I am read-only"),
        turn(text="It could not."),
    ])
    _repl(wiring, "have a sub-agent set A to 2")
    child_turn = wiring.runtime.backend.seen[3]
    replies = _replies(child_turn)
    assert replies["p1"].is_error and SUBAGENT_READ_ONLY in replies["p1"].content
    assert replies["c1"].is_error and SUBAGENT_READ_ONLY in replies["c1"].content
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    # The session's own policy is as it was.
    assert wiring.runtime.policy.read_only == ""


def test_the_session_changes_files_again_once_it_is_done(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        _spawn(), _report(),
        turn(call("r0", "read_file", {"path": "a.py"})),
        turn(call("p1", "apply_patch", {"patch": PATCH_A})),
        turn(text="Set."),
    ])
    # In the same task: the REPL's own reset between tasks is not what frees it.
    wiring.runtime.run_turn("find A with a sub-agent, then set it to 2")
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"
    assert wiring.runtime.policy.read_only == ""


def test_it_starts_none_of_its_own(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        _spawn(),
        turn(call("s2", "spawn_agent", {"task": "deeper"})),
        _report(),
        turn(text="done"),
    ])
    _repl(wiring, "go")
    reply = _replies(wiring.runtime.backend.seen[2])["s2"]
    assert reply.is_error and "spawn_agent" in reply.content
    assert len(_agents(wiring)) == 1
    names = {tool["name"] if isinstance(tool, dict) else tool.name
             for tool in wiring.runtime.backend.seen[1].tools}
    assert "spawn_agent" not in names and "read_file" in names


# --- one that may change files --------------------------------------------------


def test_one_that_may_change_files_is_asked_for_and_asks_as_the_session_does(repo, tmp_path):
    asked = []
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("r0", "read_file", {"path": "a.py"})),
        _spawn(task="Set A to 2.", read_only=False),
        turn(call("r1", "read_file", {"path": "a.py"})),
        turn(call("p1", "apply_patch", {"patch": PATCH_A})),
        _report("A is 2 now"),
        turn(text="Done."),
    ], approve=None)

    def approve(request):
        asked.append(request.display)
        return ApprovalChoice.ONCE

    wiring.runtime.approval = approve
    _repl(wiring, "have a sub-agent set A to 2")
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"
    told = wiring.runtime.backend.seen[2].messages[1].blocks[0].text
    assert told.startswith(BRIEF) and READ_ONLY_BRIEF not in told
    assert asked[0].startswith("spawn_agent: a sub-agent that may change files")
    assert asked[1].startswith("agent 1: ")
    # What the session read before is read again before it patches.
    assert not wiring.runtime.tool_ctx.read_tracker.snapshot()


@pytest.mark.parametrize("mode", [Mode.PLAN, Mode.DONT_ASK])
def test_one_that_may_change_files_is_refused_where_changes_are(repo, tmp_path, mode):
    wiring = _wire(repo, tmp_path / "home", [
        _spawn(task="Set A to 2.", read_only=False),
        turn(text="It was refused."),
    ], approve=None)
    wiring.runtime.policy.mode = mode
    _repl(wiring, "go")
    reply = _replies(wiring.runtime.backend.seen[1])["s1"]
    assert reply.is_error and not _agents(wiring)


def test_a_read_only_task_s_sub_agent_is_read_only(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        _spawn(task="Set A to 2."),
        turn(call("p1", "apply_patch", {"patch": PATCH_A})),
        _report(),
        turn(text="ok"),
    ])
    # As foundry review's task is (D-131): the REPL sets its own per task.
    wiring.runtime.policy.read_only = "a review changes nothing"
    wiring.runtime.run_turn("go")
    reply = _replies(wiring.runtime.backend.seen[2])["p1"]
    assert reply.is_error and "a review changes nothing" in reply.content
    assert wiring.runtime.policy.read_only == "a review changes nothing"


# --- what is kept, and shown ----------------------------------------------------


def test_its_journal_is_the_session_s(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [_spawn(), _report(), turn(text="ok")])
    _repl(wiring, "go")
    [folder] = _agents(wiring)
    records = list(SessionStore.read_records(folder / "events.jsonl"))
    [started] = [r.payload for r in records if r.type == "subagent"]
    # Its own journal says where the work started, as a session's does.
    assert [r.type for r in records][1:3] == ["git_baseline", "subagent"]
    assert started == {"parent": wiring.session.session_id, "number": 1, "read_only": True,
                       "task": "Find where A is set, and say its value."}
    assert SessionStore.terminal_status(folder / "events.jsonl").value == "completed"
    # Not a session of its own.
    assert sorted(p.name for p in (tmp_path / "home" / "sessions").iterdir()) == [
        wiring.session.session_id]
    agent = _results(wiring)["s1"]["agent"]
    assert agent == {"number": 1, "session": folder.name, "status": "completed", "tokens": 15}
    # What it spent is the session's, in the session's journal: a resume counts it.
    spent = [r.payload for r in SessionStore.read_records(wiring.session.path)
             if r.type == EventType.TOKEN_USAGE and "agent" in r.payload]
    assert spent == [{"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 0,
                      "agent": 1}]


def test_what_it_does_is_shown_as_its_own(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        _spawn(), turn(call("r1", "read_file", {"path": "a.py"})), _report(),
        turn(text="ok")])
    _repl(wiring, "go")
    shown = wiring.renderer.console.file.getvalue()
    assert "agent 1: read a.py" in shown


def test_numbered_in_the_session(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        _spawn(), _report("one"),
        turn(call("s2", "spawn_agent", {"task": "again"})), _report("two"),
        turn(text="ok")])
    _repl(wiring, "go")
    assert _replies(wiring.runtime.backend.seen[4])["s2"].content.startswith("sub-agent 2 ")
    assert len(_agents(wiring)) == 2


def test_stopped_with_the_session_s_task(repo, tmp_path):
    def interrupt():
        raise KeyboardInterrupt

    wiring = _wire(repo, tmp_path / "home", [_spawn(), interrupt, turn(text="later")])
    _repl(wiring, "go")
    [folder] = _agents(wiring)
    assert SessionStore.terminal_status(folder / "events.jsonl").value == "cancelled"
    assert wiring.runtime.policy.read_only == "" and wiring.runtime.child is None


def test_a_cancel_while_it_works_stops_it(repo, tmp_path):
    def cancel_then_read():
        wiring.runtime.cancel()          # as Ctrl+C does, from the REPL
        return turn(call("r1", "read_file", {"path": "a.py"}))

    wiring = _wire(repo, tmp_path / "home", [_spawn(), cancel_then_read, _report(),
                                             turn(text="ok")])
    wiring.runtime.run_turn("go")
    [folder] = _agents(wiring)
    assert SessionStore.terminal_status(folder / "events.jsonl").value == "cancelled"


def test_what_it_spends_is_the_goal_s_but_the_goal_is_not_its(repo, tmp_path):
    from foundry.core.goal import Goal

    wiring = _wire(repo, tmp_path / "home", [_spawn(), _report(), turn(text="ok")])
    wiring.runtime.change_goal(Goal("find A"), by="user")
    wiring.runtime.run_turn("go")
    seen = wiring.runtime.backend.seen
    assert "update_goal" in {tool.name for tool in seen[0].tools}
    assert not {tool.name for tool in seen[1].tools} & {"get_goal", "update_goal"}
    assert wiring.runtime.goal.tokens_used == 3 * 15


def test_a_cancel_reaches_it():
    from foundry.core.runtime import AgentRuntime

    cancelled = []

    class Child:
        def cancel(self):
            cancelled.append(True)

    parent = AgentRuntime.__new__(AgentRuntime)
    parent._cancelled, parent.child = False, Child()
    AgentRuntime.cancel(parent)
    assert parent._cancelled and cancelled == [True]


def test_retries_are_told_as_the_session_s_again(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [_spawn(), _report(), turn(text="ok")])
    backend = wiring.runtime.backend
    backend.on_retry = wiring.runtime._retrying
    _repl(wiring, "go")
    assert backend.on_retry == wiring.runtime._retrying


def test_off_when_the_user_says_so(repo, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("[runtime]\nsubagents = false\n", encoding="utf-8")
    wiring = _wire(repo, home, [])
    assert "spawn_agent" not in wiring.runtime.registry.names()
    on = _wire(repo, tmp_path / "other", [])
    assert "spawn_agent" in on.runtime.registry.names()
