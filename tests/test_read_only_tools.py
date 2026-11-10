"""A read-only task is not offered the tools it may not use (D-148).

A review, `/side`, `/recap` and a read-only sub-agent refuse every edit and
every command whatever its arguments, but their requests still offered
`apply_patch` and `run_command`. A model played behind a local relay (the
roadmap's P1) said so: a read-only sub-agent's tool list "still offered
apply_patch and run_command", which pulled against "change nothing and run no
command", and a reviewer weighed whether to try a test run that could only be
refused. Now those requests leave out every tool whose kind is a mutator --
the built-in ones and an MCP server's that is not read-only -- and a call to
one anyway is still refused by the policy, not taken for an unknown tool.
"""

from __future__ import annotations

from foundry.cli.agents import SUBAGENT_READ_ONLY

from test_resume import PATCH_A, _wire, call, repo, turn  # noqa: F401  (repo is a fixture)
from test_slash_commands import _repl

REFUSED = {"apply_patch", "run_command"}


def _offered(request) -> set[str]:
    return {schema.name for schema in request.tools}


def _report(text="A is 1"):
    return turn(call("f9", "finish", {"status": "completed", "summary": text, "claims": []}))


def test_a_read_only_sub_agent_is_offered_only_what_it_may_use(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("s1", "spawn_agent", {"task": "Find A."})),
        turn(call("r1", "read_file", {"path": "a.py"})),
        _report(),
        turn(text="A is 1."),
    ])
    _repl(wiring, "use a sub-agent to find A")
    parent_first, child_first, child_second, parent_after = wiring.runtime.backend.seen
    for request in (child_first, child_second):
        assert not _offered(request) & REFUSED
        assert {"read_file", "search_text", "finish"} <= _offered(request)
    # The session itself is offered everything, before and after.
    for request in (parent_first, parent_after):
        assert REFUSED <= _offered(request)


def test_one_that_may_change_files_is_offered_them(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("s1", "spawn_agent", {"task": "Set A to 2.", "read_only": False})),
        _report("left as it was"),
        turn(text="done"),
    ])
    _repl(wiring, "have a sub-agent set A to 2")
    assert REFUSED <= _offered(wiring.runtime.backend.seen[1])


def test_a_call_to_a_tool_left_out_is_still_refused_by_the_policy(repo, tmp_path):
    """A model may call one it saw earlier in the conversation, or none."""
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("s1", "spawn_agent", {"task": "Set A to 2."})),
        turn(call("p1", "apply_patch", {"patch": PATCH_A})),
        _report("I could not"),
        turn(text="It could not."),
    ])
    _repl(wiring, "have a sub-agent set A to 2")
    [refused] = [b for m in wiring.runtime.backend.seen[2].messages for b in m.blocks
                 if getattr(b, "call_id", None) == "p1" and hasattr(b, "is_error")]
    assert refused.is_error and SUBAGENT_READ_ONLY in refused.content
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"


def test_a_side_question_is_offered_only_reads_and_the_next_task_everything(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="yes"), turn(text="ok")])
    _repl(wiring, "/side is a.py Python?", "go on")
    side, after = wiring.runtime.backend.seen
    assert not _offered(side) & REFUSED and "read_file" in _offered(side)
    assert REFUSED <= _offered(after)


def test_a_review_is_offered_only_reads(repo, tmp_path):
    (repo / "a.py").write_text("A = 3\n", encoding="utf-8")
    wiring = _wire(repo, tmp_path / "home", [_report("A changed to 3"), turn(text="ok")])
    _repl(wiring, "/review", "and now?")
    review, after = wiring.runtime.backend.seen
    assert not _offered(review) & REFUSED and "git_diff" in _offered(review)
    assert REFUSED <= _offered(after)


def test_plan_mode_is_still_offered_them(repo, tmp_path):
    """Plan mode refuses edits until the plan is approved, then makes them: the
    model plans with the tools it will use. Only a task that is read-only to
    its end leaves them out."""
    from foundry.core.policy.engine import Mode

    wiring = _wire(repo, tmp_path / "home", [turn(text="the plan")])
    wiring.runtime.policy.mode = Mode.PLAN
    wiring.runtime.run_turn("plan setting A to 2")
    assert REFUSED <= _offered(wiring.runtime.backend.seen[0])
