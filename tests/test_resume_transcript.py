"""A resumed session shows its conversation before the prompt comes back
(D-108), as codex's resume does: what the user typed, the model's answers,
each tool call's line.

What carries it: what the user typed is journaled beside the message it
became (``user_input.typed``) whenever the message says more -- a command's
prompt, a resume's note, an attached file -- so the conversation is shown as
it was typed, not as the model was told it; the rest is the journal's
scrubbed copy, stripped of control sequences and never read as markup.
"""

from __future__ import annotations

import builtins
import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.cli.render import Renderer
from foundry.cli.transcript import MAX_SHOWN, MAX_USER_LINES, show_conversation
from foundry.cli.wiring import _close
from foundry.core.context import SUMMARY_PREFIX
from foundry.core.conversation import (ImageBlock, Message, Role, TextBlock, ToolResultBlock,
                                       ToolUseBlock)
from foundry.core.events import TerminalStatus
from foundry.core.resume import read_journal, summarize
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.registry import default_registry

from test_resume import _wire, call, repo, turn  # noqa: F401  (repo is a fixture)
from foundry.cli.repl import run_task
from foundry.cli.slash_commands import RunTask

from test_slash_commands import _repl, _sent

END = "-- the session continues here --"


def _console():
    return Console(file=io.StringIO(), force_terminal=True, width=100, color_system=None)


def _shown(wiring) -> list[str]:
    wiring.renderer.console = _console()
    show_conversation(wiring)
    return [line.rstrip() for line in wiring.renderer.console.file.getvalue().splitlines()
            if line.strip()]


def _resumed(repo, home, sid, turns=()):
    return _wire(repo, home, list(turns), resume=sid)


def _inputs(wiring):
    return [r.payload for r in SessionStore.read_records(wiring.session.path)
            if r.type == EventType.USER_INPUT]


# --- a session, resumed ---------------------------------------------------------------


def test_the_conversation_is_shown_as_it_was_typed(repo, tmp_path):
    home = tmp_path / "home"
    wiring = _wire(repo, home, [turn(call("c1", "read_file", {"path": "a.py"})),
                                turn(text="It sets **A** to 1.")])
    _repl(wiring, "what is in @a.py?")
    assert "The user attached this file" in _sent(wiring)[0]
    resumed = _resumed(repo, home, wiring.session.session_id)
    assert _shown(resumed) == ["foundry> what is in @a.py?", "-> read a.py",
                               "It sets A to 1.", END]
    _close(resumed, TerminalStatus.CANCELLED, "test")


def test_a_command_is_shown_as_the_command(repo, tmp_path):
    home = tmp_path / "home"
    (home / "prompts").mkdir(parents=True)
    (home / "prompts" / "fix.md").write_text("Fix $1 carefully, then run the tests.\n",
                                             encoding="utf-8")
    wiring = _wire(repo, home, [turn(text="Fixed.")])
    _repl(wiring, "/fix a.py")
    assert _sent(wiring) == ["Fix a.py carefully, then run the tests."]
    resumed = _resumed(repo, home, wiring.session.session_id)
    assert _shown(resumed) == ["foundry> /fix a.py", "Fixed.", END]
    assert read_journal(resumed.session.path).first_task == "/fix a.py"
    _close(resumed, TerminalStatus.CANCELLED, "test")
    assert summarize(resumed.session.path.parent).first_task == "/fix a.py"


def test_a_resumes_note_is_not_shown(repo, tmp_path):
    home = tmp_path / "home"
    wiring = _wire(repo, home, [turn(text="Started.")])
    _repl(wiring, "start")
    sid = wiring.session.session_id
    again = _resumed(repo, home, sid, [turn(text="Going on.")])
    _repl(again, "go on")
    [note_and_task] = _sent(again)
    assert note_and_task != "go on" and note_and_task.endswith("go on")
    assert _inputs(again)[-1] == {"text": note_and_task, "typed": "go on"}
    third = _resumed(repo, home, sid)
    assert _shown(third) == ["foundry> start", "Started.", "foundry> go on", "Going on.", END]
    _close(third, TerminalStatus.CANCELLED, "test")


