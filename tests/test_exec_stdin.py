"""`exec -` (D-053): the task read from stdin, as `codex exec -` reads it.

A long task -- a bug report, a spec, a template filled in by a script -- does
not fit a command line, and Windows quoting mangles one that has quotes in
it. Piped in, it arrives whole.
"""

from __future__ import annotations

import io

import pytest

from foundry.cli import app

from test_cli_flags import _exec_home
from test_resume import fake_model, repo  # noqa: F401 (fixtures)


class _Pipe:
    """stdin as a pipe: bytes, and not a terminal."""

    def __init__(self, data: bytes, tty: bool = False):
        self.buffer = io.BytesIO(data)
        self._tty = tty

    def isatty(self):
        return self._tty


def _task_sent(httpd) -> str:
    return httpd.requests[0]["messages"][-1]["content"]


def _run(monkeypatch, repo, data: bytes, *extra, tty=False):
    monkeypatch.setattr("sys.stdin", _Pipe(data, tty))
    return app.main(["exec", "-", "--workspace", str(repo), *extra])


@pytest.mark.parametrize("data, task", [
    (b"fix the failing test\n", "fix the failing test"),
    ('Fix "parse(\'a b\')" -- café, 中文\r\nline two\r\n'.encode("utf-8"),
     'Fix "parse(\'a b\')" -- café, 中文\nline two'),
    (b"\xef\xbb\xbfwith a byte-order mark", "with a byte-order mark"),
    ("﻿UTF-16 from PowerShell 5.1 é".encode("utf-16-le"), "UTF-16 from PowerShell 5.1 é"),
    ("﻿big-endian".encode("utf-16-be"), "big-endian"),
], ids=["plain", "quotes-and-non-ascii", "utf-8-bom", "utf-16-le", "utf-16-be"])
def test_the_task_on_stdin_is_the_task(repo, tmp_path, monkeypatch, capsys, fake_model,
                                       data, task):
    from test_cli_e2e import text_turn

    httpd, handler = fake_model
    _exec_home(tmp_path, httpd, monkeypatch)
    handler.turns, handler.index = [text_turn("ok"), text_turn("ok")], 0
    assert _run(monkeypatch, repo, data) == 10
    capsys.readouterr()
    assert _task_sent(httpd) == task


@pytest.mark.parametrize("data, tty, message", [
    (b"", False, "stdin held no task"),
    (b"  \r\n\n", False, "stdin held no task"),
    (b"x" * (app.MAX_STDIN_TASK_BYTES + 1), False, "larger than 1 MB"),
    (b"typed", True, "pipe it in"),
], ids=["empty", "blank", "too-large", "a-terminal"])
def test_no_task_on_stdin_is_refused(repo, tmp_path, monkeypatch, capsys, data, tty, message):
    monkeypatch.setenv("FOUNDRY_HOME", str(tmp_path / "home"))
    assert _run(monkeypatch, repo, data, tty=tty) == 2
    assert message in capsys.readouterr().err


def test_a_task_of_exactly_the_limit_is_read(monkeypatch):
    monkeypatch.setattr("sys.stdin", _Pipe(b"y" * app.MAX_STDIN_TASK_BYTES))
    assert len(app._task_from_stdin()) == app.MAX_STDIN_TASK_BYTES


def test_a_text_stream_without_bytes_is_read_too(monkeypatch):
    class TextOnly:
        def isatty(self):
            return False

        def read(self, size=-1):
            return "from a text stream\n"

    monkeypatch.setattr("sys.stdin", TextOnly())
    assert app._task_from_stdin() == "from a text stream"


def test_no_stdin_at_all_is_refused(monkeypatch):
    monkeypatch.setattr("sys.stdin", None)
    with pytest.raises(ValueError, match="pipe it in"):
        app._task_from_stdin()


def test_a_task_named_on_the_command_line_does_not_read_stdin(repo, tmp_path, monkeypatch,
                                                              capsys, fake_model):
    from test_cli_e2e import text_turn

    httpd, handler = fake_model
    _exec_home(tmp_path, httpd, monkeypatch)
    handler.turns, handler.index = [text_turn("ok"), text_turn("ok")], 0
    monkeypatch.setattr("sys.stdin", _Pipe(b"not this"))
    assert app.main(["exec", "this one", "--workspace", str(repo)]) == 10
    capsys.readouterr()
    assert _task_sent(httpd) == "this one"
