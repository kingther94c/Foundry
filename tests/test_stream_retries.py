"""A response stream cut part-way is asked again, as codex's
``stream_max_retries`` (D-099).

A connection dropped, stalled or closed before the turn was finished ended the
task ``blocked`` -- after the answer had begun on screen -- and the user had to
ask again by hand; only a failure before the stream began was retried. Nothing
of a turn is acted on before it finishes, so the same request is safe to send
again: the runtime does, up to ``[backend] stream_max_retries`` times (5),
saying so each time, and saying when what was on screen is not kept.

What it may not do: keep any of the cut answer, re-ask a failure that had the
request's own retries already (which would multiply them), or keep asking
after the task was cancelled.
"""

from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from rich.console import Console

from foundry.cli.wiring import build
from foundry.core.config import load_config, parse_settings
from foundry.core.conversation import Role
from foundry.core.errors import ConfigError, StreamDropped, TransientError
from foundry.core.events import Notice, TerminalStatus
from foundry.core.httpc import open_retrying_stream
from foundry.core.session import EventType, SessionStore

from test_anthropic_wiring import _home
from test_cli_e2e import repo  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-stream")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-stream")


# --- what each protocol's server sends -----------------------------------------------------


def _data(*events):
    return b"".join(f"data: {json.dumps(e)}\n\n".encode() for e in events)


def _named(*events):
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


_START = {"type": "message_start", "message": {
    "model": "claude-opus-5-5", "usage": {"input_tokens": 3, "output_tokens": 1}}}


def _claude_text(text):
    return [{"type": "content_block_start", "index": 0,
             "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": text}}]


