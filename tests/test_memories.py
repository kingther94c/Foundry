"""codex's memories (D-145): a pass reads a workspace's idle sessions for
what a later session there should know, merges it into notes and a summary,
and a session starts with the summary and reads the notes with
read_memories -- only in a workspace the user trusts, and only its own.
"""

from __future__ import annotations

import os
import time

import pytest

from foundry.cli import app
from foundry.cli.memories import NOT_TRUSTED, TURN_ON, USAGE
from foundry.core.config import MemorySettings, load_config, parse_settings
from foundry.core.errors import ConfigError
from foundry.core.events import TerminalStatus
from foundry.core.memories import (
    MEMORY_HEADING,
    NOTES_FILE,
    SESSIONS_DIR,
    STATE_FILE,
    SUMMARY_FILE,
    _FolderLock,
    folder,
    forget,
    load_state,
    run_pass,
    transcript,
)
from foundry.core.session import EventType, SessionStore

from test_resume import _Live, _wire, call, repo, turn  # noqa: F401  (fixture)
from test_slash_commands import _repl, _shown

NOW = time.time()
MERGED = "Run the tests with pytest -q.\n---NOTES---\n# Testing\n- pytest -q runs them"


@pytest.fixture(autouse=True)
def machine(tmp_path, monkeypatch):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "programdata"))


def _home(tmp_path, text: str = "[features]\nmemories = true\n", *, trust=None):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")
    if trust is not None:
        app._remember_trust(home, trust)
    return home


def _session(repo, home, task="add a test", *, said="I added it.", hours=7.0):
    """A session in ``repo``, closed, last active ``hours`` ago."""
    wiring = _wire(repo, home, [turn(text=said)])
    _repl(wiring, task)
    _idle(wiring.session.path, hours)
    return wiring.session


def _idle(path, hours: float) -> None:
    at = NOW - hours * 3600
    os.utime(path, (at, at))


def _pass(home, repo, answers, *, settings=None, scrub=lambda text: text, model="m"):
    backend = _Live(list(answers))
    result = run_pass(home=home, root=repo, settings=settings or MemorySettings(),
                      backend=backend, model=model, scrub=scrub, now=NOW)
    return result, backend


def _none_read(home, repo, **kwargs) -> bool:
    """A pass that reads nothing: asks the model nothing and fails at nothing
    -- a pass with no answers to give would otherwise read none as well."""
    result, backend = _pass(home, repo, [], **kwargs)
    return (result.read, result.errors, backend.seen) == (0, [], [])


def _kept(home, repo) -> list[str]:
    own = folder(home, repo) / SESSIONS_DIR
    return sorted(p.stem for p in own.glob("*.md")) if own.is_dir() else []


# --- the settings -------------------------------------------------------------------


def test_codex_s_names_and_ranges(tmp_path):
    config = load_config(home=_home(tmp_path, "[memories]\ngenerate_memories = false\n"
                                              "no_memories_if_mcp_or_web_search = true\n"
                                              "max_rollouts_per_startup = 500\n"
                                              "min_rollout_idle_hours = 0\n"
                                              "max_rollout_age_days = 7\n"
                                              'extract_model = " small "\n'))
    assert config.memory_settings == MemorySettings(
        generate=False, disable_on_external_context=True, max_sessions_per_pass=128,
        min_idle_hours=1, max_age_days=7, extract_model="small")
    assert config.memories is False         # the settings are not the feature


@pytest.mark.parametrize("text, said", [
    ("use_memories = 1", "memories.use_memories must be true or false"),
    ("max_raw_memories_for_consolidation = 1.5", "must be a whole number"),
    ("max_rollout_age_days = true", "must be a whole number"),
    ('consolidation_model = " "', "must be a model's name"),
    ("version = \"v2\"", "no setting memories.version; Foundry keeps one store"),
    ("max_unused_days = 3", "does not track when a memory was last used"),
    ("min_rate_limit_remaining_percent = 5", "Foundry sees no rate-limit windows"),
    ("use_memory = true", "did you mean 'use_memories'"),
    ("disable_on_external_context = true\nno_memories_if_mcp_or_web_search = false",
     "are the same setting"),
])
def test_what_is_refused(tmp_path, text, said):
    with pytest.raises(ConfigError, match=said):
        load_config(home=_home(tmp_path, "[memories]\n" + text + "\n"))


