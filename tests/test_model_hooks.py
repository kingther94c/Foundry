"""PreModelRequest and PostModelRequest hooks, as codex's (D-122): told of
each try of a model request -- retries and a compaction's included -- with
how it ended, and never read."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry.core.backends.replay import _emit
from foundry.core.conversation import Capabilities, ModelTurn, StopReason, Usage
from foundry.core.errors import ProtocolError, StreamDropped, TransientError
from foundry.core.events import TerminalStatus
from foundry.core.hooks import Hook, HookRun, HookSet, parse_hooks, read_model_request
from foundry.core.session import EventType
from foundry.core.winapi import IS_WINDOWS
from test_hooks import (  # noqa: F401  (fixtures)
    Scripted,
    _group,
    _hooks_json,
    _notices,
    _records,
    _shown,
    _wire,
    call,
    home,
    machine,
    repo,
    turn,
)

pytestmark = pytest.mark.skipif(not IS_WINDOWS, reason="hooks run through cmd.exe here")

PRE = Hook(event="PreModelRequest", matcher="", command="pre")


def _read(stdout="", **fields):
    return read_model_request([HookRun(PRE, exit_code=fields.pop("exit_code", 0),
                                       stdout=stdout, **fields)])


def _told(hook) -> list[dict]:
    return [c["stdin"] for c in hook.calls()]


def _both(home, tmp_path, **answers):
    """One hook for both events, logging each; its log is the order they ran in."""
    hook = Scripted(tmp_path / "h")
    if answers:
        hook.answer(**answers)
    _hooks_json(home, {"PreModelRequest": [_group(hook.command)],
                       "PostModelRequest": [_group(hook.command)]})
    return hook


def _events(hook) -> list[str]:
    return [(t["hook_event_name"], t.get("status")) for t in _told(hook)]


USED = {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5,
        "reasoning_output_tokens": 0, "total_tokens": 15}


# --- what their answers mean ------------------------------------------------------


@pytest.mark.parametrize("stdout", [
    "", "plain words", json.dumps({"continue": False, "stopReason": "x", "decision": "block",
                                   "systemMessage": "m"}), "{broken"])
def test_what_a_hook_prints_is_not_read(stdout):
    verdict = _read(stdout)
    assert (verdict.block, verdict.stops, verdict.context, verdict.messages,
            verdict.failures) == ([], [], [], [], [])
    assert verdict.records[0]["outcome"] == "completed"


def test_stderr_is_shown_and_a_failure_says_why():
    assert _read(stderr=" slow gateway \n").messages == ["slow gateway"]
    assert _read(exit_code=2, stderr="no").failures == ["`pre` failed: no"]
    assert _read(exit_code=1).failures == ["`pre` exited with code 1"]
    assert _read(exit_code=None, error="timed out after 5s").failures == [
        "`pre` timed out after 5s"]
    assert _read(exit_code=2, stderr="no").messages == []


def test_they_run_whatever_their_matcher_and_are_listed_without_one(repo, home):
    hooks = HookSet(parse_hooks({"PreModelRequest": [_group("a", "Bash")],
                                 "PostModelRequest": [_group("b", "nothing")]}, "f"))
    assert [h.command for h in hooks.matching("PreModelRequest", ())] == ["a"]
    assert [h.command for h in hooks.matching("PostModelRequest", ())] == ["b"]
    _hooks_json(home, {"PostModelRequest": [_group("log.cmd")]})
    assert _shown(_wire(repo, home, [])).splitlines() == [
        f"PostModelRequest  log.cmd ({home / 'hooks.json'}, 600s)"]


# --- around each request ----------------------------------------------------------------


def test_each_request_is_told_before_and_after(repo, home, tmp_path):
    hook = _both(home, tmp_path)
    wiring = _wire(repo, home, [turn(call("c1", "read_file", {"path": "a.py"})),
                                turn(text="done")])
    wiring.runtime.run_turn("read it")
    assert _events(hook) == [("PreModelRequest", None), ("PostModelRequest", "completed")] * 2
    before, after = _told(hook)[:2]
    assert Path(before.pop("cwd")) == Path(wiring.workspace.root)
    assert before == {"session_id": wiring.session.session_id,
                      "transcript_path": str(wiring.session.path),
                      "hook_event_name": "PreModelRequest", "model": "fake", "turn_id": "1"}
    after.pop("cwd")
    assert after == {**before, "hook_event_name": "PostModelRequest", "status": "completed",
                     "error": None, "token_usage": USED}
    requests = [r for r in _records(wiring, EventType.HOOK)]
    assert [r["event"] for r in requests] == ["PreModelRequest", "PostModelRequest"] * 2


def test_a_failed_request_is_told_why(repo, home, tmp_path):
    hook = _both(home, tmp_path)
    wiring = _wire(repo, home, [])          # the scripted model has nothing to say
    outcome = wiring.runtime.run_turn("hello")
    assert outcome.status is TerminalStatus.FAILED
    (_, after) = _told(hook)
    assert after["status"] == "failed" and after["token_usage"] is None
    assert "scripted backend exhausted" in after["error"]


class _Model:
    """A model whose first ``broken`` answers fail as ``failure`` says."""

    def __init__(self, failure, broken=1, on_stream=None):
        self.failure, self.broken, self.on_stream = failure, broken, on_stream
        self.seen = []

    def capabilities(self):
        return Capabilities()

    def stream_turn(self, request):
        self.seen.append(request)
        if self.on_stream is not None:
            self.on_stream(self)
        if len(self.seen) <= self.broken:
            raise self.failure
        return _emit(ModelTurn(text="done", usage=Usage(10, 5),
                               stop_reason=StopReason.END_TURN))


def test_a_stream_asked_again_is_two_tries(repo, home, tmp_path):
    hook = _both(home, tmp_path)
    wiring = _wire(repo, home, [])
    wiring.runtime.backend = _Model(StreamDropped("the stream broke"))
    wiring.runtime.sleep = lambda seconds: None
    assert wiring.runtime.run_turn("hello").text == "done"
    assert _events(hook) == [("PreModelRequest", None), ("PostModelRequest", "failed"),
                             ("PreModelRequest", None), ("PostModelRequest", "completed")]
    assert "the stream broke" in _told(hook)[1]["error"]


def test_a_backends_own_retry_is_a_try_too(repo, home, tmp_path):
    hook = _both(home, tmp_path)
    wiring = _wire(repo, home, [])

    def retried(model):
        if len(model.seen) == 1:      # as the connection's retry says it is waiting
            wiring.runtime._retrying(TransientError("HTTP 503"), 2, 4, 0.0)

    wiring.runtime.backend = _Model(None, broken=0, on_stream=retried)
    assert wiring.runtime.run_turn("hello").text == "done"
    assert _events(hook) == [("PreModelRequest", None), ("PostModelRequest", "failed"),
                             ("PreModelRequest", None), ("PostModelRequest", "completed")]
    assert _told(hook)[1]["error"] == "HTTP 503"


def test_a_cancelled_request_is_told_and_its_hooks_run_to_their_end(repo, home, tmp_path):
    hook = _both(home, tmp_path)
    wiring = _wire(repo, home, [])
    wiring.runtime.backend = _Model(ProtocolError("never"), broken=0,
                                    on_stream=lambda model: wiring.runtime.cancel())
    assert wiring.runtime.run_turn("hello").status is TerminalStatus.CANCELLED
    assert _events(hook)[-1] == ("PostModelRequest", "cancelled")
    assert _told(hook)[-1]["error"] is None


def test_a_cancel_already_honoured_does_not_cut_the_post_hook_short(repo, home, tmp_path):
    # It takes a while, and says so only at its end.
    _both(home, tmp_path, stderr="told", sleep=0.5)
    wiring = _wire(repo, home, [])
    wiring.runtime.backend = _Model(ProtocolError("never"), broken=0,
                                    on_stream=lambda model: wiring.runtime.cancel())
    assert wiring.runtime.run_turn("hello").status is TerminalStatus.CANCELLED
    assert "PostModelRequest hook: told" in _notices(wiring)


def test_a_compactions_request_is_told_too(repo, home, tmp_path):
    hook = _both(home, tmp_path)
    wiring = _wire(repo, home, [turn(text="ok"), turn(text="the summary")])
    wiring.runtime.run_turn("explain a.py")
    wiring.runtime.compact(auto=False)
    assert _events(hook) == [("PreModelRequest", None), ("PostModelRequest", "completed")] * 2


def test_a_hook_cannot_stop_or_change_a_request(repo, home, tmp_path):
    _both(home, tmp_path, stdout=json.dumps({"continue": False, "decision": "block",
                                             "reason": "no", "systemMessage": "m"}),
          stderr="logged")
    wiring = _wire(repo, home, [turn(text="done")])
    outcome = wiring.runtime.run_turn("hello")
    assert outcome.text == "done"
    assert _notices(wiring).count("PreModelRequest hook: logged") == 1
    assert "PostModelRequest hook: logged" in _notices(wiring)
    assert all("m" != n.split(": ")[-1] for n in _notices(wiring))


def test_no_hooks_change_nothing(repo, home):
    wiring = _wire(repo, home, [turn(text="done")])
    assert wiring.runtime.run_turn("hello").text == "done"
    assert _records(wiring, EventType.HOOK) == []
