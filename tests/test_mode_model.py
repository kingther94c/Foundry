"""/mode and /model: what a session may do, and which model it runs on, chosen
again mid-session (D-047).

Only the user can change either -- the commands are read from what they type
-- and each change is journaled, told to the model with the next message,
kept by /new, and named by a resume that does not carry it over.
"""

from __future__ import annotations

import builtins

import pytest

from foundry.cli import app
from foundry.cli.repl import repl
from foundry.cli.wiring import _announce_found, _close
from foundry.core.events import ApprovalChoice, Notice, TerminalStatus
from foundry.core.policy.engine import Mode
from foundry.core.prompts import mode_line
from foundry.core.resume import read_journal
from foundry.core.session import EventType, SessionStore

from test_resume import PATCH_A, _wire, call, repo, turn  # noqa: F401  (repo is a fixture)
from test_undo import _Typist


def _session(wiring, *lines) -> _Typist:
    typist = _Typist([*lines, "/exit"])
    _close(wiring, repl(wiring, typist), "session ended")
    return typist


def _records(wiring, kind):
    return [r.payload for r in SessionStore.read_records(wiring.session.path) if r.type == kind]


def _user_text(request) -> str:
    return request.messages[-1].text_content


def _system(request) -> str:
    return request.messages[0].text_content


# --- /mode -------------------------------------------------------------------------------


def test_mode_alone_shows_the_mode_and_the_choices(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    shown = "\n".join(_session(wiring, "/mode").printed)
    assert "this session is in [bold]default[/bold] mode" in shown
    for mode in Mode:
        assert f"  {mode.value:<13} " in shown
    assert "usage: /mode NAME" in shown
    assert wiring.runtime.backend.seen == [] and not _records(wiring, EventType.MODE_CHANGE)


def test_mode_changes_the_policy_the_prompt_and_tells_the_model(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(text="ok"),
        turn(call("p1", "apply_patch", {"patch": PATCH_A})),
        turn(text="refused, as plan mode says"),
        turn(text="still planning"),
    ])
    events: list = []
    wiring.runtime.events.subscribe(events.append)
    _session(wiring, "hello", "/mode plan", "change A", "and now?")
    assert wiring.runtime.policy.mode is Mode.PLAN
    seen = wiring.runtime.backend.seen
    assert mode_line(Mode.PLAN) not in _system(seen[0])
    assert mode_line(Mode.PLAN) in _system(seen[1])
    assert _user_text(seen[1]) == (
        f"The user switched this session from default to plan mode. {mode_line(Mode.PLAN)}"
        "\n\nchange A")
    assert _user_text(seen[3]) == "and now?"                 # told once
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"   # plan mode held
    assert _records(wiring, EventType.MODE_CHANGE) == [
        {"from": "default", "to": "plan", "by": "repl", "reason": "the user typed /mode"}]
    assert any(isinstance(e, Notice) and e.text == "this session is in plan mode now; type "
               "/approve when its plan is ready" for e in events)
    assert read_journal(wiring.session.path).mode == "plan"


@pytest.mark.parametrize("typed, mode", [
    ("accept-edits", Mode.ACCEPT_EDITS), ("accept_edits", Mode.ACCEPT_EDITS),
    ("  DONT-ASK ", Mode.DONT_ASK), ("plan", Mode.PLAN),
])
def test_mode_takes_either_spelling(repo, tmp_path, typed, mode):
    wiring = _wire(repo, tmp_path / "home", [])
    _session(wiring, f"/mode {typed}")
    assert wiring.runtime.policy.mode is mode
    assert wiring.chosen == {"mode": mode}


def test_accept_edits_chosen_mid_session_lets_a_patch_apply(repo, tmp_path):
    asked: list[str] = []
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("r1", "read_file", {"path": "a.py"}),
             call("p1", "apply_patch", {"patch": PATCH_A})),
        turn(text="A is 2"),
    ])
    wiring.runtime.approval = lambda request: asked.append(request.tool) or ApprovalChoice.DENY
    _session(wiring, "/mode accept-edits", "make A 2")
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n" and asked == []


def test_an_unknown_or_unchanged_mode_changes_nothing(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="hi")])
    before = wiring.runtime.context.system_prompt
    shown = "\n".join(_session(wiring, "/mode yolo", "/mode default", "hello").printed)
    assert "there is no 'yolo' mode; the modes are default, accept_edits, plan, dont_ask" in shown
    assert "already in default mode" in shown
    assert wiring.runtime.policy.mode is Mode.DEFAULT
    assert wiring.runtime.context.system_prompt == before
    assert not _records(wiring, EventType.MODE_CHANGE) and wiring.chosen == {}
    assert _user_text(wiring.runtime.backend.seen[0]) == "hello"


def test_a_plan_entered_with_mode_is_approved_with_approve(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="the plan: A to 2"), turn(text="done")])
    _session(wiring, "/mode plan", "plan it", "/approve", "y")
    assert wiring.runtime.policy.mode is Mode.DEFAULT
    assert [r["to"] for r in _records(wiring, EventType.MODE_CHANGE)] == ["plan", "default"]


# --- /model ------------------------------------------------------------------------------