def test_typed_is_journaled_only_when_the_message_says_more(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="Hi.")])
    _repl(wiring, "hello")
    assert _inputs(wiring) == [{"text": "hello"}]


def test_a_message_nobody_typed_is_journaled_as_it_is(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="Hi."), turn(text="Yes.")])
    wiring.runtime.run_turn("from a program")
    run_task(wiring, RunTask("from a command's own task"))
    assert _inputs(wiring) == [{"text": "from a program"},
                               {"text": "from a command's own task"}]
    _close(wiring, TerminalStatus.CANCELLED, "test")


def test_a_task_from_the_command_line_is_journaled_as_typed(repo, tmp_path, monkeypatch):
    home = tmp_path / "home"
    wiring = _wire(repo, home, [turn(text="Started.")])
    _repl(wiring, "start")
    again = _resumed(repo, home, wiring.session.session_id, [turn(text="ok")])
    monkeypatch.setattr("foundry.cli.session_commands.build", lambda *a, **k: again)
    monkeypatch.setattr("foundry.cli.session_commands._interactive", lambda: False)
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    app.main(["resume", again.session.session_id, "carry on", "--workspace", str(repo)])
    assert _inputs(again)[-1]["typed"] == "carry on"


# --- where it is shown ----------------------------------------------------------------


@pytest.mark.parametrize("interactive", [True, False])
def test_shown_at_a_terminal_before_the_resume_is_announced(repo, tmp_path, monkeypatch,
                                                            interactive):
    home = tmp_path / "home"
    wiring = _wire(repo, home, [turn(text="Started.")])
    _repl(wiring, "start the work")
    again = _resumed(repo, home, wiring.session.session_id, [turn(text="ok")])
    again.renderer.console = _console()
    monkeypatch.setattr("foundry.cli.session_commands.build", lambda *a, **k: again)
    monkeypatch.setattr("foundry.cli.session_commands._interactive", lambda: interactive)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "/exit")
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    args = ["resume", again.session.session_id, "--workspace", str(repo)]
    app.main(args if interactive else [*args, "go on"])
    screen = again.renderer.console.file.getvalue()
    assert ("foundry> start the work" in screen) is interactive
    if interactive:
        assert screen.index("foundry> start the work") < screen.index("resumed session")


# --- what is shown --------------------------------------------------------------------


def _fake(history, typed=()):
    return SimpleNamespace(resumed=SimpleNamespace(history=tuple(history), typed=tuple(typed)),
                           renderer=Renderer(console=_console()),
                           runtime=SimpleNamespace(registry=default_registry()))


def _user(text, *blocks):
    return Message(role=Role.USER, blocks=(*blocks, TextBlock(text)))


def _model(text="", *calls):
    return Message(role=Role.ASSISTANT, blocks=((TextBlock(text),) if text else ()) + calls)


def _screen(fake):
    show_conversation(fake)
    return [line.rstrip() for line in fake.renderer.console.file.getvalue().splitlines()
            if line.strip()]


def test_nothing_without_a_resume_or_a_conversation():
    fake = _fake([])
    assert _screen(fake) == []
    fake.resumed = None
    assert _screen(fake) == []


def test_only_what_the_user_and_the_model_said():
    fake = _fake([_user("go"), _model("", call("c1", "read_file", {"path": "x"})),
                  Message(role=Role.TOOL, blocks=(ToolResultBlock("c1", "SECRET OUTPUT"),)),
                  _model("done")])
    assert _screen(fake) == ["foundry> go", "-> read x", "done", END]


def test_a_plan_is_not_a_line():
    fake = _fake([_model("", call("c1", "update_plan", {"plan": []}),
                         call("c2", "run_command", {"command": "pytest -q"}))])
    assert _screen(fake) == ["-> pytest -q", END]
    # A turn of calls alone has no answer to show, not even an empty line.
    assert fake.renderer.console.file.getvalue() == "-> pytest -q\n" + END + "\n"


def test_an_answer_of_spaces_is_no_answer():
    fake = _fake([Message(role=Role.ASSISTANT, blocks=(
        TextBlock("  "), call("c1", "run_command", {"command": "pytest -q"})))])
    show_conversation(fake)
    assert fake.renderer.console.file.getvalue() == "-> pytest -q\n" + END + "\n"


