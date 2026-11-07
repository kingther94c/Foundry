"""`/rename NAME`: a session's name, to list it by and resume it by (D-125),
as codex's /rename names a thread and `codex resume` takes a name.

The name is journaled, so a resumed session keeps it; the latest one holds.
`foundry resume NAME` -- and everything that takes a session id -- finds the
newest session of the workspace with that name, an id first, as codex looks;
`foundry resume --all NAME` any workspace's. A name is one line that can be
typed after `foundry resume`.
"""

from __future__ import annotations

import argparse

import pytest

from foundry.cli import app
from foundry.core.errors import ResumeError
from foundry.core.resume import (
    MAX_NAME,
    journal_name,
    named_session,
    resolve_session,
    session_name,
    summarize,
)
from foundry.core.session import EventType, SessionStore

from test_resume import _wire, repo, turn  # noqa: F401  (fixture)
from test_resume_picker import home  # noqa: F401  (fixture)
from test_slash_commands import _repl, _shown


def _session(home, workspace, number, name=None, *names):
    """A journal with a known id, named ``name`` then each of ``names``."""
    store = SessionStore(home / "sessions",
                         session_id=f"20261108T0000{number:02d}-0000000{number}")
    store.write_header(workspace=str(workspace), profile="", model="m", foundry_version="t")
    store.append(EventType.USER_INPUT, {"text": f"task {number}"})
    for each in ([name] if name else []) + list(names):
        store.append(EventType.RENAMED, {"name": each})
    store.close()
    return store.session_id


def _renamed(wiring) -> list[str]:
    return [r.payload["name"] for r in SessionStore.read_records(wiring.session.path)
            if r.type == EventType.RENAMED]


# --- what a name may be --------------------------------------------------------------------


@pytest.mark.parametrize("raw, name", [
    ("parser", "parser"),
    ("  fix the parser  ", "fix the parser"),
    ("\u89e3\u6790\u5668 #2", "\u89e3\u6790\u5668 #2"),
    ("x" * MAX_NAME, "x" * MAX_NAME),
    ("lastly", "lastly"),
])
def test_a_name(raw, name):
    assert session_name(raw) == name


@pytest.mark.parametrize("raw, why", [
    ("   ", "a name cannot be empty"),
    ("one\ntwo", "a name is one line"),
    ("one\rtwo", "a name is one line"),
    ("bell\x07", "a name cannot hold a control character"),
    ("csi\x9b", "a name cannot hold a control character"),
    ("del\x7f", "a name cannot hold a control character"),
    ('say "hi"', "a name cannot hold a double quote"),
    ("x" * (MAX_NAME + 1), f"a name is at most {MAX_NAME} characters"),
    ("last", "`last` already means the newest session"),
    ("--all", "a name cannot start with -"),
])
def test_what_is_not_a_name(raw, why):
    with pytest.raises(ValueError) as caught:
        session_name(raw)
    assert str(caught.value).startswith(why)


# --- /rename -------------------------------------------------------------------------------


def test_rename_names_the_session(repo, home):
    wiring = _wire(repo, home, [])
    shown = _shown(_repl(wiring, "/rename", "/rename fix the parser", "/rename", "/status"))
    assert "this session has no name; /rename NAME gives it one" in shown
    assert 'this session is now named fix the parser; foundry resume "fix the parser" ' \
           "continues it" in shown
    assert 'this session is named fix the parser; foundry resume "fix the parser" ' \
           "continues it" in shown
    assert _renamed(wiring) == ["fix the parser"]


def test_status_shows_the_name_once_it_has_one(repo, home):
    wiring = _wire(repo, home, [])
    shown = _shown(_repl(wiring, "/status", "/rename parser", "/status"))
    assert shown.count("[dim]session  [/dim]") == 2
    assert shown.count("[dim]name     [/dim] parser") == 1


@pytest.mark.parametrize("name, hint", [
    ("parser-2.0", "foundry resume parser-2.0 "),
    ("a&b", 'foundry resume "a&b" '),
])
def test_the_hint_quotes_a_name_only_where_a_shell_needs_it(repo, home, name, hint):
    wiring = _wire(repo, home, [])
    assert hint + "continues it" in _shown(_repl(wiring, f"/rename {name}"))


def test_the_latest_name_holds(repo, home):
    wiring = _wire(repo, home, [])
    _repl(wiring, "/rename first", "/rename second")
    assert _renamed(wiring) == ["first", "second"]
    assert journal_name(wiring.session.path) == "second"
    assert summarize(wiring.session.path.parent).name == "second"