def test_model_alone_shows_the_model(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    shown = "\n".join(_session(wiring, "/model").printed)
    assert "this session uses [bold]fake[/bold] (openai_compat, profile personal)" in shown
    assert "usage: /model NAME" in shown and "--profile" in shown


def test_model_switches_the_next_request_and_leaves_the_prompt_alone(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="one"), turn(text="two"),
                                             turn(text="three")])
    events: list = []
    wiring.runtime.events.subscribe(events.append)
    _session(wiring, "hello", "/model other-model:7b", "again", "once more")
    seen = wiring.runtime.backend.seen
    assert [r.model for r in seen] == ["fake", "other-model:7b", "other-model:7b"]
    # The conversation is sent to the new model as it stands: the system
    # prompt and everything before are the same bytes.
    assert seen[1].messages[:3] == seen[0].messages + (seen[1].messages[2],)
    assert _system(seen[1]) == _system(seen[0])
    assert _user_text(seen[1]) == ("The user switched this session from the model fake to "
                                   "other-model:7b.\n\nagain")
    assert _user_text(seen[2]) == "once more"
    requests = _records(wiring, EventType.MODEL_REQUEST)
    assert [r["model"] for r in requests] == ["fake", "other-model:7b", "other-model:7b"]
    assert _records(wiring, EventType.MODEL_CHANGE) == [
        {"from": "fake", "to": "other-model:7b", "by": "repl"}]
    assert wiring.config.backend.model == "other-model:7b"
    assert wiring.chosen == {"model": "other-model:7b"}
    assert any(isinstance(e, Notice) and e.text == "this session uses other-model:7b now "
               "(it was fake)" for e in events)
    assert read_journal(wiring.session.path).switched_model == "other-model:7b"


@pytest.mark.parametrize("name", ["two words", "-flag", "x" * 201, "bell\x07", "a;b", "fake"])
def test_a_bad_or_unchanged_model_name_changes_nothing(repo, tmp_path, name):
    wiring = _wire(repo, tmp_path / "home", [])
    shown = "\n".join(_session(wiring, f"/model {name}").printed)
    assert wiring.runtime.model == "fake" and wiring.config.backend.model == "fake"
    assert not _records(wiring, EventType.MODEL_CHANGE) and wiring.note == ""
    assert ("already on fake" if name == "fake" else "a model name is letters") in shown


def test_a_model_name_may_be_as_long_as_two_hundred(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    _session(wiring, "/model us.anthropic.claude-opus-5-5:0" + "x" * 170)
    assert len(wiring.runtime.model) == 200


def test_status_shows_the_switched_model_and_mode(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    shown = "\n".join(_session(wiring, "/model m2", "/mode plan", "/status").printed)
    assert "m2 (openai_compat, profile personal)" in shown and "plan" in shown


def test_help_lists_both(repo, tmp_path):
    shown = "\n".join(_session(_wire(repo, tmp_path / "home", []), "/help").printed)
    assert "/mode [NAME]" in shown and "/model [NAME]" in shown


# --- across sessions ---------------------------------------------------------------------


def test_new_keeps_the_model_and_mode_chosen(repo, tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    home.mkdir()
    (home / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "fake"\nstream = false\n',
        encoding="utf-8")
    lines = iter(["/model second", "/mode plan", "/new", "/status", "/new", "/status", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda *a: next(lines))
    assert app.main(["run", "--workspace", str(repo)]) == 13
    headers = [next(iter(SessionStore.read_records(j))).payload["model"]
               for j in sorted((home / "sessions").glob("*/events.jsonl"),
                               key=lambda p: p.stat().st_mtime)]
    assert headers == ["fake", "second", "second"]       # and again after a second /new
    after = capsys.readouterr().out.split("new session")[-1]
    assert "second (openai_compat" in after and "plan" in after


def test_a_resume_names_the_model_it_does_not_carry_over(repo, tmp_path):
    home = tmp_path / "home"
    wiring = _wire(repo, home, [turn(text="hi")])
    _session(wiring, "/model second", "hello")
    resumed = _wire(repo, home, [], resume="last")
    events: list = []
    resumed.runtime.events.subscribe(events.append)
    _announce_found(resumed)
    assert resumed.runtime.model == "fake"
    assert any(isinstance(e, Notice) and e.text == (
        "it had been switched to the model second before it stopped; a resume continues on "
        "fake, as configured (-m second goes on with the other)") for e in events)
    _close(resumed, TerminalStatus.CANCELLED, "session ended")


def test_a_resume_on_the_switched_model_says_nothing_of_it(repo, tmp_path):
    home = tmp_path / "home"
    wiring = _wire(repo, home, [turn(text="hi")])
    _session(wiring, "/model second", "hello")
    resumed = _wire(repo, home, [], resume="last", overrides={"model": "second"})
    events: list = []
    resumed.runtime.events.subscribe(events.append)
    _announce_found(resumed)
    assert not any(isinstance(e, Notice) and "had been switched to the model" in e.text
                   for e in events)
    _close(resumed, TerminalStatus.CANCELLED, "session ended")


def test_the_model_change_record_is_synced(tmp_path):
    from foundry.core.session import _FSYNC_TYPES
    assert EventType.MODEL_CHANGE in _FSYNC_TYPES


def test_two_changes_before_a_message_are_both_told(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="ok")])
    _session(wiring, "/mode plan", "/model m2", "go")
    text = _user_text(wiring.runtime.backend.seen[0])
    assert text.startswith("The user switched this session from default to plan mode.")
    assert text.endswith("The user switched this session from the model fake to m2.\n\ngo")
