"""A task's budget: 100 rounds and 500 tool calls (D-214).

The limits were 40 and 200 from the first runtime, set by no measurement. On
the fourth probe set a gateway model makes about one call a round -- patch,
run the step's tests, update the plan, read the module again -- and r-long,
seven steps, took 34, 38 and 41 rounds when it finished and ran out of 40 in
two runs more. codex and Claude Code set no such limit, OpenHands 500
iterations; Foundry has no default ceiling on what a task spends, so these
two stay what bounds an unattended run. When they run out, the model still
gets one last request to wind up (D-197).
"""

from __future__ import annotations

import pytest

from foundry.core.config import Config, load_config
from foundry.core.errors import ConfigError
from foundry.core.events import TerminalStatus
from foundry.core.runtime import Budget

from test_resume import OVERRIDES, _core, _wire, call, finish, repo, turn  # noqa: F401


def _read(cid):
    return call(cid, "read_file", {"path": "x.txt"})


def _core_reading(tmp_path, turns, budget=None):
    runtime, session, repo = _core(tmp_path, turns, budget=budget)
    (repo / "x.txt").write_text("data\n", encoding="utf-8")
    return runtime, session


def _offered(request):
    return [t.name for t in request.tools]


def _answers(request):
    return {b.call_id: b for m in request.messages for b in m.blocks
            if getattr(b, "call_id", None) and hasattr(b, "is_error")}


def test_a_task_has_100_rounds_and_500_calls_unless_the_user_says_otherwise(
        repo, tmp_path):  # noqa: F811
    assert (Budget().max_tool_rounds, Budget().max_tool_calls) == (100, 500)
    assert (Config().max_tool_rounds, Config().max_tool_calls) == (100, 500)
    config = load_config(repo, home=tmp_path / "home")
    assert (config.max_tool_rounds, config.max_tool_calls) == (100, 500)

    wiring = _wire(repo, tmp_path / "home", [])
    assert (wiring.runtime.budget.max_tool_rounds, wiring.runtime.budget.max_tool_calls) == (
        100, 500)
    wiring.session.close()


def test_a_task_goes_on_past_forty_rounds(tmp_path):
    turns = [turn(_read(f"r{i}")) for i in range(45)] + [finish()]
    runtime, session = _core_reading(tmp_path, turns)

    outcome = runtime.run_turn("read it, again and again")

    assert outcome.status is TerminalStatus.COMPLETED
    seen = runtime.backend.seen
    assert len(seen) == 46
    assert all("read_file" in _offered(request) for request in seen)
    session.close()


def test_the_hundred_and_first_round_is_the_last_and_offers_only_finish(tmp_path):
    turns = [turn(_read(f"r{i}")) for i in range(100)] + [finish(status="partial")]
    runtime, session = _core_reading(tmp_path, turns)

    outcome = runtime.run_turn("read it, again and again")

    assert outcome.status is TerminalStatus.PARTIAL
    seen = runtime.backend.seen
    assert len(seen) == 101
    assert "read_file" in _offered(seen[99])
    assert _offered(seen[100]) == ["finish"]
    told = [b.text for m in seen[100].messages if m.role.value == "user"
            for b in m.blocks if hasattr(b, "text") and not hasattr(b, "call_id")][-1]
    assert "(tool-call rounds exceeded (100))" in told
    session.close()


def test_the_five_hundred_and_first_call_is_the_first_not_run(tmp_path):
    turns = ([turn(*(_read(f"r{i}.{j}") for j in range(20))) for i in range(25)]
             + [turn(_read("last.1"), _read("last.2")), finish(status="partial")])
    runtime, session = _core_reading(tmp_path, turns)

    outcome = runtime.run_turn("read it, many times at once")

    assert outcome.status is TerminalStatus.PARTIAL
    seen = runtime.backend.seen
    assert len(seen) == 27
    answers = _answers(seen[26])
    assert not answers["r24.19"].is_error
    for cid in ("last.1", "last.2"):
        assert answers[cid].is_error
        assert "(tool calls exceeded (500))" in answers[cid].content
    assert _offered(seen[26]) == ["finish"]
    session.close()


@pytest.mark.parametrize("key", ["max_tool_rounds", "max_tool_calls"])
def test_a_repository_may_lower_them_and_not_raise_them(tmp_path, key):
    workspace = tmp_path / "repo"
    (workspace / ".foundry").mkdir(parents=True)
    settings = workspace / ".foundry" / "config.toml"

    settings.write_text(f"[runtime]\n{key} = 60\n", encoding="utf-8")
    assert getattr(load_config(workspace, home=tmp_path / "home"), key) == 60

    settings.write_text(f"[runtime]\n{key} = 501\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=f"may only lower {key}"):
        load_config(workspace, home=tmp_path / "home")