def test_a_repository_may_not_set_them(tmp_path, repo):
    (repo / ".foundry").mkdir()
    (repo / ".foundry" / "config.toml").write_text("[memories]\nuse_memories = false\n",
                                                   encoding="utf-8")
    with pytest.raises(ConfigError, match="project config may not set memories"):
        load_config(repo, home=_home(tmp_path))


def test_dash_c_says_where_they_go():
    with pytest.raises(ConfigError, match=r"\[memories\] goes in your config.toml"):
        parse_settings(["memories.use_memories=false"])


# --- the pass -----------------------------------------------------------------------


def test_an_idle_session_is_read_and_merged_into_notes(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    session = _session(repo, home, "use pytest -q, not pytest", said="Noted.")
    result, backend = _pass(home, repo, [turn(text="- the tests run with pytest -q"),
                                         turn(text=MERGED)])
    assert (result.read, result.kept, result.merged, result.errors) == (1, 1, True, [])
    where = folder(home, repo)
    assert _kept(home, repo) == [session.session_id]
    assert (where / NOTES_FILE).read_text(encoding="utf-8") == "# Testing\n- pytest -q runs them\n"
    assert (where / SUMMARY_FILE).read_text(encoding="utf-8") == "Run the tests with pytest -q.\n"
    read, merge = backend.seen
    assert "User: use pytest -q, not pytest" in read.messages[1].blocks[0].text
    assert "Assistant: Noted." in read.messages[1].blocks[0].text
    assert "## Session " + session.session_id in merge.messages[1].blocks[0].text
    assert load_state(where)["sessions"][session.session_id]["size"] == \
        session.path.stat().st_size
    # Nothing changed: nothing is asked again.
    again, backend = _pass(home, repo, [])
    assert (again.read, again.merged, backend.seen) == (0, False, [])
    assert "no session to read" in again.said()


def test_what_is_not_read(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    _session(repo, home, hours=2)                       # not idle long enough
    _session(repo, home, hours=31 * 24)                 # too old
    other = tmp_path / "other"
    other.mkdir()
    from foundry.core.tools.git import run_git
    run_git(["init"], other)
    run_git(["config", "user.email", "t@example.com"], other)
    run_git(["config", "user.name", "T"], other)
    (other / "x.py").write_text("X = 1\n", encoding="utf-8")
    run_git(["add", "."], other)
    run_git(["commit", "-m", "x"], other)
    _session(other, home)                               # another workspace's
    unanswered = _wire(repo, home, [])                  # no answer from the model
    unanswered.session.close()
    _idle(unanswered.session.path, 7)
    in_use = _session(repo, home)
    with SessionStore(in_use.dir.parent, session_id=in_use.session_id, resume=True):
        _idle(in_use.path, 7)                           # held by an open store
        assert _none_read(home, repo)


def test_a_session_that_says_no_is_not_read(repo, tmp_path):
    home = _home(tmp_path, "[features]\nmemories = true\n[memories]\ngenerate_memories = false\n",
                 trust=repo)
    session = _session(repo, home)
    modes = [r.payload for r in SessionStore.read_records(session.path)
             if r.type == EventType.MEMORY_MODE]
    assert modes == [{"mode": "disabled", "by": "generate_memories"}]
    assert _none_read(home, repo)


def test_generate_off_and_on_again_for_this_session(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    wiring = _wire(repo, home, [turn(text="ok")])
    shown = _shown(_repl(wiring, "/memories generate off", "hello"))
    assert "this session is not read for memories" in shown
    _idle(wiring.session.path, 7)
    assert _none_read(home, repo)
    wiring = _wire(repo, home, [turn(text="ok")], resume="last")
    _repl(wiring, "/memories generate on")
    _idle(wiring.session.path, 7)
    assert _pass(home, repo, [turn(text="NONE")])[0].read == 1


def test_one_that_reached_outside_is_left_out_when_asked(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    session = _session(repo, home)
    with SessionStore(session.dir.parent, session_id=session.session_id, resume=True) as store:
        store.append(EventType.TOOL_CALL, {"call_id": "m1", "name": "mcp__docs__search",
                                           "target": "x", "display": "x"})
    _idle(session.path, 7)
    assert _none_read(home, repo, settings=MemorySettings(disable_on_external_context=True))
    assert _pass(home, repo, [turn(text="NONE")])[0].read == 1


def test_nothing_worth_keeping_keeps_nothing(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    _session(repo, home)
    result, backend = _pass(home, repo, [turn(text="none")])
    assert (result.read, result.kept, result.merged, len(backend.seen)) == (1, 0, False, 1)
    assert _kept(home, repo) == [] and not (folder(home, repo) / NOTES_FILE).exists()


def test_a_merge_without_its_mark_writes_nothing(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    _session(repo, home)
    result, _ = _pass(home, repo, [turn(text="- a"), turn(text="notes without a summary")])
    assert result.errors and "---NOTES---" in result.errors[0] and not result.merged
    assert not (folder(home, repo) / NOTES_FILE).exists()
    assert load_state(folder(home, repo))["last_pass"]["errors"] == result.errors
    # The session's memories are kept; the next pass merges them.
    again, _ = _pass(home, repo, [turn(text=MERGED)])
    assert again.merged and again.read == 0


def test_a_model_that_cannot_be_asked_is_asked_again_next_time(repo, tmp_path):
    from foundry.core.errors import ProtocolError

    home = _home(tmp_path, trust=repo)
    _session(repo, home)

    def fail():
        raise ProtocolError("the gateway said no")
    result, _ = _pass(home, repo, [fail])
    assert result.read == 0 and "the gateway said no" in result.errors[0]
    assert _pass(home, repo, [turn(text="NONE")])[0].read == 1


def test_a_journal_that_cannot_be_read_is_not_read_again(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    session = _session(repo, home)
    with session.path.open("a", encoding="utf-8") as fh:
        fh.write('{"v": 99, "type": "model_request", "ordinal": 999, "payload": {}}\n')
    _idle(session.path, 7)
    result, _ = _pass(home, repo, [])
    assert result.read == 0 and result.errors
    assert "error" in load_state(folder(home, repo))["sessions"][session.session_id]
    assert _pass(home, repo, [])[0].errors == []


def test_a_session_that_went_on_is_read_again(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    session = _session(repo, home)
    _pass(home, repo, [turn(text="- a"), turn(text=MERGED)])
    wiring = _wire(repo, home, [turn(text="ok")], resume=session.session_id)
    _repl(wiring, "more")
    _idle(session.path, 7)
    result, _ = _pass(home, repo, [turn(text="- b"), turn(text=MERGED)])
    assert result.read == 1 and result.merged


def test_a_session_that_went_on_to_nothing_worth_keeping(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    session = _session(repo, home)
    _pass(home, repo, [turn(text="- a"), turn(text=MERGED)])
    wiring = _wire(repo, home, [turn(text="ok")], resume=session.session_id)
    _repl(wiring, "that was wrong; forget it")
    _idle(session.path, 7)
    result, backend = _pass(home, repo, [turn(text="NONE")])
    assert (result.read, result.kept, result.merged, len(backend.seen)) == (1, 0, False, 1)
    # Its memories are gone, and with them the notes: no other session has any.
    where = folder(home, repo)
    assert _kept(home, repo) == []
    assert not (where / NOTES_FILE).exists() and not (where / SUMMARY_FILE).exists()


def test_the_newest_sessions_first_and_as_many_as_set(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    old = _session(repo, home, hours=30)
    new = _session(repo, home, hours=8)
    settings = MemorySettings(max_sessions_per_pass=1, max_raw_memories=1)
    _pass(home, repo, [turn(text="- new"), turn(text=MERGED)], settings=settings)
    assert _kept(home, repo) == [new.session_id]
    # The older one is read next, and the memories kept are the newest
    # session's: the older one's are let go.
    result, backend = _pass(home, repo, [turn(text="- old"), turn(text=MERGED)],
                            settings=settings)
    assert result.read == 1 and _kept(home, repo) == [new.session_id]
    assert "## Session " + old.session_id not in backend.seen[1].messages[1].blocks[0].text


def test_the_models_used(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    _session(repo, home)
    settings = MemorySettings(extract_model="small", consolidation_model="big")
    _, backend = _pass(home, repo, [turn(text="- a"), turn(text=MERGED)], settings=settings)
    assert [request.model for request in backend.seen] == ["small", "big"]
    _session(repo, home)
    _, backend = _pass(home, repo, [turn(text="- a"), turn(text=MERGED)], model="session's")
    assert [request.model for request in backend.seen] == ["session's", "session's"]


def test_what_is_written_is_scrubbed(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    _session(repo, home)
    scrub = lambda text: text.replace("hunter2", "[redacted]")    # noqa: E731
    _pass(home, repo, [turn(text="- the password is hunter2"),
                       turn(text="sum hunter2\n---NOTES---\nnotes hunter2")], scrub=scrub)
    where = folder(home, repo)
    for path in [*where.rglob("*.md")]:
        assert "hunter2" not in path.read_text(encoding="utf-8"), path


def test_one_pass_at_a_time(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    _session(repo, home)
    where = folder(home, repo)
    where.mkdir(parents=True)
    lock = _FolderLock(where / "lock")
    try:
        result, backend = _pass(home, repo, [])
        assert result.busy and backend.seen == [] and "another pass" in result.said()
    finally:
        lock.release()


def test_forgetting(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    _session(repo, home)
    _pass(home, repo, [turn(text="- a"), turn(text=MERGED)])
    assert forget(home, repo, now=NOW)
    where = folder(home, repo)
    assert not (where / NOTES_FILE).exists() and _kept(home, repo) == []
    assert _none_read(home, repo)                      # what was before, not again
    later = _session(repo, home)
    os.utime(later.path, (NOW + 1, NOW + 1))           # active after the reset
    result = run_pass(home=home, root=repo, settings=MemorySettings(),
                      backend=_Live([turn(text="- b"), turn(text=MERGED)]), model="m",
                      scrub=lambda text: text, now=NOW + 8 * 3600)
    assert result.read == 1 and _kept(home, repo) == [later.session_id]
    assert not forget(home, tmp_path / "nowhere")


def test_a_transcript_is_what_was_said_and_done():
    from foundry.core.conversation import Message, Role, TextBlock, ToolResultBlock

    history = (Message.text(Role.USER, "fix a.py"),
               Message(role=Role.ASSISTANT, blocks=(TextBlock("Reading it."),
                                                    call("c1", "read_file", {"path": "a.py"}))),
               Message(role=Role.USER, blocks=(ToolResultBlock("c1", "x" * 5000),)))
    text = transcript(history)
    assert text.startswith("User: fix a.py\n\nAssistant: Reading it.\n\n[call read_file ")
    assert "[result: " in text and len(text) < 2000


# --- a session ----------------------------------------------------------------------


def _remembered(home, repo, summary="Run pytest -q.", notes="# Notes\n- pytest -q"):
    where = folder(home, repo)
    where.mkdir(parents=True, exist_ok=True)
    (where / SUMMARY_FILE).write_text(summary, encoding="utf-8")
    (where / NOTES_FILE).write_text(notes, encoding="utf-8")


def _system(wiring, at=0) -> str:
    return wiring.runtime.backend.seen[at].messages[0].blocks[0].text


def test_a_session_starts_with_the_summary_and_reads_the_notes(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    _remembered(home, repo)
    wiring = _wire(repo, home, [turn(call("r1", "read_memories", {})), turn(text="ok")],
                   approve=None)
    _repl(wiring, "hello")
    system = _system(wiring)
    assert MEMORY_HEADING + "Run pytest -q." in system
    assert system.index(MEMORY_HEADING) < system.index(wiring.runtime.context.environment)
    reply = [b for b in wiring.runtime.backend.seen[1].messages[-1].blocks][0]
    assert not reply.is_error and reply.content == "# Notes\n- pytest -q"


def test_read_memories_when_the_notes_are_gone(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    _remembered(home, repo, notes="")
    wiring = _wire(repo, home, [turn(call("r1", "read_memories", {})), turn(text="ok")],
                   approve=None)
    _repl(wiring, "hello")
    reply = wiring.runtime.backend.seen[1].messages[-1].blocks[0]
    assert reply.is_error and "there are no notes for this workspace yet" in reply.content


def test_a_sub_agent_starts_with_it_too(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    _remembered(home, repo)
    wiring = _wire(repo, home, [
        turn(call("s1", "spawn_agent", {"task": "Find A."})),
        turn(call("f9", "finish", {"status": "completed", "summary": "A is 1",
                                   "claims": []})),
        turn(text="A is 1.")])
    _repl(wiring, "use a sub-agent")
    assert MEMORY_HEADING in _system(wiring, 1)
    assert "read_memories" in [tool.name for tool in wiring.runtime.backend.seen[1].tools]


@pytest.mark.parametrize("text, trusted, used, kept", [
    ("", True, False, False),                                   # the feature off
    ("[features]\nmemories = true\n", False, False, False),     # not trusted
    ("[features]\nmemories = true\n[memories]\nuse_memories = false\n", True, False, True),
])
def test_when_a_session_has_none(repo, tmp_path, text, trusted, used, kept):
    home = _home(tmp_path, text, trust=repo if trusted else None)
    _remembered(home, repo)
    wiring = _wire(repo, home, [turn(text="ok")])
    _repl(wiring, "hello")
    assert (MEMORY_HEADING in _system(wiring)) is used
    assert "read_memories" not in wiring.runtime.registry.names()
    assert (wiring.memory_folder is not None) is kept


def test_slash_memories(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    _remembered(home, repo)
    wiring = _wire(repo, home, [turn(text="one"), turn(text="two"), turn(text="three")])
    shown = _shown(_repl(wiring, "/memories", "/memories use off", "first",
                         "/memories use on", "second", "/memories use", "/memories generate x"))
    assert "this session uses them, and is read for them once it has been idle 6h" in shown
    assert "0 session(s) kept; no pass yet" in shown
    assert "this session does not use its memories from the next message" in shown
    assert MEMORY_HEADING not in _system(wiring, 0)
    assert MEMORY_HEADING in _system(wiring, 1)
    offered = [[tool.name for tool in request.tools] for request in wiring.runtime.backend.seen]
    assert "read_memories" not in offered[0] and "read_memories" in offered[1]
    assert "read_memories" in wiring.runtime.registry.names()
    assert shown.count(USAGE) == 2


def test_slash_memories_when_there_are_none_to_use(repo, tmp_path):
    home = _home(tmp_path, trust=repo)
    wiring = _wire(repo, home, [])
    shown = _shown(_repl(wiring, "/memories", "/memories use on"))
    assert "this session has none to use yet" in shown
    assert "there are no memories yet" in shown
    assert MEMORY_HEADING not in wiring.runtime.context.memories


@pytest.mark.parametrize("text, trusted, said", [
    ("", True, TURN_ON),
    ("[features]\nmemories = true\n", False, NOT_TRUSTED),
])
def test_slash_memories_off(repo, tmp_path, text, trusted, said):
    wiring = _wire(repo, _home(tmp_path, text, trust=repo if trusted else None), [])
    assert said in _shown(_repl(wiring, "/memories"))


# --- foundry memories ---------------------------------------------------------------


@pytest.fixture()
def cli(tmp_path, monkeypatch, repo):
    home = _home(tmp_path, trust=repo)
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    backend = _Live([turn(text="- a"), turn(text=MERGED)])

    class _Key:
        def acquire(self):
            return self

        def reveal(self):
            return "k"
    monkeypatch.setattr(app, "_credential_source", lambda config, vault: _Key())
    monkeypatch.setattr(app, "make_backend", lambda config, key: backend)
    return home, backend


def test_update_reads_the_sessions_due(cli, repo, capsys):
    home, backend = cli
    _session(repo, home)
    capsys.readouterr()
    assert app.main(["memories", "update", "-C", str(repo)]) == 0
    assert "1 session(s) read, 1 with something to keep; the notes written again" in \
        capsys.readouterr().out
    assert len(backend.seen) == 2
    assert app.main(["memories", "show", "-C", str(repo)]) == 0
    shown = capsys.readouterr().out
    assert "1 session(s) kept; the last pass read 1" in " ".join(shown.split())
    # The summary a session starts with, then the notes, as written.
    assert shown.index("summary") < shown.index("Run the tests with pytest -q.") \
        < shown.index("notes") < shown.index("# Testing\n- pytest -q runs them")


def test_update_says_nothing_when_quiet_and_fails_on_an_error(cli, repo, capsys):
    home, backend = cli
    _session(repo, home)
    backend.turns[1] = turn(text="no mark")
    capsys.readouterr()
    assert app.main(["memories", "update", "--quiet", "-C", str(repo)]) != 0
    assert capsys.readouterr().out == ""


def test_update_only_where_trusted(cli, tmp_path, capsys):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert app.main(["memories", "update", "-C", str(elsewhere)]) != 0
    assert "memories are kept only for a workspace you trust" in \
        " ".join(capsys.readouterr().out.split())


def test_reset(cli, repo, capsys):
    home, _ = cli
    _session(repo, home)
    assert app.main(["memories", "update", "-C", str(repo)]) == 0
    assert app.main(["memories", "reset", "-C", str(repo)]) == 0
    assert "forgot the memories of" in " ".join(capsys.readouterr().out.split())
    assert _kept(home, repo) == [] and (folder(home, repo) / STATE_FILE).is_file()


# --- the pass as a session starts ---------------------------------------------------


def test_a_session_starts_the_pass_in_a_process_of_its_own(repo, tmp_path, monkeypatch):
    home = _home(tmp_path, trust=repo)
    started = []
    wiring = _wire(repo, home, [])
    with monkeypatch.context() as patch:
        # Only for the start: the session runs git through Popen too.
        patch.setattr(app.subprocess, "Popen",
                      lambda argv, **kwargs: started.append((argv, kwargs)))
        app.start_memory_pass(wiring, "work")
    wiring.session.close()
    (argv, kwargs), = started
    assert argv[1:3] == ["-P", "-c"]
    assert argv[4:] == ["memories", "update", "--quiet", "-C", str(wiring.workspace.root),
                        "-p", "work"]
    assert kwargs["cwd"] == str(home) and kwargs["stdout"] == app.subprocess.DEVNULL
    # It runs this Foundry, wherever it was imported from: here, the source
    # tree, which python -m foundry would not find from the Foundry folder.
    shown = app.subprocess.run([*argv[:4], "memories", "show", "-C", str(repo)],
                               cwd=str(home), capture_output=True, text=True,
                               encoding="utf-8", env={**os.environ, "FOUNDRY_HOME": str(home)},
                               timeout=120)
    assert shown.returncode == 0, shown.stderr
    assert "0 session(s) kept; no pass yet" in " ".join(shown.stdout.split())


@pytest.mark.parametrize("text, interactive, starts", [
    ("[features]\nmemories = true\n", True, True),
    ("[features]\nmemories = true\n", False, False),
    ("", True, False),
])
def test_which_sessions_start_it(repo, tmp_path, monkeypatch, text, interactive, starts):
    home = _home(tmp_path, text + '[backend]\nmodel = "fake"\nbase_url = '
                                  '"http://127.0.0.1:9/v1"\n', trust=repo)
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    started = []
    monkeypatch.setattr(app, "start_memory_pass",
                        lambda wiring, profile: started.append(profile))
    monkeypatch.setattr(app, "_interactive", lambda: interactive)
    monkeypatch.setattr(app, "_repl",
                        lambda wiring, console, first=None: TerminalStatus.CANCELLED)
    monkeypatch.setattr(app, "show_conversation", lambda wiring: None)
    app.main(["run", "-C", str(repo)])
    assert started == ([None] if starts else [])
