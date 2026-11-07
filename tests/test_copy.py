"""`/copy`: the model's latest answer, or one of its code blocks, on the
clipboard (D-123), as codex's /copy offers the whole response and each code
block in it.

The answer is the conversation's latest -- a message, or the summary of the
``finish`` that ended a task -- as it was shown: without control sequences
or carriage returns. The tests never touch the user's clipboard: the live
ones swap the clipboard write in the script for a file PowerShell writes.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys

import pytest

from foundry.cli import copying
from foundry.cli.copying import (
    CLIPBOARD_WRITE,
    COPY_SCRIPT,
    CopyFailed,
    as_shown,
    copy_text,
    latest_answer,
)
from foundry.cli.markdown import code_blocks
from foundry.core.conversation import Message, Role, TextBlock, ToolUseBlock

from test_resume import _wire, call, repo, turn  # noqa: F401  (fixture)
from test_slash_commands import _repl, _shown

windows = pytest.mark.skipif(sys.platform != "win32", reason="the clipboard is written on Windows")

ANSWER = ("Run it like this:\n\n```python\nprint('hi')\n```\n\nor in the shell:\n\n"
          "~~~powershell\nGet-ChildItem\n~~~\n")


def _said(text: str) -> Message:
    return Message.text(Role.ASSISTANT, text)


def _finished(summary, text: str = "") -> Message:
    blocks = ((TextBlock(text),) if text else ()) + (ToolUseBlock(
        "f1", "finish", json.dumps({"status": "completed", "summary": summary})),)
    return Message(Role.ASSISTANT, blocks)


def _clipboard(monkeypatch, fails: str = "") -> list[str]:
    """What was put on the clipboard, in place of putting it there."""
    copied: list[str] = []

    def write(text):
        if fails:
            raise CopyFailed(fails)
        copied.append(text)

    monkeypatch.setattr(copying, "copy_text", write)
    return copied


# --- which answer --------------------------------------------------------------------------


def test_the_latest_message_the_model_wrote():
    history = [Message.text(Role.USER, "hi"), _said("first"), Message.text(Role.USER, "more"),
               _said("second")]
    assert latest_answer(history) == "second"


def test_a_finish_gives_its_summary_not_the_words_before_it():
    assert latest_answer([_said("old"), _finished("all done", text="finishing")]) == "all done"


def test_a_message_of_calls_alone_is_passed_over():
    calls = Message(Role.ASSISTANT, (ToolUseBlock("c1", "read_file", '{"path": "a.py"}'),))
    result = Message(Role.TOOL, ())
    assert latest_answer([_said("the answer"), calls, result]) == "the answer"


@pytest.mark.parametrize("summary", [None, "", "  ", 3])
def test_a_finish_without_a_summary_falls_back_to_its_words(summary):
    assert latest_answer([_finished(summary, text="what it said")]) == "what it said"


def test_a_finish_whose_arguments_are_broken_falls_back_too():
    broken = Message(Role.ASSISTANT, (TextBlock("words"), ToolUseBlock("f1", "finish", "{oops")))
    assert latest_answer([broken]) == "words"


def test_nothing_before_the_model_has_written():
    assert latest_answer([]) is None
    assert latest_answer([Message.text(Role.USER, "hi"), _said("   ")]) is None


def test_what_is_copied_is_what_was_shown():
    assert as_shown("a\x1b[31mred\x1b[0m\r\nb\x1b]8;;http://x\x07c\x07") == "ared\nbc"


# --- its code blocks -----------------------------------------------------------------------


def test_each_fenced_block_with_its_language():
    assert code_blocks(ANSWER) == [("python", "print('hi')"), ("powershell", "Get-ChildItem")]


def test_a_fence_closes_only_on_its_own_marker():
    text = "````md\n```python\nx = 1\n```\n````\n```\nplain\n```"
    assert code_blocks(text) == [("md", "```python\nx = 1\n```"), ("", "plain")]
    assert code_blocks("```\n```js is not a close\n`````") == [("", "```js is not a close")]


def test_a_block_left_open_at_the_end_is_one_too():
    assert code_blocks("```sh\nls\n\n") == [("sh", "ls\n\n")]


def test_text_without_a_fence_has_none():
    assert code_blocks("just `inline` code\n    indented code\n") == []


# --- /copy ---------------------------------------------------------------------------------


def test_copy_puts_the_whole_answer_there_and_lists_its_blocks(repo, tmp_path, monkeypatch):
    copied = _clipboard(monkeypatch)
    wiring = _wire(repo, tmp_path / "home", [turn(text=ANSWER)])
    shown = _shown(_repl(wiring, "explain", "/copy"))
    assert copied == [ANSWER]
    assert "copied the latest answer to the clipboard (11 lines)" in shown
    assert "/copy 1  python: print('hi')" in shown
    assert "/copy 2  powershell: Get-ChildItem" in shown


def test_copy_n_puts_that_block_there(repo, tmp_path, monkeypatch):
    copied = _clipboard(monkeypatch)
    wiring = _wire(repo, tmp_path / "home", [turn(text=ANSWER)])
    shown = _shown(_repl(wiring, "explain", "/copy 2", "/copy 1"))
    assert copied == ["Get-ChildItem", "print('hi')"]
    assert "copied code block 2 (powershell) to the clipboard (1 line)" in shown
    assert "/copy 1  python" not in shown


def test_a_finish_summary_is_the_answer(repo, tmp_path, monkeypatch):
    copied = _clipboard(monkeypatch)
    wiring = _wire(repo, tmp_path / "home", [turn(call("f1", "finish", {
        "status": "completed", "summary": "Fixed it.\n```\npytest -q\n```", "claims": []}))])
    shown = _shown(_repl(wiring, "fix it", "/copy", "/copy 1"))
    assert copied == ["Fixed it.\n```\npytest -q\n```", "pytest -q"]
    assert "copied code block 1 to the clipboard (1 line)" in shown


def test_an_answer_with_control_sequences_is_copied_as_shown(repo, tmp_path, monkeypatch):
    copied = _clipboard(monkeypatch)
    wiring = _wire(repo, tmp_path / "home", [turn(text="safe\x1b[2J\r\n```\nrm\x1b[8m -rf x\n```")])
    _repl(wiring, "go", "/copy", "/copy 1")
    assert copied == ["safe\n```\nrm -rf x\n```", "rm -rf x"]


def test_a_block_with_no_language_is_called_code(repo, tmp_path, monkeypatch):
    _clipboard(monkeypatch)
    wiring = _wire(repo, tmp_path / "home", [turn(text="```\n" + "x" * 80 + "\n```")])
    shown = _shown(_repl(wiring, "go", "/copy"))
    assert "/copy 1  code: " + "x" * 57 + "..." in shown


def test_a_block_is_listed_by_its_first_line_with_words(repo, tmp_path, monkeypatch):
    _clipboard(monkeypatch)
    wiring = _wire(repo, tmp_path / "home", [turn(text="```py\n\n   \n  first = 1  \nsecond\n```")])
    assert "/copy 1  py: first = 1" in _shown(_repl(wiring, "go", "/copy"))


@pytest.mark.parametrize("lines, said", [
    (["/copy"], "there is no answer to copy yet"),
    (["go", "/copy 1"], "the latest answer has no code block; /copy copies all of it"),
    (["go", "/copy two"], "usage: /copy [N] -- N, one of the latest answer's code blocks"),
    (["go", "/copy -1"], "usage: /copy [N]"),
    (["go", "/copy \u00b2"], "usage: /copy [N]"),
])
def test_what_cannot_be_copied_says_why(repo, tmp_path, monkeypatch, lines, said):
    copied = _clipboard(monkeypatch)
    wiring = _wire(repo, tmp_path / "home", [turn(text="plain words")])
    assert said in _shown(_repl(wiring, *lines))
    assert copied == []


@pytest.mark.parametrize("number", ["0", "3", "999999"])
def test_a_block_that_is_not_there_says_how_many_are(repo, tmp_path, monkeypatch, number):
    copied = _clipboard(monkeypatch)
    wiring = _wire(repo, tmp_path / "home", [turn(text=ANSWER)])
    shown = _shown(_repl(wiring, "go", f"/copy {number}"))
    assert "the latest answer has 2 code block(s): /copy 1 to /copy 2" in shown
    assert copied == []


def test_an_empty_block_is_not_copied(repo, tmp_path, monkeypatch):
    copied = _clipboard(monkeypatch)
    wiring = _wire(repo, tmp_path / "home", [turn(text="```py\n   \n```")])
    assert "code block 1 (py) is empty: nothing to copy" in _shown(_repl(wiring, "go", "/copy 1"))
    assert copied == []


def test_a_clipboard_that_cannot_be_written_says_why(repo, tmp_path, monkeypatch):
    _clipboard(monkeypatch, fails="PowerShell exit 1")
    wiring = _wire(repo, tmp_path / "home", [turn(text=ANSWER)])
    shown = _shown(_repl(wiring, "go", "/copy"))
    assert "cannot copy the latest answer: PowerShell exit 1" in shown
    assert "/copy 1" not in shown


def test_a_resumed_session_copies_its_own_answer(repo, tmp_path, monkeypatch):
    copied = _clipboard(monkeypatch)
    home = tmp_path / "home"
    first = _wire(repo, home, [turn(text="from before")])
    _repl(first, "go")
    again = _wire(repo, home, [], resume="last")
    _repl(again, "/copy")
    assert copied == ["from before"]


def test_help_lists_it(repo, tmp_path):
    assert "/copy [N]" in _shown(_repl(_wire(repo, tmp_path / "home", []), "/help"))


# --- writing the clipboard, live ------------------------------------------------------------


@windows
@pytest.mark.parametrize("text", ["plain", "h\u00e9llo \u2014 \u4e16\u754c\n  second line\n",
                                  "a" * 100_000], ids=["plain", "unicode", "long"])
def test_the_text_reaches_powershell_whole(monkeypatch, tmp_path, text):
    written = tmp_path / "clipboard.txt"
    assert COPY_SCRIPT.count(CLIPBOARD_WRITE) == 1
    monkeypatch.setattr(copying, "COPY_SCRIPT", COPY_SCRIPT.replace(
        CLIPBOARD_WRITE, f"[IO.File]::WriteAllText('{written}', $text)"))
    copy_text(text)
    assert written.read_bytes().decode("utf-8") == text


@windows
def test_a_failing_write_says_so(monkeypatch):
    monkeypatch.setattr(copying, "COPY_SCRIPT", COPY_SCRIPT.replace(CLIPBOARD_WRITE, "exit 4"))
    with pytest.raises(CopyFailed, match="PowerShell exit 4"):
        copy_text("x")


# --- how PowerShell is started --------------------------------------------------------------


class _Run:
    """subprocess.run, as the clipboard write calls it."""

    def __init__(self, returncode=0, raises=None):
        self.returncode, self.raises = returncode, raises
        self.calls: list = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, "", "")


def _powershell(monkeypatch, run, path=r"C:\Windows\System32\powershell.exe"):
    monkeypatch.setattr(copying, "IS_WINDOWS", True)
    monkeypatch.setattr(copying, "find_program",
                        lambda name: path if name == "powershell.exe" else None)
    monkeypatch.setattr(copying.subprocess, "run", run)


def test_powershell_is_started_by_its_path_with_the_text_on_its_input(monkeypatch):
    run = _Run()
    _powershell(monkeypatch, run)
    copy_text("caf\u00e9")
    [(argv, kwargs)] = run.calls
    assert argv == [r"C:\Windows\System32\powershell.exe", "-NoProfile", "-NonInteractive",
                    "-Sta", "-Command", COPY_SCRIPT]
    assert base64.b64decode(kwargs["input"]).decode("utf-8") == "caf\u00e9"
    assert kwargs["timeout"] == copying.COPY_TIMEOUT_S
    assert kwargs["creationflags"] == copying.CREATE_NO_WINDOW


@pytest.mark.parametrize("run, said", [
    (_Run(returncode=1), "PowerShell exit 1"),
    (_Run(raises=subprocess.TimeoutExpired("powershell", 15)),
     "writing the clipboard took over 15s"),
    (_Run(raises=OSError("no such program")),
     "PowerShell could not be started (no such program)"),
])
def test_a_write_that_fails_says_why(monkeypatch, run, said):
    _powershell(monkeypatch, run)
    with pytest.raises(CopyFailed) as caught:
        copy_text("x")
    assert str(caught.value) == said


def test_without_powershell_it_says_so(monkeypatch):
    _powershell(monkeypatch, _Run(), path=None)
    with pytest.raises(CopyFailed, match="powershell.exe was not found on PATH"):
        copy_text("x")


def test_elsewhere_it_says_windows_only(monkeypatch):
    monkeypatch.setattr(copying, "IS_WINDOWS", False)
    with pytest.raises(CopyFailed, match="written on Windows only"):
        copy_text("x")
