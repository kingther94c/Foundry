"""`foundry mcp-server` keeps one task to a repository, submodules included,
and a cancel is never lost (D-156).

P2's review of the server (D-103) found:

* A task's place was `git rev-parse --show-toplevel`: a submodule's own
  root. A task in the superproject writes the submodule's files too, so a
  task there and one in the submodule were two tasks writing one file.
* A reply to a session held open registered its request after `_work` had
  looked for a cancel: a cancel in between found no runtime to stop, and the
  task ran to its end with nobody waiting for it.
"""

from __future__ import annotations

import threading

from foundry.cli import mcp_server
from foundry.cli.mcp_server import Server, _place
from foundry.core.tools.git import run_git

from test_mcp_server import (  # noqa: F401  (fixtures)
    _asked,
    _Client,
    _finish,
    _only_session,
    _repository,
    client,
    home,
    model,
)
from test_resume import repo  # noqa: F401  (fixture)


def _with_submodule(outer, inner, name="sub"):
    """``inner`` added to ``outer`` as the submodule ``name``."""
    code, out, err = run_git(["-c", "protocol.file.allow=always", "submodule", "add",
                              str(inner), name], outer)
    assert code == 0, out + err
    run_git(["commit", "-m", f"add {name}"], outer)
    return outer / name


# --- one task to a repository --------------------------------------------------------------


def test_a_submodule_is_its_superproject_s_place(tmp_path):
    outer = _repository(tmp_path / "outer")
    sub = _with_submodule(outer, _repository(tmp_path / "inner"))
    (sub / "deep").mkdir()
    (outer / "docs").mkdir()
    assert _place(sub) == _place(sub / "deep") == _place(outer) == _place(outer / "docs")
    # Its own clone elsewhere is another place.
    assert _place(tmp_path / "inner") != _place(outer)


def test_a_submodule_of_a_submodule_is_the_outermost_s_place(tmp_path):
    middle = _repository(tmp_path / "middle")
    _with_submodule(middle, _repository(tmp_path / "leaf"), "leaf")
    outer = _repository(tmp_path / "outer")
    sub = _with_submodule(outer, middle, "middle")
    code, out, err = run_git(["-c", "protocol.file.allow=always", "submodule", "update",
                              "--init", "--recursive"], outer)
    assert code == 0, out + err
    assert (sub / "leaf" / "o.py").is_file()
    assert _place(sub / "leaf") == _place(sub) == _place(outer)


def test_how_far_up_is_bounded(tmp_path, monkeypatch):
    outer = _repository(tmp_path / "outer")
    sub = _with_submodule(outer, _repository(tmp_path / "inner"))
    monkeypatch.setattr(mcp_server, "MAX_SUPERPROJECTS", 0)
    assert _place(sub) != _place(outer)


def test_a_place_that_is_no_repository_is_itself(tmp_path):
    bare = tmp_path / "plain"
    bare.mkdir()
    assert _place(bare) == _place(bare / ".")


def test_one_task_at_a_time_in_a_superproject_and_its_submodule(home, model, tmp_path):
    outer = _repository(tmp_path / "outer")
    sub = _with_submodule(outer, _repository(tmp_path / "inner"))
    made = _Client(home, outer)
    try:
        model.turns, model.gate = [_finish("done")], threading.Event()
        made.start(1, "foundry", prompt="one")
        _asked(model, 1)
        busy = made.call(2, "foundry", prompt="two", cwd=str(sub))
        assert busy["isError"] is True and busy["content"][0]["text"].startswith(
            "another Foundry task is at work in ")
        model.gate.set()
        assert made.answer(1)["result"]["isError"] is False
        # Once it is done, the submodule may have its turn.
        assert made.call(3, "foundry", prompt="three", cwd=str(sub))["isError"] is False
    finally:
        made.server.close()


# --- a cancel is never lost ----------------------------------------------------------------


def test_a_cancel_before_a_reply_takes_its_session_stops_the_task(client, model, home,
                                                                 monkeypatch):
    model.turns = [_finish("first"), _finish("never")]
    first = client.call(1, "foundry", prompt="one")["structuredContent"]
    real = Server._take

    def cancelled_first(self, rid, name, task):
        # The cancel comes after _work looked for one, before the session is taken.
        self._cancel(rid)
        return real(self, rid, name, task)

    monkeypatch.setattr(Server, "_take", cancelled_first)
    client.start(2, "foundry-reply", sessionId=first["sessionId"], prompt="two")
    client.settle()
    assert client.answers(2) == [] and len(model.requests) == 1
    assert client.server._requests == {} and client.server._cancelled == set()
    # The cancel is spent with the task: the session goes on.
    monkeypatch.setattr(Server, "_take", real)
    model.turns = [_finish("went on")]
    model.index = 0
    assert client.call(3, "foundry-reply", sessionId=first["sessionId"], prompt="three")[
        "structuredContent"]["text"] == "went on"
    assert _only_session(home) == first["sessionId"]


def test_a_cancel_of_another_request_leaves_a_reply_be(client, model, monkeypatch):
    model.turns = [_finish("first"), _finish("second")]
    first = client.call(1, "foundry", prompt="one")["structuredContent"]
    real = Server._take

    def other_cancelled(self, rid, name, task):
        with self._lock:
            self._requests[99] = None
        self._cancel(99)
        return real(self, rid, name, task)

    monkeypatch.setattr(Server, "_take", other_cancelled)
    assert client.call(2, "foundry-reply", sessionId=first["sessionId"], prompt="two")[
        "structuredContent"]["text"] == "second"
