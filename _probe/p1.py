import sys, json, tempfile, os
from pathlib import Path
sys.path.insert(0, "src")
from foundry.core.session import SessionStore, EventType, ArtifactStore
from foundry.core.events import TerminalStatus

tmp = Path(tempfile.mkdtemp())

# --- P1: two SessionStores against the same home dir, same session id
a = SessionStore(tmp/"sessions", session_id="S1")
b = SessionStore(tmp/"sessions", session_id="S1")
a.append(EventType.TOOL_CALL, {"who":"a"})
b.append(EventType.TOOL_CALL, {"who":"b"})
a.record_termination(TerminalStatus.COMPLETED, "a done")
b.record_termination(TerminalStatus.FAILED, "b done")
a.close(); b.close()
print("P1 records:")
for r in SessionStore.read_records(a.path):
    print("  ", r.ordinal, r.type, r.payload)
print("P1 terminal:", SessionStore.terminal_status(a.path))

# --- P2: interleaved write ordering with buffering
c = SessionStore(tmp/"sessions", session_id="S2")
d = SessionStore(tmp/"sessions", session_id="S2")
c.append(EventType.NOTICE, {"n": "x"*100})   # buffered, not flushed
d.append(EventType.APPROVAL, {"n": "y"})     # flushed
c.append(EventType.APPROVAL, {"n": "z"})     # flush -> writes buffered content after d's
c.close(); d.close()
raw = (tmp/"sessions"/"S2"/"events.jsonl").read_text(encoding="utf-8")
print("P2 raw:")
print(repr(raw))
print("P2 parsed count:", len(list(SessionStore.read_records(c.path))))
