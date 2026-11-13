"""A repository's skills stay in it, and sub-agents spend the task's budget
(D-157).

P2's review of skills (D-113) and sub-agents (D-141) found:

* A repository's skill had to resolve inside the folder it was found in --
  `.agents/skills` -- not inside the repository: with `.agents` a junction to
  a folder elsewhere, that folder's skills were the repository's, and
  load_skill read their files.
* A skill's files were listed with `rglob`, which goes through junctions: a
  junction in a skill's folder to a drive's root walked the drive before
  the listing was cut to its 200.
* A read-only sub-agent starts without asking, exec's included, and each
  had a fresh budget of the task's limits: one turn of spawns made the
  task's 200 calls as many times over as the model liked.
* A sub-agent that may change files, stopped by Ctrl+C after a patch, left
  the session's reads standing: the session could patch the file on what it
  read before.
"""

from __future__ import annotations

import os

import pytest

from foundry.core import skills as skills_module
from foundry.core.session import EventType, SessionStore
from foundry.core.skills import SkillTool, load_skills
from foundry.core.tools.base import ToolContext

from test_resume import PATCH_A, _wire, call, repo, turn  # noqa: F401  (fixture)
from test_skills import _link, _skill
from test_slash_commands import _repl
from test_subagents import _agents, _report, _results, _spawn

pytestmark = pytest.mark.skipif(os.name != "nt", reason="junctions are Windows'")


def _ending(path) -> dict:
    [end] = [r.payload for r in SessionStore.read_records(path)
             if r.type == EventType.TERMINATION]
    return end


def _listing(skills, name) -> str:
    tool = SkillTool(skills)
    ctx = ToolContext(workspace=None, artifacts=None, read_tracker=None,
                      max_output_bytes=100_000)
    return tool.execute(tool.validate({"name": name}), ctx).content


# --- a repository's skills -----------------------------------------------------------------


@pytest.mark.parametrize("linked", [".agents", ".agents/skills"])
def test_a_repository_s_skills_through_a_link_out_of_it_are_not_used(tmp_path, linked):
    home, work = tmp_path / "home", tmp_path / "work"
    elsewhere = tmp_path / "elsewhere"
    _skill(elsewhere / "skills" if linked == ".agents" else elsewhere, "borrowed")
    link = work / linked
    link.parent.mkdir(parents=True)
    _link(link, elsewhere)
    skills = load_skills(home, work, trusted=True)
    assert skills.found == {}
    assert [why for _, why in skills.skipped] == ["it leads outside the repository"]


def test_a_link_to_a_folder_in_the_repository_is_followed(tmp_path):
    home, work = tmp_path / "home", tmp_path / "work"
    _skill(work / "tools" / "agents" / "skills", "kept")
    _link(work / ".agents", work / "tools" / "agents")
    assert sorted(load_skills(home, work, trusted=True).found) == ["kept"]


def test_a_repository_reached_through_a_link_keeps_its_skills(tmp_path):
    home, real = tmp_path / "home", tmp_path / "real"
    _skill(real / ".agents" / "skills", "kept")
    (real / ".git").mkdir()
    _link(tmp_path / "via", real)
    assert sorted(load_skills(home, tmp_path / "via", trusted=True).found) == ["kept"]
    # And from a folder in it.
    (real / "src").mkdir()
    assert sorted(load_skills(home, tmp_path / "via" / "src", trusted=True).found) == ["kept"]


def test_a_repository_that_cannot_be_placed_offers_no_skill(tmp_path, monkeypatch):
    home, work = tmp_path / "home", tmp_path / "work"
    _skill(work / ".agents" / "skills", "unplaced")
    _skill(home / "skills", "mine")
    monkeypatch.setattr(skills_module, "_resolved", lambda path: None)
    assert sorted(load_skills(home, work, trusted=True).found) == ["mine"]


def test_a_skill_s_listing_follows_no_link(tmp_path):
    home = tmp_path / "home"
    folder = _skill(home / "skills", "docs")
    (folder / "refs").mkdir()
    (folder / "refs" / "api.md").write_text("api", encoding="utf-8")
    (folder / "z.md").write_text("z", encoding="utf-8")
    (folder / ".hidden").mkdir()
    (folder / ".hidden" / "h.md").write_text("h", encoding="utf-8")
    outside = tmp_path / "outside"
    (outside / "deep").mkdir(parents=True)
    (outside / "deep" / "private.txt").write_text("private", encoding="utf-8")
    _link(folder / "linked", outside)
    listing = _listing(load_skills(home, None, trusted=False), "docs")
    assert "refs/api.md" in listing and "z.md" in listing
    assert listing.index("refs/api.md") < listing.index("z.md")
    assert "private" not in listing and "linked" not in listing and "h.md" not in listing


