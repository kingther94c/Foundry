"""A message of several lines at the REPL (D-052).

Pasting a stack trace or a block of code used to send each line as its own
task. A paste arrives at once, faster than anyone types, so the lines after
the first belong to the same message; and lines typed between two lines of
three double quotes are one message too.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from foundry.cli import app
from foundry.cli import repl as repl_module
from foundry.cli.repl import BLOCK, input_waiting, read_message
from foundry.core.events import TerminalStatus

from test_resume import _wire, repo, turn  # noqa: F401 (fixture)
from test_slash_commands import _Recorder, _sent

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="a Windows console")
SRC = str(Path(__file__).resolve().parents[1] / "src")


def _waiting(monkeypatch, *answers: bool) -> list[bool]:
    """input_waiting answers these, then False."""
    queue = list(answers)
    monkeypatch.setattr(repl_module, "input_waiting", lambda: queue.pop(0) if queue else False)
    return queue


class _Interrupting(_Recorder):
    """Ctrl+C when it reaches the line None."""

    def input(self, prompt=""):
        line = self.lines.pop(0)
        if line is None:
            raise KeyboardInterrupt
        return line


# --- reading one message --------------------------------------------------------------------


def test_a_typed_line_is_a_message(monkeypatch):
    _waiting(monkeypatch)
    assert read_message(_Recorder(["  fix the parser  "])) == "fix the parser"


def test_a_pasted_block_is_one_message(monkeypatch):
    _waiting(monkeypatch, True, True, False)
    typist = _Recorder(["Traceback (most recent call last):", '  File "a.py", line 1',
                        "ValueError: bad", "next task"])
    assert read_message(typist) == ('Traceback (most recent call last):\n  File "a.py", '
                                    'line 1\nValueError: bad')
    assert typist.lines == ["next task"]
    assert any("3 lines, sent as one message" in p for p in typist.printed)


def test_blank_lines_at_the_ends_of_a_paste_go(monkeypatch):
    _waiting(monkeypatch, True, True, True, False)
    assert read_message(_Recorder(["", "  a", "  b", ""])) == "  a\n  b"


def test_lines_between_triple_quotes_are_one_message(monkeypatch):
    _waiting(monkeypatch)
    typist = _Recorder([BLOCK, "", "def f():", "    return 1", "", f"  {BLOCK}  ", "after"])
    assert read_message(typist) == "def f():\n    return 1"
    assert typist.lines == ["after"]


def test_triple_quotes_in_a_paste_are_just_text(monkeypatch):
    # A pasted docstring opens no block: the paste is one message, whole.
    _waiting(monkeypatch, True, True, True, False)
    typist = _Recorder([BLOCK, "Docs.", BLOCK, "x = 1"])
    assert read_message(typist) == f"{BLOCK}\nDocs.\n{BLOCK}\nx = 1"


def test_ctrl_c_in_a_block_discards_it(monkeypatch):
    _waiting(monkeypatch)
    typist = _Interrupting([BLOCK, "half a thought", None])
    assert read_message(typist) == ""
    assert any("(discarded)" in p for p in typist.printed)


def test_the_end_of_input_in_a_block_ends_the_session(monkeypatch):
    _waiting(monkeypatch)

    class Ending(_Recorder):
        def input(self, prompt=""):
            if not self.lines:
                raise EOFError
            return self.lines.pop(0)

    with pytest.raises(EOFError):
        read_message(Ending([BLOCK, "a"]))


# --- in the REPL ------------------------------------------------------------------------------


def test_the_model_gets_a_paste_as_one_message(repo, tmp_path, monkeypatch):
    wiring = _wire(repo, tmp_path / "home", [turn(text="seen it"), turn(text="and that")])
    _waiting(monkeypatch, True, False, True, False, False)   # blank lines, then a paste
    typist = _Recorder(["   ", "  ", "why does this fail?", "ValueError: bad", "and this?",
                        "/exit"])
    status = app._repl(wiring, typist)
    app._close(wiring, status, "session ended")
    assert _sent(wiring) == ["why does this fail?\nValueError: bad", "and this?"]


def test_a_discarded_block_sends_nothing_and_the_session_goes_on(repo, tmp_path, monkeypatch):
    wiring = _wire(repo, tmp_path / "home", [turn(text="ok")])
    _waiting(monkeypatch)
    typist = _Interrupting([BLOCK, "never mind", None, "real task", "/exit"])
    status = app._repl(wiring, typist)
    app._close(wiring, status, "session ended")
    assert status is TerminalStatus.CANCELLED and _sent(wiring) == ["real task"]


def test_help_says_how(repo, tmp_path, monkeypatch):
    wiring = _wire(repo, tmp_path / "home", [])
    _waiting(monkeypatch)
    typist = _Recorder(["/help", "/exit"])
    app._close(wiring, app._repl(wiring, typist), "session ended")
    assert any("a pasted block is one message" in p for p in typist.printed)


# --- what is waiting --------------------------------------------------------------------------


def test_input_that_is_not_a_terminal_is_read_a_line_at_a_time(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("a\nb\n"))
    if sys.platform == "win32":
        import msvcrt
        monkeypatch.setattr(msvcrt, "kbhit", lambda: True)   # not asked
    assert input_waiting() is False


@windows_only
def test_the_windows_console_is_asked_whether_keys_are_waiting(monkeypatch):
    import msvcrt

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr(msvcrt, "kbhit", lambda: True)
    assert input_waiting() is True
    monkeypatch.setattr(msvcrt, "kbhit", lambda: False)
    assert input_waiting(timeout=0.01) is False


# A child with a console of its own puts a paste into its input buffer --
# key events, as the console makes of a paste -- and reads it as the REPL does.
CHILD = r"""
import ctypes, json, sys
from ctypes import wintypes
from foundry.cli.repl import read_message

