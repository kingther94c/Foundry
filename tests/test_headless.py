"""Headless behaviour and gateway drift.

Both cases here came from running the real CLI rather than from unit tests: a
server that answers JSON to a stream request, and a trust prompt with nobody to
answer it. Each failed in a way that looked like success, which is why they get
regression tests.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from rich.console import Console

from foundry.cli.app import build
from foundry.core.backends.base import collect_turn
from foundry.core.backends.openai_compat import OpenAICompatBackend
from foundry.core.backends.responses import ResponsesBackend
from foundry.core.conversation import Message, Role, TurnRequest
from foundry.core.events import TerminalStatus
from foundry.core.policy.engine import Mode
from foundry.core.tools.git import run_git


class _JsonOnlyHandler(BaseHTTPRequestHandler):
    """A gateway that ignores stream:true and answers with a single JSON body."""

    payload: dict = {}

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps(self.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def json_server():
    httpd = HTTPServer(("127.0.0.1", 0), _JsonOnlyHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()


def url(server) -> str:
    host, port = server.server_address
    return f"http://{host}:{port}/v1"


def a_request() -> TurnRequest:
    return TurnRequest(messages=(Message.text(Role.USER, "hi"),), tools=(), model="m")


def test_chat_backend_degrades_when_the_server_does_not_stream(json_server):
    """Silently returning an empty turn would read as 'the model said nothing'."""
    _JsonOnlyHandler.payload = {
        "choices": [{"message": {"content": "not streaming"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }
    backend = OpenAICompatBackend(base_url=url(json_server), model="m", stream=True)
    turn = collect_turn(iter(backend.stream_turn(a_request())))
    assert turn.text == "not streaming"
    assert backend.stream is False, "the degradation should stick for the session"


def test_responses_backend_degrades_when_the_server_does_not_stream(json_server):
    _JsonOnlyHandler.payload = {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "plain"}]}],
        "usage": {"input_tokens": 3, "output_tokens": 1},
    }
    backend = ResponsesBackend(base_url=url(json_server), model="m", stream=True)
    turn = collect_turn(iter(backend.stream_turn(a_request())))
    assert turn.text == "plain"
    assert backend.stream is False


def test_tool_calls_survive_the_degraded_path(json_server):
    _JsonOnlyHandler.payload = {
        "choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": '{"path":"a.py"}'}}]},
            "finish_reason": "tool_calls"}],
    }
    backend = OpenAICompatBackend(base_url=url(json_server), model="m", stream=True)
    turn = collect_turn(iter(backend.stream_turn(a_request())))
    assert turn.tool_calls[0].name == "read_file"


# --- headless trust and policy -------------------------------------------


@pytest.fixture()
def repo(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "FOUNDRY.md").write_text("# notes\nRun tests with pytest.\n", encoding="utf-8")
    run_git(["init"], repo)
    run_git(["config", "user.email", "t@e.com"], repo)
    run_git(["config", "user.name", "T"], repo)
    run_git(["add", "-A"], repo)
    run_git(["commit", "-m", "init"], repo)
    return repo


def test_unattended_run_does_not_load_untrusted_project_doc(repo, tmp_path):
    """Reading EOF from a closed stdin must not be treated as consent."""
    wiring = build(repo, home=tmp_path / "home", interactive=False,
                   console=Console(file=open(tmp_path / "o.txt", "w", encoding="utf-8")))
    assert wiring.runtime.context.project_doc == ""
    wiring.session.close()


def test_dont_ask_mode_denies_unapproved_work(repo, tmp_path):
    wiring = build(repo, home=tmp_path / "home", interactive=False,
                   overrides={"mode": Mode.DONT_ASK},
                   console=Console(file=open(tmp_path / "o.txt", "w", encoding="utf-8")))
    from foundry.core.policy.engine import Verdict
    from foundry.core.tools.base import Operation, ToolKind

    op = Operation(tool="run_command", kind=ToolKind.MUTATOR,
                   args={"command": "python -c \"print(1)\""},
                   display="python -c \"print(1)\"", target="python -c \"print(1)\"")
    decision, _ = wiring.runtime.policy.evaluate(op)
    assert decision.verdict is Verdict.DENY
    wiring.session.close()


def test_preauthorized_rule_allows_unattended_work(repo, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        '[[permissions]]\ntool = "run_command"\npattern = "python -m pytest*"\n'
        'decision = "allow"\n', encoding="utf-8")

    wiring = build(repo, home=home, interactive=False,
                   overrides={"mode": Mode.DONT_ASK},
                   console=Console(file=open(tmp_path / "o.txt", "w", encoding="utf-8")))
    from foundry.core.policy.engine import Verdict
    from foundry.core.tools.base import Operation, ToolKind

    op = Operation(tool="run_command", kind=ToolKind.MUTATOR,
                   args={"command": "python -m pytest tests -q"},
                   display="python -m pytest tests -q", target="python -m pytest tests -q")
    decision, _ = wiring.runtime.policy.evaluate(op)
    assert decision.verdict is Verdict.ALLOW
    wiring.session.close()


def test_event_id_is_visible_to_the_model(repo, tmp_path):
    """A claim can only cite an event the model was actually shown."""
    from foundry.core.session import SessionStore
    from foundry.core.tools.base import ReadTracker, ToolContext
    from foundry.core.tools.command import RunCommand
    from foundry.core.workspace import Workspace

    session = SessionStore(tmp_path / "sessions")
    tool = RunCommand(recorder=session)
    ctx = ToolContext(workspace=Workspace(repo), artifacts=session.artifacts,
                      read_tracker=ReadTracker())
    output = tool.execute(tool.validate({"command": 'python -c "print(1)"'}), ctx)
    session.close()
    assert output.metadata["event_ordinal"] > 0
