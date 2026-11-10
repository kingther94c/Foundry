"""Undo (D-036): a session's patches reverted from what its journal recorded.

The property that carries the feature: undo only ever puts back what the
session itself replaced, and only where the file still holds exactly what the
session wrote. Anything else -- an edit made since, a change between two
patches, content too sensitive to keep, a move it cannot finish -- is left as
it is and named.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys

from foundry.cli import app
from foundry.cli.repl import repl
from foundry.cli.wiring import _close
from foundry.core.events import TerminalStatus
from foundry.core.redaction import Redactor
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.files import ReadFile
from foundry.core.tools.patch import ApplyPatch
from foundry.core.undo import (
    CHANGED_BETWEEN,
    CHANGED_SINCE,
    NOT_KEPT,
    NOT_KEPT_CREDENTIAL,
    Change,
    apply_undo,
    plan_undo,
    read_source,
    recorded_changes,
    undo_payload,
)
from foundry.core.workspace import Workspace

from test_resume import _core, _wire, call, repo, turn  # noqa: F401  (repo is a fixture)

SECRET = "hunter2-correct-horse-battery-staple"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def envelope(*bodies: str) -> str:
    return "*** Begin Patch\n" + "\n".join(bodies) + "\n*** End Patch\n"


def update(path: str, old: str, new: str, *, move_to: str = "") -> str:
    move = f"*** Move to: {move_to}\n" if move_to else ""
    return (f"*** Update File: {path}\n{move}"
            f"<<<<<<< SEARCH\n{old}\n=======\n{new}\n>>>>>>> REPLACE")


def read(cid: str, path: str):
    return turn(call(cid, "read_file", {"path": path}))


def patch(cid: str, *bodies: str):
    return turn(call(cid, "apply_patch", {"patch": envelope(*bodies)}))


def tree(root) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _run(tmp_path, files: dict[str, str], script, *, redactor=None):
    runtime, session, repo = _core(tmp_path, [*script, turn(text="done")], redactor=redactor)
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8", newline="\n")
    runtime.run_turn("change things")
    return runtime, session, repo


def _results(session):
    return [r for r in SessionStore.read_records(session.path) if r.type == EventType.TOOL_RESULT]


def _undo(session, repo, *, last=False):
    records = list(SessionStore.read_records(session.path))
    plan = plan_undo(recorded_changes(records), Workspace(repo), session.artifacts, last=last)
    outcome = apply_undo(plan, Workspace(repo), session.artifacts)
    session.append(EventType.UNDO, undo_payload(plan, outcome, scope="last" if last else "all",
                                                by="test"))
    return plan, outcome


# --- what a patch records ------------------------------------------------------


def test_a_patch_records_what_each_file_held_before_and_after(tmp_path):
    runtime, session, repo = _run(tmp_path, {"a.py": "A = 1\n", "b.py": "B = 1\n"}, [
        read("r1", "a.py"), read("r2", "b.py"),
        patch("p1", update("a.py", "A = 1", "A = 2"), "*** Add File: new.py\n+NEW = 1"),
        patch("p2", "*** Delete File: b.py"),
        patch("p3", update("a.py", "A = 2", "A = 3", move_to="sub/c.py")),
    ])
    by_call = {r.payload["call_id"]: r.payload["changes"] for r in _results(session)
               if r.payload.get("changes")}
    assert set(by_call) == {"p1", "p2", "p3"}

    updated, added = by_call["p1"]
    assert updated["path"] == "a.py" and updated["kept"] is True
    assert session.artifacts.read_bytes(updated["before"]) == b"A = 1\n"
    assert updated["before_sha"] == sha(b"A = 1\n") and updated["after"] == sha(b"A = 2\n")
    assert added == {"path": "new.py", "before": None, "before_sha": None, "kept": True,
                     "after": sha((repo / "new.py").read_bytes())}

    (deleted,) = by_call["p2"]
    assert deleted["after"] is None and deleted["before_sha"] == sha(b"B = 1\n")

    source, target = by_call["p3"]
    assert (source["path"], source["after"], source["moved_to"]) == ("a.py", None, "sub/c.py")
    assert (target["path"], target["before"], target["moved_from"]) == ("sub/c.py", None, "a.py")
    assert target["after"] == sha((repo / "sub" / "c.py").read_bytes())
    # The model never sees any of it.
    replies = [r for r in SessionStore.read_records(session.path) if r.type == EventType.TOOL_REPLY]
    assert not any("before_sha" in r.payload["content"] for r in replies)


def test_a_patch_still_applies_where_nothing_can_be_kept(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("A = 1\n", encoding="utf-8", newline="\n")
    ctx = ToolContext(workspace=Workspace(repo), artifacts=None, read_tracker=ReadTracker())
    ReadFile().execute(ReadFile().validate({"path": "a.py"}), ctx)
    tool = ApplyPatch()
    out = tool.execute(tool.validate({"patch": envelope(update("a.py", "A = 1", "A = 2"))}), ctx)
    assert not out.is_error and (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"
    (change,) = out.metadata["changes"]
    assert change["kept"] is False and change["before"] is None


def test_a_partly_applied_patch_records_what_landed(tmp_path):
    runtime, session, repo = _run(tmp_path, {"a.py": "A = 1\n", "b.py": "B = 1\n"}, [
        read("r1", "a.py"), read("r2", "b.py"),
        patch("p1", update("a.py", "A = 1", "A = 2"), update("b.py", "B = 9", "B = 10")),
    ])
    (result,) = [r for r in _results(session) if r.payload["call_id"] == "p1"]
    assert result.payload["is_error"] is True
    assert [c["path"] for c in result.payload["changes"]] == ["a.py"]

    plan, outcome = _undo(session, repo)
    assert [s.path for s in outcome.reverted] == ["a.py"]
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"


# --- undoing -------------------------------------------------------------------


def test_undo_puts_back_updated_added_deleted_and_moved_files(tmp_path):
    files = {"a.py": "A = 1\n", "b.py": "B = 1\n"}
    runtime, session, repo = _run(tmp_path, files, [
        read("r1", "a.py"), read("r2", "b.py"),
        patch("p1", update("a.py", "A = 1", "A = 2"), "*** Add File: new.py\n+NEW = 1"),
        patch("p2", "*** Delete File: b.py"),
        patch("p3", update("a.py", "A = 2", "A = 3", move_to="sub/c.py")),
    ])
    assert set(tree(repo)) == {"new.py", "sub/c.py"}

    plan, outcome = _undo(session, repo)
    assert plan.skipped == () and outcome.failed == ()
    assert {s.path: s.action for s in outcome.reverted} == {
        "a.py": "restore", "b.py": "restore", "new.py": "remove", "sub/c.py": "remove"}
    assert tree(repo) == {name: text.encode() for name, text in files.items()}

    # Recorded as undone: there is nothing left to undo.
    records = list(SessionStore.read_records(session.path))
    assert recorded_changes(records) == []
    (undo,) = [r for r in records if r.type == EventType.UNDO]
    assert undo.payload["scope"] == "all" and len(undo.payload["reverted"]) == 5


def test_a_file_edited_since_is_left_alone_with_everything_before(tmp_path):
    runtime, session, repo = _run(tmp_path, {"a.py": "A = 1\n", "b.py": "B = 1\n"}, [
        read("r1", "a.py"), read("r2", "b.py"),
        patch("p1", update("a.py", "A = 1", "A = 2")),
        patch("p2", update("a.py", "A = 2", "A = 3"), update("b.py", "B = 1", "B = 2")),
    ])
    (repo / "a.py").write_text("A = 3  # the user's edit\n", encoding="utf-8", newline="\n")

    plan, outcome = _undo(session, repo)
    (skip,) = plan.skipped
    assert (skip.path, skip.reason) == ("a.py", CHANGED_SINCE)
    assert len(skip.changes) == 2  # the earlier patch too: undoing it would discard the edit
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 3  # the user's edit\n"
    assert (repo / "b.py").read_text(encoding="utf-8") == "B = 1\n"
    # Still pending: an undo later, once the edit is gone, can take it.
    assert {c.path for c in recorded_changes(SessionStore.read_records(session.path))} == {"a.py"}


def test_a_change_between_two_patches_stops_undo_there(tmp_path):
    runtime, session, repo = _core(tmp_path, [
        read("r1", "a.py"), patch("p1", update("a.py", "A = 1", "A = 2")), turn(text="done"),
        read("r2", "a.py"), patch("p2", update("a.py", "A = 3", "A = 4")), turn(text="done"),
    ])
    (repo / "a.py").write_text("A = 1\n", encoding="utf-8", newline="\n")
    runtime.run_turn("make A 2")
    (repo / "a.py").write_text("A = 3\n", encoding="utf-8", newline="\n")  # as a command might
    runtime.run_turn("make A 4")

    plan, outcome = _undo(session, repo)
    # Back to what the outside change left, not to before the session.
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 3\n"
    (skip,) = plan.skipped
    assert skip.reason == CHANGED_BETWEEN and [c.call_id for c in skip.changes] == ["p1"]


def test_content_holding_a_credential_is_never_kept_or_restored(tmp_path):
    redactor = Redactor()
    redactor.register(SECRET)
    runtime, session, repo = _run(tmp_path, {"cfg.py": f"KEY = '{SECRET}'\nA = 1\n"}, [
        read("r1", "cfg.py"), patch("p1", update("cfg.py", "A = 1", "A = 2")),
    ], redactor=redactor)
    (change,) = [c for r in _results(session) for c in r.payload.get("changes", [])]
    assert change["kept"] is False and change["before"] is None
    for stored in (session.dir / "artifacts").iterdir():
        assert SECRET.encode() not in stored.read_bytes()

    plan, outcome = _undo(session, repo)
    assert outcome.reverted == () and plan.skipped[0].reason == NOT_KEPT_CREDENTIAL
    assert f"KEY = '{SECRET}'\nA = 2\n" == (repo / "cfg.py").read_text(encoding="utf-8")


def test_last_takes_the_newest_patch_and_then_the_one_before(tmp_path):
    runtime, session, repo = _run(tmp_path, {"a.py": "A = 1\n", "b.py": "B = 1\n"}, [
        read("r1", "a.py"), read("r2", "b.py"),
        patch("p1", update("a.py", "A = 1", "A = 2")),
        patch("p2", update("b.py", "B = 1", "B = 2"), update("a.py", "A = 2", "A = 3")),
    ])
    _undo(session, repo, last=True)
    assert tree(repo) == {"a.py": b"A = 2\n", "b.py": b"B = 1\n"}
    _undo(session, repo, last=True)
    assert tree(repo) == {"a.py": b"A = 1\n", "b.py": b"B = 1\n"}
    plan, _ = _undo(session, repo, last=True)
    assert plan.empty


def test_a_move_is_undone_whole_or_not_at_all(tmp_path):
    runtime, session, repo = _run(tmp_path, {"a.py": "A = 1\n"}, [
        read("r1", "a.py"), patch("p1", update("a.py", "A = 1", "A = 2", move_to="c.py")),
    ])
    (repo / "a.py").write_text("a new file where the old one was\n", encoding="utf-8", newline="\n")

    plan, outcome = _undo(session, repo)
    assert outcome.reverted == ()
    reasons = {s.path: s.reason for s in plan.skipped}
    assert reasons["a.py"] == CHANGED_SINCE and "moved" in reasons["c.py"]
    assert tree(repo) == {"a.py": b"a new file where the old one was\n", "c.py": b"A = 2\n"}


def test_a_file_changed_while_undo_runs_is_left_alone(tmp_path):
    runtime, session, repo = _run(tmp_path, {"a.py": "A = 1\n"}, [
        read("r1", "a.py"), patch("p1", update("a.py", "A = 1", "A = 2")),
    ])
    workspace = Workspace(repo)
    plan = plan_undo(recorded_changes(SessionStore.read_records(session.path)), workspace,
                     session.artifacts)
    (repo / "a.py").write_text("A = 5\n", encoding="utf-8", newline="\n")
    outcome = apply_undo(plan, workspace, session.artifacts)
    assert outcome.reverted == () and outcome.failed[0].reason == CHANGED_SINCE
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 5\n"


def test_damaged_or_missing_kept_content_is_not_written_back(tmp_path):
    runtime, session, repo = _run(tmp_path, {"a.py": "A = 1\n", "b.py": "B = 1\n"}, [
        read("r1", "a.py"), read("r2", "b.py"),
        patch("p1", update("a.py", "A = 1", "A = 2"), update("b.py", "B = 1", "B = 2")),
    ])
    changes = {c["path"]: c for r in _results(session) for c in r.payload.get("changes", [])}
    (session.dir / "artifacts" / changes["a.py"]["before"]).write_bytes(b"tampered\n")

    plan, outcome = _undo(session, repo)
    assert [s.path for s in outcome.failed] == ["a.py"] and "damaged" in outcome.failed[0].reason
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"
    assert (repo / "b.py").read_text(encoding="utf-8") == "B = 1\n"

    missing = Change(result=1, call_id="x", path="b.py", before="0" * 64,
                     before_sha=sha(b"B = 1\n"), after=sha(b"B = 1\n"), kept=True)
    plan = plan_undo([missing], Workspace(repo), session.artifacts)
    assert plan.skipped[0].reason == NOT_KEPT


def test_a_recorded_path_outside_the_workspace_is_refused(tmp_path):
    runtime, session, repo = _run(tmp_path, {}, [])
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me\n", encoding="utf-8", newline="\n")
    forged = Change(result=1, call_id="x", path="../outside.txt", before=None, before_sha=None,
                    after=sha(b"keep me\n"), kept=True)
    plan = plan_undo([forged], Workspace(repo), session.artifacts)
    assert plan.steps == () and plan.skipped
    assert outside.read_text(encoding="utf-8") == "keep me\n"


def test_a_journal_from_before_undo_has_nothing_to_undo(tmp_path):
    runtime, session, repo = _run(tmp_path, {"a.py": "A = 1\n"}, [
        read("r1", "a.py"), patch("p1", update("a.py", "A = 1", "A = 2")),
    ])
    session.close()
    lines = []
    for line in session.path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        record["payload"].pop("changes", None)
        lines.append(json.dumps(record))
    session.path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    source = read_source(session.path)
    assert source.changes == () and "a.py" in source.patched


# --- the commands ---------------------------------------------------------------


PATCH_A = envelope(update("a.py", "A = 1", "A = 2"))


def _patched_session(repo, home):
    wiring = _wire(repo, home, [turn(call("r1", "read_file", {"path": "a.py"})),
                                turn(call("p1", "apply_patch", {"patch": PATCH_A})),
                                turn(text="A is 2 now")])
    wiring.runtime.run_turn("make A 2")
    _close(wiring, TerminalStatus.CANCELLED, "session ended")
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"
    return wiring.session.session_id


def test_undo_through_main(repo, tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    session_id = _patched_session(repo, home)
    (repo / "generated.txt").write_text("from a command\n", encoding="utf-8", newline="\n")
    workspace = ["--workspace", str(repo)]
    journal = home / "sessions" / session_id / "events.jsonl"

    assert app.main(["undo", "--dry-run", *workspace]) == 0
    out = capsys.readouterr().out
    assert "restore" in out and "a.py" in out and "dry run" in out
    assert "generated.txt" in out  # named, not undone
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"

    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    size = journal.stat().st_size
    assert app.main(["undo", *workspace]) == 2
    assert "--yes" in capsys.readouterr().out
    assert journal.stat().st_size == size  # nothing written

    assert app.main(["undo", "--yes", *workspace]) == 0
    assert "undone" in capsys.readouterr().out
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    assert (repo / "generated.txt").is_file()
    undo = [r for r in SessionStore.read_records(journal) if r.type == EventType.UNDO]
    assert len(undo) == 1 and undo[0].payload["by"] == "cli"

    assert app.main(["undo", "--yes", *workspace]) == 0
    assert "nothing to undo" in capsys.readouterr().out


def test_undo_refuses_a_session_in_use_or_another_workspace(repo, tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    session_id = _patched_session(repo, home)

    with SessionStore(home / "sessions", session_id=session_id, resume=True):
        assert app.main(["undo", "--yes", "--workspace", str(repo)]) == 1
    assert "in use" in capsys.readouterr().out and (repo / "a.py").read_text(
        encoding="utf-8") == "A = 2\n"

    other = tmp_path / "other"
    other.mkdir()
    assert app.main(["undo", session_id, "--yes", "--workspace", str(other)]) == 1
    assert "worked in" in capsys.readouterr().out
    assert app.main(["undo", "--yes", "--workspace", str(other)]) == 1
    assert "no recorded session" in capsys.readouterr().out
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"


class _Typist:
    def __init__(self, lines):
        self.lines = list(lines)
        self.printed: list[str] = []

    def input(self, prompt=""):
        return self.lines.pop(0)

    def print(self, *args, **kwargs):
        self.printed.append(" ".join(str(a) for a in args))


def test_slash_undo_reverts_the_last_patch_and_tells_the_model(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("r1", "read_file", {"path": "a.py"})),
        turn(call("p1", "apply_patch", {"patch": PATCH_A})),
        turn(text="A is 2 now"),
        turn(text="A is 1 again"),
    ])
    typist = _Typist(["make A 2", "/undo", "y", "what is A?", "/exit"])
    status = repl(wiring, typist)
    _close(wiring, status, "session ended")

    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    told = wiring.runtime.backend.seen[-1].messages[-1].blocks[0].text
    assert told.startswith("The user undid changes") and "a.py" in told
    assert told.endswith("what is A?")
    # Read again before patching it; commands from before the undo cannot vouch.
    assert not wiring.runtime.tool_ctx.read_tracker.has_read("a.py")
    (undo,) = [r for r in SessionStore.read_records(wiring.session.path)
               if r.type == EventType.UNDO]
    assert undo.payload["by"] == "repl" and wiring.runtime._last_mutation == undo.ordinal


def test_slash_undo_asks_first_and_says_when_there_is_nothing(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("r1", "read_file", {"path": "a.py"})),
        turn(call("p1", "apply_patch", {"patch": PATCH_A})),
        turn(text="done"),
    ])
    typist = _Typist(["/undo", "make A 2", "/undo", "n", "/exit"])
    _close(wiring, repl(wiring, typist), "session ended")
    assert any("nothing to undo" in line for line in typist.printed)
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"
    assert not [r for r in SessionStore.read_records(wiring.session.path)
                if r.type == EventType.UNDO]


def test_a_resumed_session_is_told_what_was_undone_after_it_stopped(repo, tmp_path, monkeypatch,
                                                                    capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    _patched_session(repo, home)
    assert app.main(["undo", "--yes", "--workspace", str(repo)]) == 0

    wiring = _wire(repo, home, [turn(text="ok")], resume="last")
    try:
        assert wiring.resumed.undone == ("a.py",)
        assert "undid patches" in wiring.note and "a.py" in wiring.note
        assert "a.py" not in wiring.outside  # back as it was: not someone else's change
    finally:
        _close(wiring, TerminalStatus.CANCELLED, "session ended")