def test_a_skill_s_listing_is_bounded(tmp_path, monkeypatch):
    home = tmp_path / "home"
    folder = _skill(home / "skills", "many")
    for index in range(30):
        (folder / f"f{index:02}.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(skills_module, "MAX_WALKED", 10)
    listing = _listing(load_skills(home, None, trusted=False), "many")
    listed = [line for line in listing.splitlines() if line.strip().endswith(".md")]
    assert 0 < len(listed) <= 10


# --- sub-agents ----------------------------------------------------------------------------


def test_a_sub_agent_makes_only_the_calls_the_task_has_left(repo, tmp_path):
    reads = [turn(call(f"r{index}", "read_file", {"path": "a.py"})) for index in range(4)]
    wiring = _wire(repo, tmp_path / "home", [
        _spawn(), *reads,
        turn(call("r9", "read_file", {"path": "a.py"})), turn(text="never")])
    wiring.runtime.budget.max_tool_calls = 4
    wiring.runtime.run_turn("go")
    [folder] = _agents(wiring)
    end = _ending(folder / "events.jsonl")
    assert (end["status"], end["reason"]) == ("partial", "tool calls exceeded (3)")
    # What it made is the task's: the session has none left.
    assert _ending(wiring.session.path)["reason"] == "tool calls exceeded (4)"
    # Each asks once more, offered only its way to end (D-197): the sub-agent's
    # r9 is not run, and the task's own last request ends it.
    assert "r9" not in _results(wiring) and len(wiring.runtime.backend.seen) == 7
    assert "Not run: the task's budget is spent" in (folder / "events.jsonl").read_text(
        encoding="utf-8")


def test_a_turn_of_spawns_does_not_multiply_the_budget(repo, tmp_path):
    spawns = turn(*[call(f"s{index}", "spawn_agent", {"task": f"look {index}"})
                    for index in range(5)])
    wiring = _wire(repo, tmp_path / "home", [spawns, *[_report()] * 5, turn(text="ok")],
                   approve=None)
    wiring.runtime.policy.interactive = False          # as exec's is
    wiring.runtime.budget.max_tool_calls = 3
    wiring.runtime.run_turn("go")
    assert len(_agents(wiring)) == 2
    # The spawns left are not run; the task's last request (D-197) is told why.
    assert ("This task has used its budget (tool calls exceeded (3))"
            in wiring.session.path.read_text(encoding="utf-8"))


def test_a_sub_agent_has_the_tokens_the_task_has_left(repo, tmp_path):
    reads = [turn(call(f"r{index}", "read_file", {"path": "a.py"})) for index in range(3)]
    wiring = _wire(repo, tmp_path / "home", [_spawn(), *reads, turn(text="ok")])
    wiring.runtime.budget.max_tokens = 40               # each turn is 15
    wiring.runtime.run_turn("go")
    [folder] = _agents(wiring)
    assert _ending(folder / "events.jsonl")["reason"] == "token budget exceeded (25)"


def test_without_a_token_limit_a_sub_agent_has_none(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [_spawn(), _report(), turn(text="ok")])
    wiring.runtime.run_turn("go")
    [folder] = _agents(wiring)
    assert _ending(folder / "events.jsonl")["status"] == "completed"


def _interrupt():
    raise KeyboardInterrupt


def test_a_writer_stopped_part_way_leaves_no_read_standing(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("r1", "read_file", {"path": "a.py"})),
        _spawn(task="Set A to 2.", read_only=False),
        turn(call("r2", "read_file", {"path": "a.py"})),
        turn(call("p1", "apply_patch", {"patch": PATCH_A})),
        _interrupt,
        turn(text="later"),
    ])
    _repl(wiring, "read a.py, then have a sub-agent change it")
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"
    assert not wiring.runtime.tool_ctx.read_tracker.snapshot()
    # What it made before it was stopped is the task's too.
    assert wiring.runtime.budget.calls == 4


def test_a_reader_stopped_part_way_leaves_the_session_s_reads(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("r1", "read_file", {"path": "a.py"})),
        _spawn(),
        turn(call("r2", "read_file", {"path": "a.py"})),
        _interrupt,
        turn(text="later"),
    ])
    _repl(wiring, "read a.py, then have a sub-agent look")
    assert wiring.runtime.tool_ctx.read_tracker.snapshot()
