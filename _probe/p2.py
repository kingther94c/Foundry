import sys, json, tempfile, subprocess, os
from pathlib import Path
sys.path.insert(0, "src")
from foundry.core.backends.replay import ScriptedBackend
from foundry.core.context import ContextManager
from foundry.core.conversation import ModelTurn, StopReason, ToolUseBlock, Usage
from foundry.core.events import ApprovalChoice, EventSink, TerminalStatus
from foundry.core.policy.engine import PolicyEngine
from foundry.core.runtime import AgentRuntime, Budget
from foundry.core.session import SessionStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.registry import default_registry
from foundry.core.tools.git import capture_baseline
from foundry.core.workspace import Workspace


def call(name, args, cid="c1"):
    return ToolUseBlock(call_id=cid, name=name, arguments=json.dumps(args))

def turn(text="", *calls):
    return ModelTurn(text=text, tool_calls=tuple(calls),
                     usage=Usage(input_tokens=10, output_tokens=5),
                     stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN)

def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)

def make_repo(tmp, commit=True):
    repo = tmp / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t.t")
    git(repo, "config", "user.name", "t")
    if commit:
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "init")
    return repo

def build(turns, repo, tmp, *, baseline=None, approval=lambda r: ApprovalChoice.ONCE):
    session = SessionStore(tmp / "sessions")
    registry = default_registry(recorder=session)
    ctx = ToolContext(workspace=Workspace(repo), artifacts=session.artifacts,
                      read_tracker=ReadTracker())
    events = []
    sink = EventSink(); sink.subscribe(events.append)
    rt = AgentRuntime(backend=ScriptedBackend(turns), registry=registry,
                      policy=PolicyEngine(), context=ContextManager(system_prompt="t"),
                      tool_ctx=ctx, session=session, events=sink,
                      approval=approval, budget=Budget(), git_baseline=baseline)
    return rt, events, session


# ---- A: claim citing a command that timed out
tmp = Path(tempfile.mkdtemp())
repo = make_repo(tmp)
rt, ev, sess = build([
    turn("", call("run_command", {"command": "python -c \"import time; time.sleep(30)\"",
                                  "timeout_s": 2}, "c1")),
    turn("", call("finish", {"status": "completed", "summary": "tests pass",
                             "claims": [{"claim_text": "pytest passes",
                                         "command_event_id": 2,
                                         "expected_exit_code": 0}]}, "c2")),
], repo, tmp)
out = rt.run_turn("go")
print("A timed-out claim ->", out.status, "|", out.summary.replace("\n", " / ")[:200])
print("   history:", rt.registry.tools["run_command"].history)
