"""/side and /btw (D-129): a question on the side, as codex's side
conversation in an ephemeral fork.

It is answered from the conversation so far, read-only, and then dropped:
the next message goes on from where the conversation was, with what the
model had read and its plan as they were, and what was owed to the next
message -- a note, an image -- still owed to it.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from foundry.cli import repl
from foundry.cli.wiring import _close
from foundry.core.events import TerminalStatus
from foundry.core.resume import read_journal
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.base import ReadTracker

from test_resume import PATCH_A, _wire, call, repo, turn  # noqa: F401  (repo is a fixture)
from test_slash_commands import _repl, _shown

QUESTION = "The user asks a question on the side."


def _texts(request) -> list[str]:
    return [m.text_content for m in request.messages[1:]]


def test_the_answer_is_dropped_and_the_conversation_goes_on(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(text="A is 1"),
        turn(text="it is a Python file"),
        turn(text="B is 1"),
    ])
    shown = _shown(_repl(wiring, "what is A?", "/side what kind of file is a.py?", "and B?"))
    seen = wiring.runtime.backend.seen
    side = _texts(seen[1])
    assert side[:2] == ["what is A?", "A is 1"]
    assert side[2].startswith(QUESTION) and side[2].endswith(
        "The question: what kind of file is a.py?")
    assert _texts(seen[2]) == ["what is A?", "A is 1", "and B?"]
    assert "back where the conversation was" in shown
    rewound = [r.payload for r in SessionStore.read_records(wiring.session.path)
               if r.type == EventType.REWOUND]
    assert [r["keep"] for r in rewound] == [2]
    typed = [r.payload.get("typed") for r in SessionStore.read_records(wiring.session.path)
             if r.type == EventType.USER_INPUT]
    assert typed == [None, "/side what kind of file is a.py?", None]


def test_btw_is_the_same(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="yes"), turn(text="ok")])
    _repl(wiring, "/btw is a.py Python?", "go on")
    seen = wiring.runtime.backend.seen
    assert _texts(seen[0])[0].startswith(QUESTION)
    assert _texts(seen[1]) == ["go on"]


def test_nothing_is_changed_on_the_side(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("r1", "read_file", {"path": "a.py"})),
        turn(call("p1", "apply_patch", {"patch": PATCH_A})),
        turn(text="I could not change it"),
    ])
    _repl(wiring, "/side could a.py say A = 2?")
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    refused = wiring.runtime.backend.seen[2].messages[-1].blocks[0]
    assert refused.is_error and "a side question changes nothing" in refused.content
    assert wiring.runtime.policy.read_only == ""


def test_what_the_model_had_read_is_kept_and_what_it_read_aside_is_not(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("r1", "read_file", {"path": "a.py"})),
        turn(text="A is 1"),
        turn(call("r2", "read_file", {"path": "b.py"})),
        turn(text="B is 1"),
    ])
    _repl(wiring, "what is A?", "/side and B?")
    tracker = wiring.runtime.tool_ctx.read_tracker
    assert tracker.has_read("a.py") and not tracker.has_read("b.py")


def test_a_restored_tracker_holds_the_snapshot_s_reads_alone():
    tracker = ReadTracker()
    tracker.record("a.py")
    snapshot = tracker.snapshot()
    tracker.record("b.py")
    tracker.restore(snapshot)
    assert tracker.has_read("a.py") and not tracker.has_read("b.py")


def test_the_plan_is_kept(repo, tmp_path):
    from test_plan import plan
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("u1", "update_plan", plan(("step one", "in_progress")))),
        turn(text="planned"),
        turn(call("u2", "update_plan", plan(("something else", "pending")))),
        turn(text="answered"),
    ])
    _repl(wiring, "plan it", "/side what else?")
    assert [s.step for s in wiring.runtime.plan] == ["step one"]


def test_a_note_and_images_wait_for_the_next_message(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="aside"), turn(text="main")])
    wiring.note = "a note for the next message"
    wiring.images.append("an image")
    seen = wiring.runtime.backend.seen
    _repl(wiring, "/side what?")
    assert "a note for the next message" not in _texts(seen[0])[-1]
    assert wiring.note == "a note for the next message" and wiring.images == ["an image"]


def test_it_is_not_compacted_meanwhile_and_compaction_comes_back(repo, tmp_path, monkeypatch):
    wiring = _wire(repo, tmp_path / "home", [turn(text="aside")])
    seen_auto: list[bool] = []
    real = type(wiring.runtime).run_turn

    def run_turn(self, *args, **kwargs):
        seen_auto.append(self.auto_compact)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(type(wiring.runtime), "run_turn", run_turn)
    _repl(wiring, "/side what?")
    assert seen_auto == [False] and wiring.runtime.auto_compact is True


def test_a_stopped_side_question_goes_back_too(repo, tmp_path):
    def interrupt():
        raise KeyboardInterrupt

    wiring = _wire(repo, tmp_path / "home", [turn(text="A is 1"), interrupt])
    shown = _shown(_repl(wiring, "what is A?", "/side and B?"))
    assert len(wiring.runtime.context.history) == 2
    assert "stopped" in shown and "back where the conversation was" in shown
    assert wiring.note == ""        # nothing to tell: the question is gone


def test_it_goes_back_however_the_side_question_ends(repo, tmp_path, monkeypatch):
    wiring = _wire(repo, tmp_path / "home", [turn(text="aside")])

    def interrupted(self):
        raise KeyboardInterrupt      # Ctrl+C just as the answer came

    monkeypatch.setattr(type(wiring.runtime), "answer_left_calls", interrupted)
    console = Console(file=io.StringIO())
    with pytest.raises(KeyboardInterrupt):
        repl._ask_aside(wiring, console, repl._side(wiring, console, "what?"))
    assert wiring.runtime.context.history == []
    assert wiring.runtime.auto_compact is True
    assert EventType.REWOUND in {r.type for r in SessionStore.read_records(wiring.session.path)}
    _close(wiring, TerminalStatus.CANCELLED, "done")


def test_a_side_question_needs_a_question(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    assert "usage: /side QUESTION" in _shown(_repl(wiring, "/side  "))
    assert wiring.runtime.backend.seen == []


def test_a_resumed_session_does_not_have_it(repo, tmp_path):
    home = tmp_path / "home"
    wiring = _wire(repo, home, [turn(text="A is 1"), turn(text="aside")])
    _repl(wiring, "what is A?", "/side what?")
    state = read_journal(wiring.session.path)
    assert [m.text_content for m in state.history] == ["what is A?", "A is 1"]
    resumed = _wire(repo, home, [], resume="last")
    assert len(resumed.runtime.context.history) == 2
    _close(resumed, TerminalStatus.CANCELLED, "done")


def test_help_lists_them(repo, tmp_path):
    shown = _shown(_repl(_wire(repo, tmp_path / "home", []), "/help"))
    assert "/side QUESTION" in shown and "/btw QUESTION" in shown
