import sys, json, tempfile, subprocess, os
from pathlib import Path
sys.path.insert(0, "src")
sys.path.insert(0, "_probe")
from p2 import call, turn, git, make_repo, build   # noqa
from foundry.core.events import TerminalStatus, ApprovalChoice

# ---- B: timed-out command, claim cites correct ordinal with exit 0
tmp = Path(tempfile.mkdtemp()); repo = make_repo(tmp)
rt, ev, sess = build([
    turn("", call("run_command", {"command": "python -c \"import time; time.sleep(30)\"",
                                  "timeout_s": 2}, "c1")),
    turn("", call("finish", {"status": "completed", "summary": "s",
                             "claims": [{"claim_text": "passes", "command_event_id": 7,
                                         "expected_exit_code": 0}]}, "c2")),
], repo, tmp)
out = rt.run_turn("go")
print("B timeout, right ordinal ->", out.status, "|", out.summary.replace("\n"," / ")[:200])

# ---- C: command run in run_turn #1, finish in run_turn #2 cites it
tmp = Path(tempfile.mkdtemp()); repo = make_repo(tmp)
rt, ev, sess = build([
    turn("", call("run_command", {"command": "python -c \"print(1)\""}, "c1")),
    turn("ran it"),
    turn("", call("finish", {"status": "completed", "summary": "s",
                             "claims": [{"claim_text": "passes", "command_event_id": 7,
                                         "expected_exit_code": 0}]}, "c2")),
], repo, tmp)
o1 = rt.run_turn("run it")
print("C turn1 ->", o1.status, repr(o1.text))
print("   hist:", rt.registry.tools["run_command"].history)
o2 = rt.run_turn("now finish")
print("C turn2 (cross-turn claim) ->", o2.status, "|", o2.summary.replace("\n"," / ")[:200])