class KEY(ctypes.Structure):
    _fields_ = [("down", wintypes.BOOL), ("repeat", wintypes.WORD), ("vk", wintypes.WORD),
                ("scan", wintypes.WORD), ("char", wintypes.WCHAR), ("state", wintypes.DWORD)]

class EVENT(ctypes.Union):
    _fields_ = [("key", KEY), ("pad", ctypes.c_byte * 16)]

class RECORD(ctypes.Structure):
    _fields_ = [("type", wintypes.WORD), ("event", EVENT)]

def paste(text):
    records = []
    for ch in text:
        for down in (True, False):
            r = RECORD(); r.type = 1
            r.event.key.down, r.event.key.repeat, r.event.key.char = down, 1, ch
            r.event.key.vk = 0x0D if ch == "\r" else 0
            records.append(r)
    kernel32 = ctypes.WinDLL("kernel32")
    written = wintypes.DWORD()
    kernel32.WriteConsoleInputW(kernel32.GetStdHandle(-10), (RECORD * len(records))(*records),
                                len(records), ctypes.byref(written))

class Plain:
    printed = []
    def input(self, prompt=""):
        return input()
    def print(self, *args, **kwargs):
        self.printed.append(" ".join(map(str, args)))

console = Plain()
paste("first line\rsecond line\rthird line\r")
pasted = read_message(console)
paste("typed\r")
typed = read_message(console)
json.dump({"pasted": pasted, "typed": typed}, open(sys.argv[1], "w", encoding="utf-8"))
"""


@windows_only
def test_a_real_paste_in_a_console_is_one_message(tmp_path):
    out = tmp_path / "read.json"
    env = {**os.environ, "PYTHONPATH": SRC}
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(out)], env=env,
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
    assert child.wait(timeout=60) == 0
    read = json.loads(out.read_text(encoding="utf-8"))
    assert read == {"pasted": "first line\nsecond line\nthird line", "typed": "typed"}
