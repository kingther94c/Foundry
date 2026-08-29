"""Journal completeness and credential refresh.

D-012 promises a journal complete enough to rebuild each request; that is what
replay, resume, and any audit of what was sent depend on. The Authorization
header is the deliberate exception.
"""

from __future__ import annotations

import json

import pytest

from foundry.core.auth import SecretHandle
from foundry.core.backends.replay import ScriptedBackend
from foundry.core.context import ContextManager
from foundry.core.conversation import ModelTurn, StopReason, ToolUseBlock, Usage
from foundry.core.errors import AuthError
from foundry.core.events import EventSink, TerminalStatus
from foundry.core.policy.engine import PolicyEngine
from foundry.core.runtime import AgentRuntime
from foundry.core.session import ArtifactStore, EventType, SessionStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.registry import default_registry
from foundry.core.workspace import Workspace


def call(name: str, args: dict, call_id: str = "c1") -> ToolUseBlock:
    return ToolUseBlock(call_id=call_id, name=name, arguments=json.dumps(args))


def turn(text: str = "", *calls: ToolUseBlock) -> ModelTurn:
    return ModelTurn(text=text, tool_calls=tuple(calls),
                     usage=Usage(input_tokens=5, output_tokens=2),
                     stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN)


@pytest.fixture()
def wiring(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    session = SessionStore(tmp_path / "sessions")

    def build(backend):
        return AgentRuntime(
            backend=backend, registry=default_registry(recorder=session),
            policy=PolicyEngine(),
            context=ContextManager(system_prompt="SYSTEM-MARKER"),
            tool_ctx=ToolContext(workspace=Workspace(repo), artifacts=session.artifacts,
                                 read_tracker=ReadTracker()),
            session=session, events=EventSink(), model="fake",
        ), session

    return build


def test_journal_records_the_messages_that_were_sent(wiring):
    runtime, session = wiring(ScriptedBackend([
        turn("", call("read_file", {"path": "src/app.py"})),
        turn("VALUE is 1"),
    ]))
    runtime.run_turn("what is VALUE?")
    session.close()

    requests = [r for r in SessionStore.read_records(session.path)
                if r.type == EventType.MODEL_REQUEST]
    assert requests

    first = requests[0].payload
    assert "messages" in first, "the request body must be reconstructable"
    rendered = json.dumps(first)
    assert "SYSTEM-MARKER" in rendered, "the system prompt is part of the request"
    assert "what is VALUE?" in rendered

    # The second request must show the tool result the model actually saw.
    second = json.dumps(requests[1].payload)
    assert "VALUE = 1" in second


def test_journal_omits_authorization(wiring):
    runtime, session = wiring(ScriptedBackend([turn("done")]))
    runtime.run_turn("hello")
    session.close()
    text = session.path.read_text(encoding="utf-8")
    assert "Authorization" not in text
    assert "Bearer" not in text


# --- credential refresh ---------------------------------------------------


class _ExpiringBackend:
    """Rejects the first sample, then succeeds once the key changes."""

    name = "expiring"

    def __init__(self):
        self.api_key = "expired-token"
        self.attempts = 0

    def capabilities(self):
        from foundry.core.conversation import Capabilities

        return Capabilities()

    def stream_turn(self, request):
        self.attempts += 1
        if self.api_key == "expired-token":
            raise AuthError("token expired (HTTP 401)")
        from foundry.core.backends.base import TurnFinished

        return iter([TurnFinished(turn("recovered"))])


class _Source:
    def __init__(self, value="fresh-token"):
        self.value = value
        self.invalidated = 0
        self.acquired = 0

    def acquire(self):
        self.acquired += 1
        return SecretHandle(self.value)

    def invalidate(self):
        self.invalidated += 1

    def logout(self):
        pass


def test_expired_credential_is_refreshed_once_and_the_turn_continues(wiring):
    backend = _ExpiringBackend()
    runtime, session = wiring(backend)
    source = _Source()
    runtime.credentials = source

    outcome = runtime.run_turn("do the work")
    session.close()

    assert source.acquired == 1
    assert backend.attempts == 2
    assert outcome.status is None, "the session should continue, not terminate"
    assert outcome.text == "recovered"


def test_refresh_never_destroys_the_stored_credential(wiring):
    """invalidate() on the gateway source clears the vault, and nothing can
    re-acquire yet: one expired token would log the user out permanently."""
    backend = _ExpiringBackend()
    runtime, session = wiring(backend)
    source = _Source()
    runtime.credentials = source

    runtime.run_turn("do the work")
    session.close()
    assert source.invalidated == 0


def test_an_unchanged_credential_does_not_cost_a_second_request(wiring):
    backend = _ExpiringBackend()
    runtime, session = wiring(backend)
    runtime.credentials = _Source(value="expired-token")  # re-acquire yields the same

    outcome = runtime.run_turn("do the work")
    session.close()

    assert backend.attempts == 1, "retrying an identical credential is a wasted request"
    assert outcome.status is TerminalStatus.BLOCKED
    assert "authentication" in outcome.summary


def test_no_credential_source_still_blocks_cleanly(wiring):
    runtime, session = wiring(_ExpiringBackend())
    outcome = runtime.run_turn("do the work")
    session.close()
    assert outcome.status is TerminalStatus.BLOCKED


def test_secret_handle_never_prints_its_value():
    handle = SecretHandle("sk-do-not-show-this", label="api key")
    assert "sk-do-not-show" not in str(handle)
    assert "sk-do-not-show" not in repr(handle)
    assert "sk-do-not-show" not in f"{handle}"
    assert handle.reveal() == "sk-do-not-show-this"
