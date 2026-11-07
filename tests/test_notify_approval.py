"""The user's ``notify`` program is told when an approval waits (D-079), as
codex's notifications say "approval requested".

What carries the feature: a session left running stops at a question nobody
sees; now the program is told, before the question blocks, what is waiting
-- scrubbed and cut like everything it is told -- and only when someone can
actually be asked: an unattended run, or a call a rule allows, tells nobody.
"""

from __future__ import annotations

import json

from foundry.core.events import ApprovalChoice
from foundry.core.notify import APPROVAL_TYPE, TEXT_LIMIT, approval_payload
from foundry.core.policy.engine import Mode, PolicyEngine
from foundry.core.redaction import Redactor

from test_notify import Recording, _notified, _runtime
from test_resume import call, turn

COMMAND = 'python -c "print(1)"'


def _asking(tmp_path, turns, answer=ApprovalChoice.DENY):
    notifier = Recording()
    runtime, session, repo_, notices = _runtime(tmp_path, turns, notifier)
    runtime.policy = PolicyEngine(mode=Mode.DEFAULT)
    asked = []

    def approve(request):
        # What the program was told by the time the question is asked.
        asked.append(list(notifier.sent))
        return answer

    runtime.approval = approve
    return runtime, session, notifier, asked


def test_the_program_is_told_before_the_question_waits(tmp_path):
    runtime, session, notifier, asked = _asking(tmp_path, [
        turn(call("c1", "run_command", {"command": COMMAND})), turn(text="ok")])
    runtime.run_turn("run it")
    told = asked[0]
    assert [n["type"] for n in told] == [APPROVAL_TYPE]
    assert told[0]["tool"] == "run_command" and told[0]["display"] == COMMAND
    assert told[0]["reason"] and told[0]["session-id"] == session.session_id
    # Then the turn's end, as before.
    assert [n["type"] for n in notifier.sent] == [APPROVAL_TYPE, "agent-turn-complete"]


def test_each_start_is_journaled_with_its_type(tmp_path):
    runtime, session, _, _ = _asking(tmp_path, [
        turn(call("c1", "run_command", {"command": COMMAND})), turn(text="ok")])
    runtime.run_turn("run it")
    assert _notified(session) == [
        {"type": APPROVAL_TYPE, "program": "notify-send", "pid": 4242},
        {"type": "agent-turn-complete", "program": "notify-send", "pid": 4242}]


def test_unattended_nobody_is_told(tmp_path):
    """Nobody can be asked, so the call is refused without waiting."""
    runtime, _, notifier, _ = _asking(tmp_path, [
        turn(call("c1", "run_command", {"command": COMMAND})), turn(text="ok")])
    runtime.approval = None
    runtime.run_turn("run it")
    assert [n["type"] for n in notifier.sent] == ["agent-turn-complete"]


def test_a_call_a_rule_allows_tells_nobody(tmp_path):
    notifier = Recording()
    runtime, _, _, _ = _runtime(tmp_path, [
        turn(call("c1", "run_command", {"command": COMMAND})), turn(text="ok")], notifier)
    runtime.approval = lambda request: ApprovalChoice.DENY       # never reached
    runtime.run_turn("run it")
    assert [n["type"] for n in notifier.sent] == ["agent-turn-complete"]


def test_what_is_told_is_scrubbed(tmp_path):
    secret = "s3cr3t-t0ken-value"
    runtime, _, notifier, _ = _asking(tmp_path, [
        turn(call("c1", "run_command", {"command": f"echo {secret}"})), turn(text="ok")])
    redactor = Redactor()
    redactor.register(secret)
    runtime.scrub = redactor.scrub
    runtime.run_turn("go")
    assert secret not in json.dumps(notifier.sent)
    assert notifier.sent[0]["display"] == "echo [redacted]"


def test_and_cut_to_fit_a_command_line():
    data = json.loads(approval_payload(session_id="s", cwd="c", tool="run_command",
                                       display="x" * (TEXT_LIMIT * 2),
                                       reason="y" * (TEXT_LIMIT * 2)))
    assert len(data["display"]) == TEXT_LIMIT and len(data["reason"]) == TEXT_LIMIT
    assert data["type"] == APPROVAL_TYPE


def test_a_program_that_cannot_start_does_not_stop_the_question(tmp_path):
    runtime, session, notifier, asked = _asking(tmp_path, [
        turn(call("c1", "run_command", {"command": COMMAND})), turn(text="ok")])
    notifier.fail = FileNotFoundError("'notify-send' was not found on PATH")
    runtime.run_turn("run it")
    assert len(asked) == 1                                        # still asked
    assert _notified(session)[0] == {"type": APPROVAL_TYPE, "program": "notify-send",
                                     "error": "'notify-send' was not found on PATH"}
