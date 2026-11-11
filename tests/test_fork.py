"""`/fork` and `foundry fork`: a copy of a session, continued apart from it
(D-126), as codex's /fork and `codex fork`.

The fork is the source's journal copied -- each record as it was, so what
the conversation cites still names it -- with a header of its own, without
the source's names, and with the artifacts it cites; then it is resumed, with
every check a resume makes. The source is only read, and kept as it was.
"""

from __future__ import annotations

import argparse
import builtins
import subprocess

import pytest

from foundry.cli import app
from foundry.cli.session_commands import _fork
from foundry.core.errors import ResumeError
from foundry.core.events import EXIT_CODES, TerminalStatus
from foundry.core.resume import fork_journal, read_journal, resume_note
from foundry.core.session import EventType, SessionStore

from test_rename import _session, _session_id
from test_resume import _wire, fake_model, repo  # noqa: F401  (fixtures)
from test_resume_picker import home  # noqa: F401  (fixture)
from test_slash_commands import _repl, _shown

FORK_NOTE = "This session is a fork of session {}: the conversation so far is that session's"


def _records(journal) -> list:
    return list(SessionStore.read_records(journal))


def _said(journal) -> list[str]:
    return [m.text_content for m in read_journal(journal).history]


# --- the copy -------------------------------------------------------------------------------


def test_the_journal_is_copied_with_a_header_of_its_own(home, repo):
    source = home / "sessions" / _session(home, repo, 1, "parser") / "events.jsonl"
    store = SessionStore(home / "sessions", session_id=_session_id(1), resume=True)
    store.append(EventType.USER_INPUT, {"text": "more"})
    store.close()
    before = source.read_bytes()
    forked = fork_journal(home / "sessions", source)
    copy = home / "sessions" / forked / "events.jsonl"
    original, copied = _records(source), _records(copy)
    assert source.read_bytes() == before
    assert forked != _session_id(1)
    # Every record but the source's name, each with its ordinal and payload.
    assert [(r.ordinal, r.type, r.payload) for r in copied[1:]] == [
        (r.ordinal, r.type, r.payload) for r in original[1:] if r.type != EventType.RENAMED]
    assert copied[0].payload == {**original[0].payload, "session_id": forked,
                                 "forked_from": _session_id(1),
                                 "forked_at": original[-1].ordinal}


def test_a_half_written_last_line_is_left_behind(home, repo):
    source = home / "sessions" / _session(home, repo, 1) / "events.jsonl"
    with source.open("a", encoding="utf-8") as fh:
        fh.write('{"ts": "x", "ordinal": 9, "ty')
    copy = home / "sessions" / fork_journal(home / "sessions", source) / "events.jsonl"
    assert copy.read_text(encoding="utf-8").endswith("}\n")
    assert len(_records(copy)) == len(_records(source))


def test_a_half_written_line_a_resume_went_on_after_is_left_behind(home, repo):
    sid = _session(home, repo, 1)
    source = home / "sessions" / sid / "events.jsonl"
    with source.open("a", encoding="utf-8") as fh:
        fh.write('{"ts": "x", "ordinal": 9, "ty')
    store = SessionStore(home / "sessions", session_id=sid, resume=True)
    store.append(EventType.USER_INPUT, {"text": "after it"})
    store.close()
    copy = home / "sessions" / fork_journal(home / "sessions", source) / "events.jsonl"
    assert [r.payload for r in _records(copy)[1:]] == [r.payload for r in _records(source)[1:]]
    assert _records(copy)[-1].payload == {"text": "after it"}


def test_the_artifacts_come_too(home, repo):
    sid = _session(home, repo, 1)
    store = SessionStore(home / "sessions", session_id=sid, resume=True)
    ref = store.artifacts.put_bytes(b"what a command printed", media_type="text/plain")
    store.close()
    forked = fork_journal(home / "sessions", home / "sessions" / sid / "events.jsonl")
    copied = home / "sessions" / forked / "artifacts" / ref.artifact_id
    assert copied.read_bytes() == b"what a command printed"