@pytest.mark.parametrize("line, why", [
    ("/rename last", "`last` already means the newest session"),
    ("/rename one\ntwo", "a name is one line"),
])
def test_a_name_that_cannot_be_is_not_kept(repo, home, line, why):
    wiring = _wire(repo, home, [])
    assert why in _shown(_repl(wiring, line))
    assert _renamed(wiring) == []


def test_help_lists_it(repo, home):
    assert "/rename [NAME]" in _shown(_repl(_wire(repo, home, []), "/help"))


# --- resuming by name ----------------------------------------------------------------------


def test_a_resume_by_name_continues_that_session_with_its_name(repo, home):
    first = _wire(repo, home, [turn(text="ok")])
    _repl(first, "/rename parser", "go")
    _repl(_wire(repo, home, [turn(text="ok")]), "something else")
    again = _wire(repo, home, [], resume="parser")
    assert again.session.session_id == first.session.session_id
    assert again.name == "parser"
    assert "this session is named parser" in _shown(_repl(again, "/rename"))


def test_the_newest_of_this_workspaces_sessions_by_that_name(home, repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    older = _session(home, repo, 1, "parser")
    _session(home, repo, 2, "parser", "lexer")       # renamed since
    newer = _session(home, repo, 3, "parser")
    elsewhere = _session(home, other, 4, "parser")
    sessions = home / "sessions"
    assert named_session(sessions, "parser", repo) == newer
    assert named_session(sessions, "parser", None) == elsewhere
    assert named_session(sessions, "lexer", repo) == _session_id(2)
    assert named_session(sessions, "Parser", repo) is None
    assert resolve_session(sessions, "parser", repo) == sessions / newer / "events.jsonl"
    assert older != newer


def _session_id(number):
    return f"20261108T0000{number:02d}-0000000{number}"


def test_only_a_rename_names_it(home, repo):
    named = _session(home, repo, 1, "parser")
    store = SessionStore(home / "sessions", session_id=named, resume=True)
    store.append(EventType.TOOL_CALL, {"name": "read_file", "display": "read a.py"})
    store.close()
    journal = home / "sessions" / named / "events.jsonl"
    assert journal_name(journal) == "parser"
    assert summarize(journal.parent).name == "parser"
    assert named_session(home / "sessions", "read_file", repo) is None


def test_an_id_is_looked_for_first(home, repo):
    first = _session(home, repo, 1)
    _session(home, repo, 2, first)                   # named as the first one's id
    sessions = home / "sessions"
    assert resolve_session(sessions, first, repo) == sessions / first / "events.jsonl"


def test_a_name_no_session_has_is_no_such_session(home, repo):
    _session(home, repo, 1, "parser")
    with pytest.raises(ResumeError, match="no such session: lexer"):
        resolve_session(home / "sessions", "lexer", repo)


def test_resume_all_finds_a_name_in_any_workspace(home, repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    there = _session(home, other, 1, "elsewhere")
    args = argparse.Namespace(resume="elsewhere", all=True, last=False, task=None,
                              workspace=str(repo))
    assert app._choose_resume(args, _Quiet()) is None
    assert args.resume == there and args.workspace == str(other)


def test_resume_all_with_a_name_no_session_has(home, repo):
    args = argparse.Namespace(resume="nothing", all=True, last=False, task=None,
                              workspace=str(repo))
    quiet = _Quiet()
    assert app._choose_resume(args, quiet) == app.EXIT_CODES[app.TerminalStatus.FAILED]
    assert "no such session: nothing" in quiet.text()


class _Quiet:
    def __init__(self):
        self.printed: list[str] = []

    def print(self, *args, **kwargs):
        self.printed.append(" ".join(str(a) for a in args))

    def text(self) -> str:
        return "\n".join(self.printed)


# --- listings ------------------------------------------------------------------------------


def test_listings_show_the_name(home, repo, capsys):
    _session(home, repo, 1)
    _session(home, repo, 2, "parser")
    assert app.main(["sessions"]) == 0
    out = capsys.readouterr().out
    named, plain = [line for line in out.splitlines() if "task" in line][:2]
    assert "parser" in named and "task 2" in named
    assert "task 1" in plain


def test_the_session_line_marks_the_name(home, repo):
    _session(home, repo, 1, "pa[red]rser")
    line = app._session_line(summarize(home / "sessions" / _session_id(1)))
    assert "[bold]pa\\[red]rser[/bold]" in line


def test_sessions_name_lists_its_records(home, repo, capsys):
    _session(home, repo, 1, "parser")
    assert app.main(["sessions", "parser"]) == 0
    assert "renamed" in capsys.readouterr().out
