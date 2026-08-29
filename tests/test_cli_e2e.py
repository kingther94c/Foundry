"""End-to-end: the CLI wiring driven by a fake model server.

This is the M0 acceptance gate in test form -- a complete session with tools,
policy, journal, and terminal status, on a machine with no network and no
credentials.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from rich.console import Console

from foundry.cli.app import build
from foundry.core.events import ApprovalChoice, TerminalStatus
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.git import run_git


class _ModelHandler(BaseHTTPRequestHandler):
    turns: list = []
    index = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.requests.append(json.loads(self.rfile.read(length) or b"{}"))
        turn = type(self).turns[min(type(self).index, len(type(self).turns) - 1)]
        type(self).index += 1
        payload = json.dumps({"model": "fake", "choices": [turn],
                              "usage": {"prompt_tokens": 10, "completion_tokens": 4}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def text_turn(text: str) -> dict:
    return {"message": {"content": text}, "finish_reason": "stop"}


def tool_turn(name: str, args: dict, call_id: str = "c1") -> dict:
    return {
        "message": {"content": None, "tool_calls": [
            {"id": call_id, "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}},
        ]},
        "finish_reason": "tool_calls",
    }


@pytest.fixture()
def model_server():
    httpd = HTTPServer(("127.0.0.1", 0), _ModelHandler)
    httpd.requests = []
    _ModelHandler.index = 0
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()


@pytest.fixture()
def repo(tmp_path):
    repo = tmp_path / "project"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    run_git(["init"], repo)
    run_git(["config", "user.email", "t@example.com"], repo)
    run_git(["config", "user.name", "T"], repo)
    run_git(["add", "."], repo)
    run_git(["commit", "-m", "initial"], repo)
    return repo


def wire(repo, tmp_path, model_server, *, approve=ApprovalChoice.ONCE):
    host, port = model_server.server_address
    wiring = build(
        repo, home=tmp_path / "home",
        overrides={"base_url": f"http://{host}:{port}/v1", "model": "fake", "stream": False},
        console=Console(file=open(tmp_path / "out.txt", "w", encoding="utf-8"), width=100),
    )
    wiring.runtime.approval = lambda request: approve
    return wiring


def test_readonly_question_completes(repo, tmp_path, model_server):
    _ModelHandler.turns = [
        tool_turn("read_file", {"path": "src/calc.py"}),
        tool_turn("finish", {"status": "completed",
                             "summary": "add() subtracts instead of adding",
                             "claims": []}, "c2"),
    ]
    wiring = wire(repo, tmp_path, model_server)
    outcome = wiring.runtime.run_turn("what does add() do?")
    wiring.session.close()

    assert outcome.status is TerminalStatus.COMPLETED
    assert SessionStore.terminal_status(wiring.session.path) is TerminalStatus.COMPLETED


def test_edit_and_verify_flow(repo, tmp_path, model_server):
    patch = (
        "*** Begin Patch\n*** Update File: src/calc.py\n"
        "<<<<<<< SEARCH\n    return a - b\n=======\n    return a + b\n>>>>>>> REPLACE\n"
        "*** End Patch"
    )
    _ModelHandler.turns = [
        tool_turn("read_file", {"path": "src/calc.py"}, "c1"),
        tool_turn("apply_patch", {"patch": patch}, "c2"),
        tool_turn("run_command", {"command": 'python -c "import sys; sys.path.insert(0, \'src\'); from calc import add; sys.exit(0 if add(2,2)==4 else 1)"'}, "c3"),
        tool_turn("finish", {"status": "completed", "summary": "fixed add()",
                             "claims": [{"claim_text": "add(2,2)==4",
                                         "command_event_id": 0, "expected_exit_code": 0}]}, "c4"),
    ]
    wiring = wire(repo, tmp_path, model_server)
    outcome = wiring.runtime.run_turn("fix add()")
    wiring.session.close()

    assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
    # The claim cites event 0, which is not a real command event, so completed
    # must be refused even though the work itself succeeded.
    assert outcome.status is TerminalStatus.PARTIAL

    types = [r.type for r in SessionStore.read_records(wiring.session.path)]
    for expected in (EventType.SESSION_META, EventType.GIT_BASELINE, EventType.MODEL_REQUEST,
                     EventType.TOOL_CALL, EventType.POLICY_DECISION, EventType.COMMAND_EXEC,
                     EventType.TERMINATION):
        assert expected in types


def test_declined_patch_leaves_the_file_alone(repo, tmp_path, model_server):
    patch = (
        "*** Begin Patch\n*** Update File: src/calc.py\n"
        "<<<<<<< SEARCH\n    return a - b\n=======\n    return 0\n>>>>>>> REPLACE\n"
        "*** End Patch"
    )
    _ModelHandler.turns = [
        tool_turn("read_file", {"path": "src/calc.py"}, "c1"),
        tool_turn("apply_patch", {"patch": patch}, "c2"),
        tool_turn("finish", {"status": "blocked", "summary": "the user declined the edit",
                             "claims": []}, "c3"),
    ]
    wiring = wire(repo, tmp_path, model_server, approve=ApprovalChoice.DENY)
    outcome = wiring.runtime.run_turn("break it")
    wiring.session.close()

    assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"
    assert outcome.status is TerminalStatus.BLOCKED


def test_dirty_file_is_reported_in_baseline(repo, tmp_path, model_server):
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a - b  # wip\n",
                                          encoding="utf-8")
    _ModelHandler.turns = [text_turn("noted")]
    wiring = wire(repo, tmp_path, model_server)
    assert "src/calc.py" in wiring.runtime.policy.dirty_files
    wiring.session.close()

    baseline = [r for r in SessionStore.read_records(wiring.session.path)
                if r.type == EventType.GIT_BASELINE][0]
    assert "src/calc.py" in baseline.payload["dirty_paths"]


def test_non_git_directory_is_refused(tmp_path, model_server):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(Exception, match="Git repository"):
        build(plain, home=tmp_path / "home",
              console=Console(file=open(tmp_path / "o.txt", "w", encoding="utf-8")))


def test_system_prompt_states_the_real_permissions(repo, tmp_path, model_server):
    _ModelHandler.turns = [text_turn("ok")]
    wiring = wire(repo, tmp_path, model_server)
    wiring.runtime.run_turn("hello")
    wiring.session.close()

    system = model_server.requests[0]["messages"][0]["content"]
    assert "no `&&`" in system or "no &&" in system
    assert "What is allowed right now" in system
    assert "never refused" not in system.lower()


def test_credentials_never_appear_in_the_journal(repo, tmp_path, model_server, monkeypatch):
    canary = "sk-canary-e2e-0123456789abcdef"
    monkeypatch.setenv("OPENAI_API_KEY", canary)
    _ModelHandler.turns = [text_turn("ok")]

    wiring = wire(repo, tmp_path, model_server)
    wiring.runtime.run_turn("hello")
    wiring.session.close()

    journal = wiring.session.path.read_text(encoding="utf-8")
    assert canary not in journal
    audit = (tmp_path / "home" / "audit.jsonl")
    if audit.is_file():
        assert canary not in audit.read_text(encoding="utf-8")
