#!/usr/bin/env python
"""Mini Foundry -- the whole thing, in one file, small enough to read.

The real Foundry is 7000 lines. This is 600 (half of them comments), with
**exactly the same structure** -- every layer shaved down to its thinnest
form. Read this file and you know what each module under src/foundry/ does.

    The entire system is one sentence:

        while the model keeps asking for tools:
            policy decides -> execute -> feed the result back -> ask again

    Every other line of code exists to put a guard around one word in
    that sentence.

Run it (no network, no API key needed):

    python demo/mini_foundry.py

Watch policy stop something:

    python demo/mini_foundry.py --script destructive

Six parts, read bottom-up:
    1. IR       -- the shape of a conversation (see core/conversation.py)
    2. Tools    -- what the model can do       (see core/tools/)
    3. Policy   -- what's allowed, what to ask (see core/policy/)
    4. Session  -- the ledger of what happened (see core/session.py)
    5. Backend  -- talking to the model        (see core/backends/)
    6. Loop     -- stitching the five together (see core/runtime.py)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# The Windows console is not UTF-8 by default, so printing one box character
# raises UnicodeEncodeError. The real Foundry deals with this class of thing
# everywhere: subprocess output is tried as UTF-8 and then as the OEM code
# page, patches preserve a file's original CRLF and BOM. Platform detail is
# not busywork for a tool like this -- it is the job.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


# ══════════════════════════════════════════════════════════════════════════
# 1. IR: what a conversation looks like
#
# The key design point: these types **belong to no model vendor**. OpenAI's
# JSON and Anthropic's JSON are both translated into them down in the backend
# layer. Swapping models therefore touches no line of loop, policy or tools.
#
# Real Foundry: core/conversation.py. It adds Usage accounting, Capabilities
# negotiation, and ToolUseBlock.arguments is deliberately kept as the **raw
# string** -- a model can emit broken JSON, and parsing it early would make
# "report that this call was malformed" impossible.
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class ToolCall:
    """The model says: call this tool for me."""
    id: str
    name: str
    arguments: str          # raw JSON string, not parsed early (see above)


@dataclass
class Message:
    """One entry in the conversation. role is user / assistant / tool."""
    role: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""   # when role == "tool", which call this answers


@dataclass
class ModelTurn:
    """One model reply: something said + tools it wants called."""
    text: str
    tool_calls: list[ToolCall]


# ══════════════════════════════════════════════════════════════════════════
# 2. Tools: what the model can do
#
# Every tool does two things: validate (is this call well-formed?) and run.
#
# **validate must run before policy.** Otherwise a malformed call pops an
# approval prompt at the user first, and only after they approve does anyone
# discover the arguments were wrong all along.
#
# Real Foundry: core/tools/. Nine tools. read_file remembers a digest of what
# it read so "read before you edit" can be enforced, apply_patch uses anchored
# search/replace and is atomic per file, run_command uses a Windows Job Object
# so the whole child process tree can be killed.
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class Operation:
    """A call that has **already been validated**.

    Policy judges it, the approval prompt displays it, the executor runs it --
    all three hold the same object. This is not fastidiousness: if what is
    shown and what is run could differ, the user did not approve what actually
    happened.
    """
    tool: str
    args: dict
    display: str        # one line, for a human
    target: str         # the key policy matches rules against (a path, or the
                        # command as written)


class Tools:
    """Four tools -- enough to show the shape."""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    # ---- Boundary check: the foundation under every file tool ----
    def _resolve(self, relative: str) -> Path:
        path = (self.workspace / relative).resolve()
        # The real Foundry also handles: 8.3 short names, reparse points, case,
        # device names (CON/NUL), drive-relative paths (C:foo), UNC paths.
        # See core/workspace.py.
        if not str(path).startswith(str(self.workspace.resolve())):
            raise ValueError(f"path escapes the workspace: {relative}")
        return path

    def validate(self, call: ToolCall) -> Operation:
        try:
            args = json.loads(call.arguments)
        except json.JSONDecodeError as exc:
            raise ValueError(f"arguments are not valid JSON: {exc}") from exc

        if call.name == "read_file":
            path = args["path"]
            self._resolve(path)                       # escapes are refused here
            return Operation("read_file", args, f"read {path}", path)

        if call.name == "write_file":
            path = args["path"]
            self._resolve(path)
            return Operation("write_file", args, f"write {path}", path)

        if call.name == "run_command":
            command = args["command"]
            return Operation("run_command", args, f"run {command}", command)

        if call.name == "finish":
            return Operation("finish", args, "finish", "")

        raise ValueError(f"no such tool: {call.name}")

    def run(self, op: Operation, session: "Session") -> str:
        if op.tool == "read_file":
            return self._resolve(op.args["path"]).read_text(encoding="utf-8")

        if op.tool == "write_file":
            path = self._resolve(op.args["path"])
            path.write_text(op.args["content"], encoding="utf-8")
            return f"wrote {op.args['path']} ({len(op.args['content'])} chars)"

        if op.tool == "run_command":
            # PYTHONDONTWRITEBYTECODE: when tests are re-run right after a file
            # is edited, if the new and old file have the same size and their
            # mtimes land in the same clock tick, Python decides the .pyc in
            # __pycache__ is still valid and **runs the pre-edit code** -- the
            # patch was right, the tests are still red. Environment noise like
            # this traps an agent in an edit-and-edit-again loop.
            #
            # The real Foundry goes further: the subprocess environment is an
            # **allowlist**, holding only what the build genuinely needs.
            # Approving pytest once should not hand every API key in your shell
            # to the repository's test code.
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            done = subprocess.run(op.args["command"], shell=True, cwd=self.workspace,
                                  capture_output=True, text=True, timeout=60, env=env)
            output = (done.stdout + done.stderr).strip()
            # **Where the evidence chain starts**: the execution is written to
            # the ledger and gets an event number. Later, when finish claims
            # "the tests pass", we come back and look that number up -- was
            # that entry really exit 0?
            event_id = session.record("command_exec", {
                "command": op.args["command"],
                "exit_code": done.returncode,
            })
            return f"exit code {done.returncode}\n{output[:2000]}\n[event_id={event_id}]"

        if op.tool == "finish":
            return ""       # handled specially by the loop, see section 6

        raise ValueError(op.tool)

    @staticmethod
    def schemas() -> list[dict]:
        """Tell the model which tools exist. The real Foundry's schemas also
        carry examples and counter-examples."""
        return [
            {"name": "read_file", "description": "read a file",
             "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                            "required": ["path"]}},
            {"name": "write_file", "description": "overwrite a file",
             "parameters": {"type": "object",
                            "properties": {"path": {"type": "string"},
                                           "content": {"type": "string"}},
                            "required": ["path", "content"]}},
            {"name": "run_command", "description": "run a command in the workspace",
             "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                            "required": ["command"]}},
            {"name": "finish", "description": "the task is done, report the result",
             "parameters": {"type": "object",
                            "properties": {"summary": {"type": "string"},
                                           "claim_event_id": {"type": "integer"}},
                            "required": ["summary"]}},
        ]


# ══════════════════════════════════════════════════════════════════════════
# 3. Policy: what's allowed, what needs asking, what is never permitted
#
# The real Foundry is a **six-step pipeline**. This keeps its skeleton and the
# property that matters most:
#
#     step 0  breaker table  -- no rule, mode, grant or hook can override it
#     step 1  DENY rules
#     step 2  ASK rules
#     step 3  mode baseline (read-only / auto-edit / ask-all / deny-all)
#     step 4  ALLOW rules
#     step 5  ask the human
#
# **The order is the entire meaning**: deny beats allow, the breaker beats
# everything. The breaker is step 0 rather than "a rule with very high
# priority" because the rule table is configurable and these entries must not
# be.
#
# Real Foundry: core/policy/. The breaker table also has to survive "two
# readings of the same command" -- `git reset --hard` can be written
# `git reset ,--hard`, hidden inside a `<# #>` comment, or wrapped in the `&`
# call operator. Every one of five adversarial review rounds broke the
# segmenter, so the breaker table now **also scans a deliberately stupid
# reading**: one that understands neither quotes nor comments, and so cannot
# be fooled by them.
# ══════════════════════════════════════════════════════════════════════════

ALLOW, ASK, DENY = "allow", "ask", "deny"

# Step 0. This table takes no configuration, no grant, no hook rewrite.
FORBIDDEN = [
    ("git push", "we never publish"),
    ("git commit", "we never commit on your behalf"),
    ("git reset --hard", "it destroys uncommitted work"),
    ("rm -rf /", "a destructive delete"),
]


@dataclass
class Decision:
    verdict: str
    reason: str
    step: int


class Policy:
    def __init__(self, mode: str = "default", allow_rules: list[str] | None = None):
        self.mode = mode                       # default | accept_edits | plan
        self.allow_rules = allow_rules or []
        self.session_grants: set[str] = set()  # approved earlier this session

    def evaluate(self, op: Operation) -> Decision:
        # ---- Step 0: the breaker table. Before anything else. ----
        for pattern, why in FORBIDDEN:
            if pattern in op.target:
                return Decision(DENY, f"'{pattern}': {why}; cannot be approved", 0)

        # ---- Step 3: mode baseline (read-only tools pass straight through) ----
        if op.tool in ("read_file", "finish"):
            return Decision(ALLOW, "read-only", 3)

        if self.mode == "plan":
            return Decision(DENY, "plan mode makes no changes", 3)

        # ---- Step 4: ALLOW rules + session grants ----
        if op.target in self.session_grants:
            return Decision(ALLOW, "already approved this session", 4)
        for rule in self.allow_rules:
            if op.target.startswith(rule):
                return Decision(ALLOW, f"matches rule {rule!r}", 4)

        if self.mode == "accept_edits" and op.tool == "write_file":
            return Decision(ALLOW, "accept_edits mode", 3)

        # ---- Step 5: ask the human ----
        return Decision(ASK, "no rule matched, needs confirmation", 5)


def ask_human(op: Operation, decision: Decision, auto: str | None) -> bool:
    """In the real Foundry this is one event and one answer, so headless mode
    can auto-resolve every ASK to DENY (fail-closed) instead of having input()
    hard-coded inside a tool."""
    print(f"\n  <approval> {op.display}")
    print(f"             reason: {decision.reason}")
    if auto is not None:
        print(f"             auto-answer: {auto}")
        return auto == "y"
    return input("             allow? [y/N] ").strip().lower() == "y"


# ══════════════════════════════════════════════════════════════════════════
# 4. Session: the ledger of what happened
#
# Append-only, one JSON object per line. Two uses:
#   (a) when something goes wrong you can look: who approved what, which
#       commands ran, how they came out;
#   (b) **the evidence chain** -- when finish claims "the tests pass", come
#       back and check that command's exit code.
#
# Real Foundry: core/session.py. It adds content-addressed artifact storage
# (large output is not stuffed into the conversation; it is stored as a file
# the model pages through on demand), credential redaction, and a degraded
# path so that failing to write the ledger cannot take the whole turn down.
# ══════════════════════════════════════════════════════════════════════════

class Session:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("w", encoding="utf-8")
        self.ordinal = 0
        self.events: list[dict] = []

    def record(self, event_type: str, payload: dict) -> int:
        self.ordinal += 1
        entry = {"n": self.ordinal, "type": event_type, "payload": payload}
        self.events.append(entry)
        self.file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.file.flush()
        return self.ordinal

    def find_command(self, event_id: int) -> dict | None:
        for entry in self.events:
            if entry["n"] == event_id and entry["type"] == "command_exec":
                return entry["payload"]
        return None

    def close(self) -> None:
        self.file.close()


# ══════════════════════════════════════════════════════════════════════════
# 5. Backend: talking to the model
#
# It only **translates**: IR in, vendor JSON out; vendor JSON in, IR out.
# It must never run a loop of its own -- otherwise two places are deciding
# what happens next.
#
# Here a hard-coded script stands in for a real model, which buys: no API key,
# deterministic results, the same run every time. The real Foundry's test
# suite (1436 tests) runs on a machine with no network and no credentials for
# exactly the same reason.
#
# Real Foundry: core/backends/. Two adapters, openai_compat (Chat Completions)
# and responses, over an HTTP/SSE client written on the stdlib.
# ══════════════════════════════════════════════════════════════════════════

def _call(cid: str, name: str, **args) -> ToolCall:
    return ToolCall(cid, name, json.dumps(args))


FIXED_CODE = "def add(a, b):\n    return a + b\n"

# Placeholder: the backend swaps it for the last [event_id=N] in the
# conversation before sending. See the comment inside SCRIPTS below.
LAST_EVENT_ID = -1

SCRIPTS = {
    # The normal script: run the tests -> read the code -> fix it -> re-run ->
    # finish with evidence
    "fix": [
        ModelTurn("Let me run the tests first and see what's failing.",
                  [_call("c1", "run_command", command="python -m pytest -q")]),
        ModelTurn("The tests fail. Let's look at the source.",
                  [_call("c2", "read_file", path="calc.py")]),
        ModelTurn("`add` was written as a subtraction. Fixing it.",
                  [_call("c3", "write_file", path="calc.py", content=FIXED_CODE)]),
        ModelTurn("Re-running to confirm.",
                  [_call("c4", "run_command", command="python -m pytest -q")]),
        # claim_event_id is written as LAST_EVENT_ID; the backend replaces it
        # with the last [event_id=N] in the conversation before sending. That
        # is exactly what a real model does: it reads the number out of its own
        # tool output and then cites it. Hard-coding a number would not work --
        # the number depends on how much happened this run.
        ModelTurn("", [_call("c5", "finish",
                             summary="Fixed the sign error in add().",
                             claim_event_id=LAST_EVENT_ID)]),
    ],
    # The blocked script: the model (or an injection hidden in a file) asks for
    # something that is never permitted
    "destructive": [
        ModelTurn("Let me clean up the working tree.",
                  [_call("c1", "run_command", command="git reset --hard HEAD")]),
        ModelTurn("I'll commit instead, then.",
                  [_call("c2", "run_command", command="git commit -am wip")]),
        ModelTurn("Fine, I'll just read the file.",
                  [_call("c3", "read_file", path="calc.py")]),
        ModelTurn("", [_call("c4", "finish", summary="Changed nothing.")]),
    ],
    # The liar script: nothing was fixed, but it claims "all tests pass" -- and
    # dutifully cites the command. The citation is real and the command did
    # run, but it exited 1. The gate catches it.
    "liar": [
        ModelTurn("Running the tests.",
                  [_call("c1", "run_command", command="python -m pytest -q")]),
        ModelTurn("", [_call("c2", "finish",
                             summary="All tests pass, task complete.",
                             claim_event_id=LAST_EVENT_ID)]),
    ],
}


class ScriptedBackend:
    """Emits prepared replies in order. A real backend sends HTTP here."""

    def __init__(self, turns: list[ModelTurn]):
        self.turns = turns
        self.index = 0

    def sample(self, messages: list[Message], tools: list[dict]) -> ModelTurn:
        # A real backend, here: translate messages into vendor JSON, attach the
        # tools, send the request, parse the SSE stream, translate back into a
        # ModelTurn. See core/backends/openai_compat.py.
        if self.index >= len(self.turns):
            return ModelTurn("(the script is out of turns)", [])
        turn = self.turns[self.index]
        self.index += 1
        return ModelTurn(turn.text, [self._resolve(c, messages) for c in turn.tool_calls])

    @staticmethod
    def _resolve(call: ToolCall, messages: list[Message]) -> ToolCall:
        """Swap the LAST_EVENT_ID placeholder for the last event number that
        really appeared in the conversation.

        This is not a trick of the scripting mechanism -- it is the real
        model's behaviour: the tool result carries `[event_id=N]`, the model
        reads it, and then cites it in finish.
        """
        if f'"claim_event_id": {LAST_EVENT_ID}' not in call.arguments:
            return call
        seen = re.findall(r"\[event_id=(\d+)\]",
                          "\n".join(m.text for m in messages if m.role == "tool"))
        latest = int(seen[-1]) if seen else 0
        return ToolCall(call.id, call.name, call.arguments.replace(
            f'"claim_event_id": {LAST_EVENT_ID}', f'"claim_event_id": {latest}'))


SYSTEM_PROMPT = """You are a coding agent working in a local Git repository.