STREAMS = {
    "openai_compat": {
        "cut": _data({"model": "m", "choices": [{"delta": {"content": "The answer is "}}]}),
        "empty": _data({"model": "m", "choices": [{"delta": {}}]}),
        "full": _data({"model": "m", "choices": [{"delta": {"content": "A is 1"}}]},
                      {"choices": [{"delta": {}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 3, "completion_tokens": 2}})
        + b"data: [DONE]\n\n",
    },
    "responses": {
        "cut": _data({"type": "response.output_text.delta", "delta": "The answer is "}),
        "empty": _data({"type": "response.in_progress"}),
        "full": _data({"type": "response.output_text.delta", "delta": "A is 1"},
                      {"type": "response.completed",
                       "response": {"usage": {"input_tokens": 3, "output_tokens": 2}}}),
    },
    "anthropic_messages": {
        "cut": _named(_START, *_claude_text("The answer is ")),
        "empty": _named(_START),
        "full": _named(_START, *_claude_text("A is 1"),
                       {"type": "content_block_stop", "index": 0},
                       {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                        "usage": {"output_tokens": 2}},
                       {"type": "message_stop"}),
    },
}
PROTOCOLS = sorted(STREAMS)


class _Server(BaseHTTPRequestHandler):
    """Answers each POST with the next scripted stream, then the full one."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.bodies.append(json.loads(self.rfile.read(
            int(self.headers.get("Content-Length", 0)))))
        reply = self.server.script.pop(0) if self.server.script else "full"
        if isinstance(reply, int):
            self.send_response(reply)
            self.send_header("Retry-After", "0")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": {"message": "busy"}}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        # HTTP/1.0: the body ends where the connection does, as a cut one would.
        self.wfile.write(reply if isinstance(reply, bytes)
                         else STREAMS[self.server.protocol][reply])


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Server)
    httpd.bodies, httpd.script, httpd.protocol = [], [], "openai_compat"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _session(repo, tmp_path, server, protocol, *script, extra=""):
    server.protocol, server.script = protocol, list(script)
    host, port = server.server_address
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text(
        f'[backend]\nprotocol = "{protocol}"\nbase_url = "http://{host}:{port}/v1"\n'
        f'model = "m"\nrequest_max_retries = 2\n{extra}', encoding="utf-8")
    out = io.StringIO()
    wiring = build(repo, home=home, console=Console(file=out, width=300))
    slept, notices = [], []
    wiring.runtime.sleep = slept.append
    wiring.runtime.events.subscribe(
        lambda event: notices.append(event.text) if isinstance(event, Notice) else None)
    return wiring, out, slept, notices


def _records(wiring, kind):
    return [r.payload for r in SessionStore.read_records(wiring.session.path) if r.type == kind]


# --- a cut answer is asked again -----------------------------------------------------------


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_a_cut_answer_is_asked_again(repo, tmp_path, server, protocol):
    wiring, out, slept, notices = _session(repo, tmp_path, server, protocol, "cut")
    outcome = wiring.runtime.run_turn("what is A?")
    wiring.session.close()
    assert outcome.text == "A is 1"
    assert len(server.bodies) == 2 and server.bodies[0] == server.bodies[1]
    assert slept == [1.0]
    [told] = [n for n in notices if "asking again" in n]
    assert told.endswith("; asking again in 1s (try 2 of 6); the answer above was cut off "
                         "and is not kept")
    # None of the cut answer is kept.
    said = [m for m in wiring.runtime.context.history if m.role is Role.ASSISTANT]
    assert [m.blocks[0].text for m in said] == ["A is 1"]
    [retry] = _records(wiring, EventType.MODEL_RETRY)
    assert (retry["attempt"], retry["attempts"], retry["delay"], retry["cut"]) == (2, 6, 1.0, True)
    assert len(_records(wiring, EventType.MODEL_REQUEST)) == 2
    assert len(_records(wiring, EventType.MODEL_RESPONSE)) == 1
    # On screen: what was cut, then why, then the answer.
    shown = out.getvalue()
    assert shown.index("The answer is ") < shown.index("asking again") < shown.index("A is 1")


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_a_cut_before_any_text_says_nothing_was_shown(repo, tmp_path, server, protocol):
    wiring, _, _, notices = _session(repo, tmp_path, server, protocol, "empty")
    assert wiring.runtime.run_turn("what is A?").text == "A is 1"
    wiring.session.close()
    [told] = [n for n in notices if "asking again" in n]
    assert told.endswith("(try 2 of 6)")


def test_claude_s_overloaded_error_part_way_is_asked_again(repo, tmp_path, server):
    overloaded = _named(_START, *_claude_text("The answer"),
                        {"type": "error", "error": {"type": "overloaded_error",
                                                    "message": "Overloaded"}})
    wiring, _, slept, notices = _session(repo, tmp_path, server, "anthropic_messages",
                                         overloaded)
    assert wiring.runtime.run_turn("what is A?").text == "A is 1"
    wiring.session.close()
    assert slept == [1.0]
    assert any(n.startswith("the API reported overloaded_error: Overloaded; asking again")
               for n in notices)


def test_a_chat_stream_closed_with_no_answer_is_asked_again(repo, tmp_path, server):
    """A gateway that faults and sends only its terminator: no answer at all."""
    wiring, _, slept, notices = _session(repo, tmp_path, server, "openai_compat",
                                         b"data: [DONE]\n\n")
    assert wiring.runtime.run_turn("what is A?").text == "A is 1"
    wiring.session.close()
    assert slept == [1.0]
    assert any(n.startswith("the response stream carried no content before its terminator")
               for n in notices)


def test_each_wait_is_twice_the_last(repo, tmp_path, server):
    wiring, _, slept, notices = _session(repo, tmp_path, server, "openai_compat",
                                         "cut", "cut", "cut")
    assert wiring.runtime.run_turn("what is A?").text == "A is 1"
    wiring.session.close()
    assert slept == [1.0, 2.0, 4.0]
    assert [n.rsplit("(", 1)[1].split(")")[0] for n in notices if "asking again" in n] == [
        "try 2 of 6", "try 3 of 6", "try 4 of 6"]


def test_when_the_tries_run_out_the_task_is_blocked(repo, tmp_path, server):
    wiring, _, slept, notices = _session(repo, tmp_path, server, "openai_compat",
                                         *["cut"] * 5, extra="stream_max_retries = 2\n")
    outcome = wiring.runtime.run_turn("what is A?")
    wiring.session.close()
    assert len(server.bodies) == 3 and slept == [1.0, 2.0]
    assert outcome.status is TerminalStatus.BLOCKED
    assert "provider unavailable: the response stream ended without its terminator" in (
        outcome.summary or outcome.text)


def test_none_are_asked_with_zero(repo, tmp_path, server):
    wiring, _, slept, notices = _session(repo, tmp_path, server, "responses", "cut",
                                         extra="stream_max_retries = 0\n")
    outcome = wiring.runtime.run_turn("what is A?")
    wiring.session.close()
    assert len(server.bodies) == 1 and slept == []
    assert outcome.status is TerminalStatus.BLOCKED
    assert not any("asking again" in n for n in notices)


def test_a_failure_before_the_stream_began_has_only_the_request_s_retries(repo, tmp_path,
                                                                          server):
    """Re-asking it too would make request_max_retries x stream_max_retries tries."""
    wiring, _, _, notices = _session(repo, tmp_path, server, "openai_compat", 503, 503, 503)
    outcome = wiring.runtime.run_turn("what is A?")
    wiring.session.close()
    assert len(server.bodies) == 2   # request_max_retries = 2: two tries in all
    assert outcome.status is TerminalStatus.BLOCKED
    assert not any("asking again" in n for n in notices)
    assert all("cut" not in r for r in _records(wiring, EventType.MODEL_RETRY))


def test_a_task_cancelled_during_the_wait_is_not_asked_again(repo, tmp_path, server):
    wiring, _, _, _ = _session(repo, tmp_path, server, "openai_compat", "cut")
    wiring.runtime.sleep = lambda delay: wiring.runtime.cancel()
    outcome = wiring.runtime.run_turn("what is A?")
    wiring.session.close()
    assert outcome.status is TerminalStatus.CANCELLED
    assert len(server.bodies) == 1


def test_a_compaction_cut_part_way_is_asked_again_and_shows_nothing(repo, tmp_path, server):
    wiring, out, slept, notices = _session(repo, tmp_path, server, "openai_compat")
    wiring.runtime.run_turn("what is A?")
    server.script = ["cut"]
    wiring.runtime.compact(auto=False)
    wiring.session.close()
    assert slept == [1.0]
    [told] = [n for n in notices if "asking again" in n]
    assert told.endswith("(try 2 of 6)")
    # A summary is the model talking to itself: none of it reached the screen.
    assert "The answer is " not in out.getvalue()


def test_a_re_asked_session_resumes_as_if_it_had_not_been(repo, tmp_path, server):
    wiring, _, _, _ = _session(repo, tmp_path, server, "openai_compat", "cut")
    wiring.runtime.run_turn("what is A?")
    session_id = wiring.session.path.parent.name
    history = wiring.runtime.context.history
    wiring.session.close()
    again = build(repo, home=tmp_path / "home",
                      console=Console(file=io.StringIO(), width=100), resume=session_id)
    assert again.runtime.context.history == history
    again.session.close()


# --- where the difference is made ----------------------------------------------------------


def _stream(*items):
    def make():
        for item in items:
            if isinstance(item, Exception):
                raise item
            yield item
    return make


def test_only_what_comes_after_the_first_event_is_a_dropped_stream():
    before = TransientError("stream stalled: timed out")
    with pytest.raises(TransientError) as caught:
        list(open_retrying_stream(_stream(before), attempts=1, sleep=lambda d: None))
    assert type(caught.value) is TransientError
    after = TransientError("stream failed: reset", payload="detail")
    with pytest.raises(StreamDropped) as caught:
        list(open_retrying_stream(_stream({"a": 1}, after), attempts=1, sleep=lambda d: None))
    assert (str(caught.value), caught.value.payload) == ("stream failed: reset", "detail")


# --- the setting ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["-1", '"5"', "true", "2.5"])
def test_anything_but_a_count_is_refused(tmp_path, value):
    with pytest.raises(ConfigError, match="backend.stream_max_retries must be"):
        load_config(home=_home(tmp_path, f"[backend]\nstream_max_retries = {value}\n"))


def test_it_is_read_from_the_file_and_the_command_line(tmp_path):
    assert load_config(home=tmp_path / "nowhere").backend.stream_max_retries == 5
    home = _home(tmp_path, "[backend]\nstream_max_retries = 0\n")
    assert load_config(home=home).backend.stream_max_retries == 0
    assert load_config(home=home, settings=parse_settings(["stream_max_retries=9"])
                       ).backend.stream_max_retries == 9


def test_build_gives_it_to_the_runtime(repo, tmp_path, server):
    wiring, _, _, _ = _session(repo, tmp_path, server, "responses",
                               extra="stream_max_retries = 3\n")
    wiring.session.close()
    assert wiring.runtime.stream_max_retries == 3
