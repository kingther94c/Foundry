"""Journal, artifact store, and redaction behaviour.

Several tests here are negative assertions: they check that something never
appears, which is the only way "credentials do not leak" becomes checkable.
"""

from __future__ import annotations

import base64
import json

import pytest

from foundry.core.events import TerminalStatus
from foundry.core.redaction import PLACEHOLDER, Redactor
from foundry.core.session import AuditLog, EventType, SessionStore


@pytest.fixture()
def redactor() -> Redactor:
    return Redactor()


def test_journal_roundtrip(tmp_path):
    with SessionStore(tmp_path) as store:
        store.write_header(workspace="C:/repo", profile="personal", model="test-model",
                           foundry_version="0.1.0")
        store.append(EventType.TOOL_CALL, {"tool": "read_file", "path": "a.py"})
        store.record_termination(TerminalStatus.COMPLETED, "finished")
        path = store.path

    records = list(SessionStore.read_records(path))
    assert [r.type for r in records] == [
        EventType.SESSION_META, EventType.TOOL_CALL, EventType.TERMINATION
    ]
    assert [r.ordinal for r in records] == [1, 2, 3]
    assert SessionStore.terminal_status(path) is TerminalStatus.COMPLETED


def test_missing_termination_reads_as_interrupted(tmp_path):
    with SessionStore(tmp_path) as store:
        store.write_header(workspace="C:/repo", profile="personal", model="m",
                           foundry_version="0.1.0")
        store.append(EventType.TOOL_CALL, {"tool": "read_file"})
        path = store.path

    assert SessionStore.terminal_status(path) is TerminalStatus.INTERRUPTED


def test_truncated_tail_is_tolerated_and_not_completed(tmp_path):
    with SessionStore(tmp_path) as store:
        store.write_header(workspace="C:/repo", profile="personal", model="m",
                           foundry_version="0.1.0")
        store.append(EventType.TOOL_CALL, {"tool": "read_file"})
        path = store.path

    # Simulate a process killed mid-write.
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"ts": "2026-01-01T00:00:00Z", "ordinal": 3, "ty')

    records = list(SessionStore.read_records(path))
    assert len(records) == 2
    assert SessionStore.terminal_status(path) is TerminalStatus.INTERRUPTED


def test_registered_credential_never_reaches_journal(tmp_path, redactor):
    secret = "sk-canary-abcdefghijklmnop"
    redactor.register(secret)
    with SessionStore(tmp_path, redactor=redactor) as store:
        store.append(EventType.TOOL_RESULT, {"content": f"token is {secret} here"})
        store.record_command(argv=["cmd"], cwd="C:/repo", exit_code=0, duration_ms=1,
                             stdout=secret.encode("utf-8"), stderr=b"")
        path = store.path

    raw = path.read_text(encoding="utf-8")
    assert secret not in raw
    assert PLACEHOLDER in raw

    for record in SessionStore.read_records(path):
        if record.type == EventType.COMMAND_EXEC:
            decoded = base64.b64decode(record.payload["stdout_b64"])
            assert secret.encode("utf-8") not in decoded


def test_credential_scrubbed_from_utf16_command_output(redactor):
    secret = "sk-canary-abcdefghijklmnop"
    redactor.register(secret)
    wide = secret.encode("utf-16-le")
    assert secret.encode("utf-16-le") not in redactor.scrub_bytes(wide)


def test_short_values_are_not_registered(redactor):
    redactor.register("abc")
    assert redactor.registered_count == 0
    assert redactor.scrub("abc def") == "abc def"


def test_artifact_ids_are_opaque_and_scoped(tmp_path):
    with SessionStore(tmp_path) as store:
        ref = store.artifacts.put_text("hello world")
        assert store.artifacts.read_text(ref.artifact_id) == "hello world"
        with pytest.raises(KeyError):
            store.artifacts.read_text("../../auth.json")
        with pytest.raises(KeyError):
            store.artifacts.read_text("0" * 64)


def test_artifact_content_is_redacted_before_storage(tmp_path, redactor):
    secret = "ghp_canaryTOKENvalue0123456789"
    redactor.register(secret)
    with SessionStore(tmp_path, redactor=redactor) as store:
        ref = store.artifacts.put_text(f"output {secret}")
        stored = (store.dir / "artifacts" / ref.artifact_id).read_bytes()
        assert secret.encode("utf-8") not in stored


def test_audit_log_appends_decisions(tmp_path, redactor):
    log = AuditLog(tmp_path / "audit.jsonl", redactor)
    log.record(workspace="C:/repo", tool="run_command", target="pytest -q",
               decision="ASK->once", outcome="exit=0")
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["tool"] == "run_command"