Look before you act, then verify. Read a file before you change it. Call
finish when you are done -- if you ran a command to verify, put that result's
[event_id=N] into claim_event_id and we will check its exit code. Leave it out
if you did not verify; a false claim gets caught.
"""


class HttpBackend:
    """Actually talks to a model. Stdlib only, non-streaming, just enough to
    follow.

    This is the thirty-line version of core/backends/openai_compat.py. The
    real one also handles SSE streaming, usage accounting, error taxonomy,
    retries and Retry-After, and how to degrade against a gateway that only
    supports non-streaming.

    It only **translates**: IR in, vendor JSON out; vendor JSON in, IR out.
    Note that it knows nothing of the loop, policy or tools -- swapping models
    does not touch those three.
    """

    def __init__(self, base_url: str, model: str, api_key: str = "any-value",
                 timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.last_response: dict | None = None      # handy to inspect in a notebook

    def sample(self, messages: list[Message], tools: list[dict]) -> ModelTurn:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}]
                        + [self._to_wire(m) for m in messages],
            "tools": [{"type": "function", "function": t} for t in tools],
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {self.api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # The server answered, just not with 200. Carry its own words out --
            # the real Foundry got burned here: the response body was stored and
            # then never read, so the user saw only "request rejected (HTTP 400)"
            # while the body said plainly which field was wrong.
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            # Never connected at all. This is the usual outcome of pointing at
            # the wrong port, and it deserves plain words, not a traceback.
            raise RuntimeError(
                f"cannot reach {self.base_url} ({getattr(exc, 'reason', exc)}). "
                "Check the endpoint and port, or drop --endpoint to use a script."
            ) from exc

        self.last_response = payload
        message = (payload.get("choices") or [{}])[0].get("message", {}) or {}
        calls = [
            ToolCall(c.get("id") or f"call_{i}",
                     (c.get("function") or {}).get("name") or "",
                     # `or "{}"`: an arguments field that is present but null
                     # makes json.loads(None) raise TypeError. The real Foundry
                     # got burned here too.
                     (c.get("function") or {}).get("arguments") or "{}")
            for i, c in enumerate(message.get("tool_calls") or [])
        ]
        return ModelTurn(message.get("content") or "", calls)

    @staticmethod
    def _to_wire(message: Message) -> dict:
        if message.role == "tool":
            return {"role": "tool", "tool_call_id": message.tool_call_id,
                    "content": message.text}
        wire: dict = {"role": message.role, "content": message.text}
        if message.tool_calls:
            wire["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": c.arguments}}
                for c in message.tool_calls
            ]
        return wire


# ══════════════════════════════════════════════════════════════════════════
# 6. Loop: stitching the five together
#
# This is the whole system. Thirty-odd lines.
#
# Real Foundry: core/runtime.py. It adds budget ceilings (rounds / calls /
# tokens), cancellation, re-fetching expired credentials, an error taxonomy
# (what to retry, what to stop on), and context window management. But the
# **shape is identical**.
# ══════════════════════════════════════════════════════════════════════════

MAX_ROUNDS = 12          # runaway guard. The real Foundry also counts tokens
                         # and tool calls.


def run(task: str, backend: ScriptedBackend, tools: Tools, policy: Policy,
        session: Session, auto: str | None) -> int:
    messages = [Message("user", task)]
    session.record("task", {"text": task})

    for round_no in range(1, MAX_ROUNDS + 1):
        turn = backend.sample(messages, tools.schemas())
        if turn.text:
            print(f"\nmodel: {turn.text}")

        # The model asked for no tools -- it is just answering. This run ends.
        if not turn.tool_calls:
            print("\n(the model asked for no tools; stopping.)")
            return 0

        messages.append(Message("assistant", turn.text, tool_calls=turn.tool_calls))

        for call in turn.tool_calls:
            # ---- Step one: validate. Before policy. ----
            try:
                op = tools.validate(call)
            except ValueError as exc:
                print(f"  x invalid call: {exc}")
                # Hand the error **back to the model** so it can fix it itself.
                # Do not crash the run -- given the error text, a model usually
                # gets it right on the next round.
                messages.append(Message("tool", f"error: {exc}", tool_call_id=call.id))
                continue

            # ---- Step two: policy decides ----
            decision = policy.evaluate(op)
            session.record("policy_decision", {
                "target": op.target, "verdict": decision.verdict,
                "reason": decision.reason, "step": decision.step,
            })

            if decision.verdict == DENY:
                print(f"  denied (step {decision.step}): {decision.reason}")
                messages.append(Message("tool", f"denied by policy: {decision.reason}",
                                        tool_call_id=call.id))
                continue

            if decision.verdict == ASK:
                if not ask_human(op, decision, auto):
                    print("  denied by the user")
                    messages.append(Message("tool", "the user denied this operation",
                                            tool_call_id=call.id))
                    continue
                policy.session_grants.add(op.target)

            # ---- Step three: execute ----
            print(f"  -> {op.display}")
            if op.tool == "finish":
                return finalize(op, session)
            try:
                result = tools.run(op, session)
            except Exception as exc:                       # noqa: BLE001
                result = f"the tool failed: {exc}"
            print(f"    {result.splitlines()[0] if result else '(empty)'}")

            # ---- Step four: feed the result back, then ask the model again ----
            messages.append(Message("tool", result, tool_call_id=call.id))

    print(f"\n! hit the {MAX_ROUNDS}-round ceiling without finishing.")
    return 10


def finalize(op: Operation, session: Session) -> int:
    """The finish gate: **only here can anything report "done"**, and a claim
    has to come with evidence.

    The model saying "the tests pass" is not enough -- it has to point at
    **which command** proves it, and we look that command's exit code up in
    the ledger. If they do not match, the run is downgraded to partial.

    Real Foundry: core/tools/finish.py + runtime._finalize. It also checks
    whether HEAD moved, and which files this session actually changed.
    """
    summary = op.args.get("summary", "")
    claim_id = op.args.get("claim_event_id")

    print(f"\nfinish: {summary}")

    if claim_id is None:
        print("status: completed (no verification claimed)")
        session.record("termination", {"status": "completed", "summary": summary})
        return 0

    command = session.find_command(claim_id)
    if command is None:
        print(f"status: partial -- cited event {claim_id} is not a command record")
        session.record("termination", {"status": "partial", "summary": summary})
        return 10
    if command["exit_code"] != 0:
        print(f"status: partial -- event {claim_id} ({command['command']}) "
              f"actually exited {command['exit_code']}, not 0")
        session.record("termination", {"status": "partial", "summary": summary})
        return 10

    print(f"status: completed -- evidence checks out "
          f"(event {claim_id}: {command['command']} -> exit 0)")
    session.record("termination", {"status": "completed", "summary": summary})
    return 0


# ══════════════════════════════════════════════════════════════════════════
# 7. main: wire it up and run
# ══════════════════════════════════════════════════════════════════════════

SAMPLE = {
    "calc.py": "def add(a, b):\n    return a - b\n",
    "test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
}


def make_sample_repo(root: Path) -> Path:
    """Built from scratch each time, so repeated runs are deterministic (the
    last run's fix does not survive into this one)."""
    workspace = root / "sample_repo"
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)
    for name, content in SAMPLE.items():
        (workspace / name).write_text(content, encoding="utf-8")
    return workspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mini Foundry")
    parser.add_argument("--script", default="fix", choices=sorted(SCRIPTS),
                        help="which script to run (fix=the normal bug fix, "
                             "destructive=stopped by the breaker table, "
                             "liar=false evidence caught)")
    parser.add_argument("--mode", default="default",
                        choices=["default", "accept_edits", "plan"])
    parser.add_argument("--yes", action="store_true", help="approve every prompt")
    parser.add_argument("--no", action="store_true",
                        help="deny every prompt (this is what headless does)")
    parser.add_argument("--workdir", default=str(Path(__file__).parent / ".run"))
    parser.add_argument("--endpoint", help="use a real model instead of a script, e.g. "
                                           "http://127.0.0.1:18790/v1")
    parser.add_argument("--model", default="openclaw/trade-advisor-panel")
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", "any-value"))
    parser.add_argument("--task",
                        default="add in calc.py has a bug. Fix it and confirm the "
                                "tests pass.")
    args = parser.parse_args(argv)

    root = Path(args.workdir)
    workspace = make_sample_repo(root)
    session = Session(root / "session.jsonl")
    auto = "y" if args.yes else ("n" if args.no else None)

    if args.endpoint:
        backend = HttpBackend(args.endpoint, args.model, args.api_key)
        source = f"{args.model} @ {args.endpoint}"
    else:
        backend = ScriptedBackend(SCRIPTS[args.script])
        source = f"script {args.script}"

    print("-" * 68)
    print(f"workspace : {workspace}")
    print(f"model     : {source}      mode: {args.mode}")
    print(f"ledger    : {session.path}")
    print("-" * 68)

    started = time.monotonic()
    try:
        code = run(
            task=args.task,
            backend=backend,
            tools=Tools(workspace),
            policy=Policy(mode=args.mode, allow_rules=["python -m pytest"]),
            session=session,
            auto=auto,
        )
    except RuntimeError as exc:
        print(f"\nbackend error: {exc}")
        code = 12
    finally:
        session.close()

    print("-" * 68)
    print(f"exit code {code} (0=completed 10=partial)  took "
          f"{time.monotonic() - started:.1f}s")
    print(f"the ledger holds {session.ordinal} records; `type {session.path}` "
          f"shows all of them.")
    return code


if __name__ == "__main__":
    sys.exit(main())