def test_a_fork_that_cannot_be_written_leaves_nothing(home, repo, monkeypatch):
    source = home / "sessions" / _session(home, repo, 1) / "events.jsonl"
    (source.parent / "artifacts").mkdir(exist_ok=True)

    def broken(src, dst, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("foundry.core.resume.shutil.copytree", broken)
    with pytest.raises(OSError, match="disk full"):
        fork_journal(home / "sessions", source)
    assert sorted(p.name for p in (home / "sessions").iterdir()) == [_session_id(1)]


def test_a_fork_is_told_it_is_one_until_it_has_run(home, repo):
    sid = _session(home, repo, 1)
    store = SessionStore(home / "sessions", session_id=sid, resume=True)
    store.append(EventType.GIT_BASELINE, {"head": "0" * 40, "branch": "main",
                                          "dirty": [], "untracked": []})
    store.close()
    source = home / "sessions" / sid / "events.jsonl"
    forked = fork_journal(home / "sessions", source)
    copy = home / "sessions" / forked / "events.jsonl"
    state = read_journal(copy)
    assert state.session_id == forked and state.fork_of == sid
    assert resume_note(state, frozenset()).startswith(FORK_NOTE.format(sid))
    store = SessionStore(home / "sessions", session_id=forked, resume=True)
    store.append(EventType.RESUMED, {})
    store.close()
    state = read_journal(copy)
    assert state.fork_of == ""
    assert resume_note(state, frozenset()).startswith("This session was resumed.")
    assert read_journal(source).fork_of == ""


# --- /fork ----------------------------------------------------------------------------------


def _model(tmp_path, monkeypatch, fake_model, *answers):
    from test_cli_e2e import text_turn

    httpd, handler = fake_model
    host, port = httpd.server_address
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text(
        f'[backend]\nbase_url = "http://{host}:{port}/v1"\nmodel = "fake"\nstream = false\n',
        encoding="utf-8")
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    handler.turns, handler.index = [text_turn(a) for a in answers], 0
    return home, httpd


def _typed(monkeypatch, *lines):
    left = iter(lines)
    monkeypatch.setattr(builtins, "input", lambda *a: next(left))


def _journals(home) -> dict[str, object]:
    """Each session's journal, by whether it is a fork."""
    out = {}
    for journal in (home / "sessions").glob("*/events.jsonl"):
        meta = _records(journal)[0].payload
        out["fork" if "forked_from" in meta else "source"] = journal
    return out


def test_fork_goes_on_in_a_copy_and_keeps_this_one(repo, tmp_path, monkeypatch, capsys,
                                                    fake_model):
    home, httpd = _model(tmp_path, monkeypatch, fake_model, "A is 1", "B is 1")
    _typed(monkeypatch, "what is A?", "/fork", "and B?", "/exit")
    assert app.main(["run", "--workspace", str(repo)]) == 13
    journals = _journals(home)
    source, fork = journals["source"].parent.name, journals["fork"].parent.name
    assert _said(journals["source"]) == ["what is A?", "A is 1"]
    said = _said(journals["fork"])
    assert said[:2] == ["what is A?", "A is 1"] and said[-1] == "B is 1"
    assert said[2].startswith(FORK_NOTE.format(source)) and said[2].endswith("and B?")
    # The working tree is not copied with the conversation (D-172).
    assert "a fork copies the conversation, not the files" in said[2]
    assert read_journal(journals["source"]).reason == \
        f"the user forked this session into {fork}"
    # The model was sent the conversation so far, from the fork.
    sent = [m["content"] for m in httpd.requests[-1]["messages"][1:]]
    assert sent[:2] == ["what is A?", "A is 1"]
    out = capsys.readouterr().out
    assert f"now in {fork}, a fork of {source}, which is kept as it was" in out
    assert f"forked session {source} into {fork}" in out


def test_a_fork_that_cannot_go_on_is_dropped_and_this_one_goes_on(
        repo, tmp_path, monkeypatch, capsys, fake_model):
    home, _ = _model(tmp_path, monkeypatch, fake_model, "A is 1", "B is 1")
    from foundry.cli.wiring import build as real

    def build(root, **kwargs):
        if kwargs.get("resume"):
            raise ResumeError("HEAD has moved")
        return real(root, **kwargs)

    monkeypatch.setattr("foundry.cli.session_commands.build", build)
    _typed(monkeypatch, "what is A?", "/fork", "and B?", "/exit")
    assert app.main(["run", "--workspace", str(repo)]) == 13
    assert list(_journals(home)) == ["source"]
    assert _said(_journals(home)["source"]) == ["what is A?", "A is 1", "and B?", "B is 1"]
    assert "cannot continue the fork: HEAD has moved" in capsys.readouterr().out


def test_fork_that_cannot_be_copied_says_so(repo, tmp_path, monkeypatch):
    def broken(sessions, source):
        raise OSError("disk full")

    monkeypatch.setattr("foundry.cli.slash_commands.fork_journal", broken)
    wiring = _wire(repo, tmp_path / "home", [])
    assert "cannot fork this session: disk full" in _shown(_repl(wiring, "/fork"))


def test_help_lists_it(repo, tmp_path):
    assert "/fork " in _shown(_repl(_wire(repo, tmp_path / "home", []), "/help"))


# --- foundry fork ---------------------------------------------------------------------------


def test_foundry_fork_continues_a_copy(repo, tmp_path, monkeypatch, capsys, fake_model):
    # exec asks once more for a finish, so it takes two answers; the fork, one.
    home, _ = _model(tmp_path, monkeypatch, fake_model, "A is 1", "A again", "B is 1")
    workspace = ["--workspace", str(repo)]
    assert app.main(["exec", "what is A?", *workspace]) == 10
    capsys.readouterr()
    assert app.main(["fork", "--last", "and B?", *workspace]) == 10
    journals = _journals(home)
    source, fork = journals["source"].parent.name, journals["fork"].parent.name
    assert f"forked {source} into {fork}; {source} is kept as it was" in capsys.readouterr().out
    kept, forked = _said(journals["source"]), _said(journals["fork"])
    assert kept[-1] == "A again" and "B is 1" not in kept
    assert forked[:len(kept)] == kept and forked[len(kept) + 1:] == ["B is 1"]
    assert forked[len(kept)].startswith(FORK_NOTE.format(source))


def test_a_fork_whose_head_has_moved_is_not_kept(repo, tmp_path, monkeypatch, capsys,
                                                  fake_model):
    home, _ = _model(tmp_path, monkeypatch, fake_model, "A is 1")
    workspace = ["--workspace", str(repo)]
    assert app.main(["exec", "what is A?", *workspace]) == 10
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "moved"], cwd=repo,
                   check=True)
    capsys.readouterr()
    assert app.main(["fork", "--last", "and B?", *workspace]) == 12
    assert list(_journals(home)) == ["source"]
    assert "HEAD" in capsys.readouterr().out


def test_fork_by_name_and_a_name_no_session_has(home, repo):
    sid = _session(home, repo, 1, "parser")
    args = argparse.Namespace(resume="parser", workspace=str(repo))
    quiet = _Quiet()
    assert _fork(args, quiet) is None
    assert args.resume != sid and f"forked {sid} into {args.resume}" in quiet.text()
    args = argparse.Namespace(resume="lexer", workspace=str(repo))
    assert _fork(args, quiet) == EXIT_CODES[TerminalStatus.FAILED]
    assert "cannot fork: no such session: lexer" in quiet.text()


def test_fork_takes_resume_s_arguments():
    parsed = app.build_parser().parse_args(["fork", "--last", "--all", "go on", "-C", "x"])
    assert (parsed.command, parsed.resume, parsed.task, parsed.last, parsed.all,
            parsed.workspace) == ("fork", "go on", None, True, True, "x")


class _Quiet:
    def __init__(self):
        self.printed: list[str] = []

    def print(self, *args, **kwargs):
        self.printed.append(" ".join(str(a) for a in args))

    def text(self) -> str:
        return "\n".join(self.printed)