def test_a_call_that_makes_no_display_is_its_name():
    fake = _fake([_model("", ToolUseBlock("c1", "read_file", "{not json"),
                         call("c2", "read_file", {"nope": 1}),
                         call("c3", "mcp__docs__search", {"q": "x"}))])
    assert _screen(fake) == ["-> read_file", "-> read_file", "-> mcp__docs__search", END]


@pytest.mark.parametrize("arguments", ["[1]", '"a.py"', "null", "3"])
def test_arguments_that_are_no_object_are_its_name(arguments):
    fake = _fake([_model("", ToolUseBlock("c1", "read_file", arguments))])
    assert _screen(fake) == ["-> read_file", END]


def test_a_long_message_is_cut():
    text = "\n".join(f"line {i}" for i in range(MAX_USER_LINES + 8))
    screen = _screen(_fake([_user(text)]))
    assert screen[0] == "foundry> line 0"
    assert screen[MAX_USER_LINES - 1] == f"line {MAX_USER_LINES - 1}"
    assert screen[MAX_USER_LINES] == "... (8 more lines)"


def test_exactly_the_lines_shown_are_not_cut():
    text = "\n".join(f"line {i}" for i in range(MAX_USER_LINES))
    assert "more lines" not in " ".join(_screen(_fake([_user(text)])))


def test_images_are_counted():
    image = ImageBlock(media_type="image/png", data="aGk=")
    assert _screen(_fake([_user("look", image, image)]))[0] == "foundry> look (2 image(s))"


def test_a_long_session_shows_its_last_messages():
    history = [_user(f"task {i}") for i in range(MAX_SHOWN + 5)]
    screen = _screen(_fake(history))
    assert screen[0] == "... 5 earlier message(s)" and screen[1] == "foundry> task 5"
    assert screen[-2] == f"foundry> task {MAX_SHOWN + 4}" and len(screen) == MAX_SHOWN + 2


def test_exactly_as_many_as_shown_says_no_earlier():
    screen = _screen(_fake([_user(f"task {i}") for i in range(MAX_SHOWN)]))
    assert screen[0] == "foundry> task 0"


def test_tool_results_are_not_counted_as_messages():
    history = [_user(f"task {i}") for i in range(MAX_SHOWN)]
    history += [Message(role=Role.TOOL, blocks=(ToolResultBlock(f"c{i}", "out"),))
                for i in range(5)]
    assert _screen(_fake(history))[0] == "foundry> task 0"


def test_each_message_of_the_users_starts_apart_as_at_the_prompt():
    fake = _fake([_model("Hello."), _user("hi")])
    assert fake.renderer.console.file.getvalue() == ""
    show_conversation(fake)
    assert fake.renderer.console.file.getvalue() == f"Hello.\n\nfoundry> hi\n{END}\n"


def test_a_compacted_conversation_says_so():
    screen = _screen(_fake([_user(SUMMARY_PREFIX + " we fixed the parser"), _user("next")]))
    assert screen == ["(what came before was compacted into a summary)", "foundry> next", END]


def test_typed_is_matched_by_the_whole_message():
    fake = _fake([_user("NOTE\n\ngo on"), _user("go on")], typed=[("NOTE\n\ngo on", "go on")])
    assert _screen(fake) == ["foundry> go on", "foundry> go on", END]
    fake = _fake([_user("NOTE\n\ngo on!")], typed=[("NOTE\n\ngo on", "go on")])
    assert _screen(fake)[0] == "foundry> NOTE"


def test_markup_and_control_sequences_are_text():
    fake = _fake([_user("[red]x[/red] \x1b]52;c;aGk=\x07y"),
                  _model("ok \x1b[2Jdone", call("c1", "run_command", {"command": "echo \x1b[31mz"}))])
    screen = "\n".join(_screen(fake))
    assert "[red]x[/red] y" in screen and "\x1b" not in fake.renderer.console.file.getvalue()
    assert "ok done" in screen and "echo z" in screen
