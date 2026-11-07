"""A program the user names, run when a turn ends (D-050).

Long work is left to run unattended; ``notify`` is how its end reaches the
user. It is a program Foundry starts, so only the user's own configuration
may name one, and it is told what happened as one JSON argument, as codex
does, without Foundry waiting for it.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from rich.console import Console

from foundry.cli.app import build
from foundry.core.config import load_config
from foundry.core.errors import ConfigError
from foundry.core.events import Notice
from foundry.core.notify import EVENT_TYPE, TEXT_LIMIT, Notifier, clip, payload
from foundry.core.redaction import Redactor
from foundry.core.session import EventType, SessionStore
from foundry.core.winapi import IS_WINDOWS

from test_program_search import IMPOSTOR, search_cwd  # noqa: F401 (fixture)
from test_resume import OVERRIDES, _core, call, repo, turn  # noqa: F401 (fixture)

windows_only = pytest.mark.skipif(not IS_WINDOWS, reason="cmd.exe is Windows'")


def _home(tmp_path: Path, text: str) -> Path:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")
    return home


# --- configuration ------------------------------------------------------------------------


def test_the_users_config_names_the_program(tmp_path):
    config = load_config(home=_home(tmp_path, 'notify = ["notify-send", "Foundry"]\n'))
    assert config.notify == ("notify-send", "Foundry")
    assert config.explain("notify") == "notify = ['notify-send', 'Foundry'] (from user)"
    assert load_config(home=_home(tmp_path / "other", "")).notify == ()


def test_a_repository_may_not_name_one(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / ".foundry").mkdir(parents=True)
    (workspace / ".foundry" / "config.toml").write_text('notify = ["x"]\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="project config may not set notify"):
        load_config(workspace, home=_home(tmp_path, ""))


def test_a_profile_may_set_or_clear_it(tmp_path):
    home = _home(tmp_path, 'notify = ["a"]\n[profiles.quiet]\nnotify = []\n'
                           '[profiles.loud]\nnotify = ["b", "c"]\n')
    assert load_config(home=home).notify == ("a",)
    assert load_config(home=home, profile="quiet").notify == ()
    assert load_config(home=home, profile="loud").notify == ("b", "c")


@pytest.mark.parametrize("value", ['"notify-send"', "[1]", '[""]', '["  "]', '["a\\u0000b"]',
                                   '{ program = "x" }'])
def test_anything_but_a_list_of_words_is_refused(tmp_path, value):
    with pytest.raises(ConfigError, match="notify must be a list of strings"):
        load_config(home=_home(tmp_path, f"notify = {value}\n"))


# --- what it is told ----------------------------------------------------------------------


def test_the_payload_says_what_the_turn_was_and_how_it_ended():
    data = json.loads(payload(session_id="s1", cwd="C:/work", status="completed",
                              user_input="fix the build", last_message="Fixed: café"))
    assert data == {"type": EVENT_TYPE, "session-id": "s1", "cwd": "C:/work",
                    "status": "completed", "input-messages": ["fix the build"],
                    "last-assistant-message": "Fixed: café"}
    assert EVENT_TYPE == "agent-turn-complete"
    answered = json.loads(payload(session_id="s1", cwd="c", status=None, user_input="q",
                                  last_message="a"))
    assert answered["status"] is None


def test_the_payload_is_scrubbed_and_cut_to_fit_a_command_line():
    redactor = Redactor()
    redactor.register("hunter2-secret-value")
    long = "word " * 1_000
    data = json.loads(payload(session_id="s", cwd="c", status=None,
                              user_input="the key is hunter2-secret-value",
                              last_message=long + "hunter2-secret-value", scrub=redactor.scrub))
    assert "hunter2" not in json.dumps(data)
    assert len(data["last-assistant-message"]) == TEXT_LIMIT
    assert data["last-assistant-message"].endswith("...")
    assert clip("short") == "short" and len(clip("x" * (TEXT_LIMIT + 1))) == TEXT_LIMIT


# --- starting it ----------------------------------------------------------------------------

# Writes what it was given -- its arguments, directory and environment -- and
# says something on both streams, which nobody should see.
RECORDER = (
    "import json, os, sys\n"
    "print('LOUD-OUT'); print('LOUD-ERR', file=sys.stderr)\n"
    "open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps("
    "{'argv': sys.argv[2:], 'cwd': os.getcwd(), 'env': sorted(os.environ)}))\n"
)


def _wait(notifier: Notifier) -> None:
    for process in notifier._running:
        process.wait(timeout=60)


def test_the_program_gets_the_payload_verbatim_and_the_command_environment(tmp_path, capfd):
    out = tmp_path / "seen.json"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    notifier = Notifier((sys.executable, "-c", RECORDER, str(out)), workspace,
                        environ={**os.environ, "FOUNDRY_TEST_TOKEN": "t0ken"})
    tricky = payload(session_id="s", cwd=str(workspace), status="completed",
                     user_input='a "quoted" \\path\\ & %PATH% | ^ <>',
                     last_message="line one\nline two\t\u00e9\u4e2d \\\" \\\\")
    pid = notifier.send(tricky)
    _wait(notifier)
    seen = json.loads(out.read_text(encoding="utf-8"))
    assert pid > 0 and seen["argv"] == [tricky]
    assert os.path.samefile(seen["cwd"], workspace)
    assert "FOUNDRY_TEST_TOKEN" not in seen["env"]
    captured = capfd.readouterr()
    assert "LOUD" not in captured.out + captured.err


def test_finished_programs_are_collected(tmp_path):
    notifier = Notifier((sys.executable, "-c", "pass"), tmp_path)
    notifier.send("{}")
    _wait(notifier)
    notifier.send("{}")
    assert len(notifier._running) == 1
    _wait(notifier)


def test_a_program_not_on_path_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="'no-such-notifier' was not found on PATH"):
        Notifier(("no-such-notifier",), tmp_path).send("{}")


@windows_only
def test_one_in_the_current_directory_is_not_run(search_cwd):
    shutil.copy(IMPOSTOR, search_cwd / "notify-me.exe")
    with pytest.raises(FileNotFoundError):
        Notifier(("notify-me",), search_cwd).send("{}")


@windows_only
@pytest.mark.parametrize("argv", [("notify-script",), ("cmd", "/c", "notify-script.cmd")])
def test_cmd_exe_is_refused_since_it_would_run_the_payload(tmp_path, argv):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "notify-script.cmd").write_text("@echo off\n", encoding="utf-8")
    environ = {**os.environ, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}"}
    with pytest.raises(ValueError, match="cmd.exe, which would read the payload as commands"):
        Notifier(argv, tmp_path, environ=environ).send('{"x": "\\" & calc & \\""}')


# --- the runtime ------------------------------------------------------------------------------


class Recording:
    def __init__(self, fail: Exception | None = None):
        self.argv = ("notify-send", "Foundry")
        self.sent: list[dict] = []
        self.fail = fail

    def send(self, data: str) -> int:
        if self.fail is not None:
            raise self.fail
        self.sent.append(json.loads(data))
        return 4242


def _runtime(tmp_path, turns, notifier):
    runtime, session, repo_ = _core(tmp_path, turns)
    runtime.notifier = notifier
    notices: list[Notice] = []
    runtime.events.subscribe(lambda e: notices.append(e) if isinstance(e, Notice) else None)
    return runtime, session, repo_, notices


def _notified(session) -> list[dict]:
    return [r.payload for r in SessionStore.read_records(session.path)
            if r.type == EventType.NOTIFIED]


def test_each_turn_that_ends_tells_the_program(tmp_path):
    notifier = Recording()
    runtime, session, repo_, _ = _runtime(tmp_path, [turn(text="A is 1"), turn(text="B is 2")],
                                          notifier)
    runtime.run_turn("what is A?")
    runtime.run_turn("and B?")
    assert [n["input-messages"] for n in notifier.sent] == [["what is A?"], ["and B?"]]
    first = notifier.sent[0]
    assert first["type"] == "agent-turn-complete" and first["status"] is None
    assert first["session-id"] == session.session_id
    assert os.path.samefile(first["cwd"], repo_)
    assert first["last-assistant-message"] == "A is 1"
    # Each record names its event since D-079.
    assert _notified(session) == [{"type": "agent-turn-complete", "program": "notify-send",
                                   "pid": 4242}] * 2


def test_a_finished_task_reports_its_status(tmp_path):
    notifier = Recording()
    finish = turn(call("f1", "finish", {"status": "completed", "summary": "all done",
                                        "claims": []}))
    runtime, _, _, _ = _runtime(tmp_path, [finish], notifier)
    runtime.run_turn("do it")
    assert notifier.sent[0]["status"] == "completed"
    assert "all done" in notifier.sent[0]["last-assistant-message"]


def test_a_turn_the_user_stopped_tells_nobody(tmp_path):
    notifier = Recording()
    runtime, session, _, _ = _runtime(tmp_path, [turn(text="never")], notifier)
    runtime.cancel()
    assert runtime.run_turn("go").status.value == "cancelled"
    assert notifier.sent == [] and _notified(session) == []


def test_a_program_that_cannot_start_warns_once_and_is_journaled(tmp_path):
    notifier = Recording(fail=FileNotFoundError("'notify-send' was not found on PATH"))
    runtime, session, _, notices = _runtime(tmp_path, [turn(text="a"), turn(text="b")], notifier)
    runtime.run_turn("one")
    runtime.run_turn("two")
    warnings = [n for n in notices if "notify" in n.text]
    assert len(warnings) == 1 and warnings[0].level == "warning"
    assert "could not start notify-send" in warnings[0].text
    assert _notified(session) == [{"type": "agent-turn-complete", "program": "notify-send",
                                   "error": "'notify-send' was not found on PATH"}] * 2


def test_a_batch_file_is_a_warning_too(tmp_path):
    notifier = Recording(fail=ValueError("x is run by cmd.exe"))
    runtime, session, _, notices = _runtime(tmp_path, [turn(text="a")], notifier)
    assert runtime.run_turn("one").status is None
    assert any("run by cmd.exe" in n.text for n in notices)


def test_what_leaves_is_scrubbed(tmp_path):
    notifier = Recording()
    runtime, _, _, _ = _runtime(tmp_path, [turn(text="the token is s3cr3t-t0ken-value")],
                                notifier)
    redactor = Redactor()
    redactor.register("s3cr3t-t0ken-value")
    runtime.scrub = redactor.scrub
    runtime.run_turn("print s3cr3t-t0ken-value")
    assert "s3cr3t" not in json.dumps(notifier.sent)


# --- wiring -----------------------------------------------------------------------------------


def test_the_session_is_wired_from_the_config(repo, tmp_path):
    home = _home(tmp_path, f'notify = [{json.dumps(sys.executable)}, "-c", "pass"]\n')
    wiring = build(repo, home=home, overrides=OVERRIDES,
                   console=Console(file=io.StringIO(), width=100))
    try:
        assert wiring.runtime.notifier.argv == (sys.executable, "-c", "pass")
        assert os.path.samefile(wiring.runtime.notifier.cwd, repo)
        assert wiring.runtime.scrub is not None
    finally:
        wiring.session.close()
    plain = build(repo, home=_home(tmp_path / "plain", ""), overrides=OVERRIDES,
                  console=Console(file=io.StringIO(), width=100))
    try:
        assert plain.runtime.notifier is None
    finally:
        plain.session.close()
