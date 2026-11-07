"""Continuing a session from its journal (D-035).

The journal is the source of truth, so resume reads nothing else. Every
message the model was sent is in a ``model_request`` record: history only
grows, masking rewrites a tool result's content but never drops a message, and
a message is never masked in the first request that carries it -- the newest
rounds are exempt. So the history is each request's messages, taken where they
first appear. What came after the last request -- its response, the tool
replies, input that never reached a model -- is in the records written for
exactly that.

What the journal cannot supply is decided conservatively:

* a tool call with no recorded reply gets one saying what is known about it.
  Providers reject a tool call without a result, and the model must not guess
  whether an interrupted command ran;
* evidence starts over. Commands run before the resume cannot back a finish
  claim and every file is read again before it is patched, because the
  workspace may have changed while the session was stopped.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
from dataclasses import dataclass, replace
from itertools import islice
from pathlib import Path
from typing import Any, Iterator

from foundry.core.conversation import (
    IR_VERSION,
    ContentBlock,
    ImageBlock,
    Message,
    ModelTurn,
    OpaqueBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)
from foundry.core.context import unbind
from foundry.core.errors import ResumeError, SessionLocked
from foundry.core.events import PlanStep, TerminalStatus
from foundry.core.goal import ACTIVE, Goal, spent
from foundry.core.policy.engine import normalize_path
from foundry.core.session import (
    SCHEMA_VERSION,
    EventType,
    JournalRecord,
    SessionStore,
    is_locked,
    new_session_id,
)
from foundry.core.tools.git import GitBaseline
from foundry.core.tools.plan import steps_from_payload

# The task a headless resume continues with when it is given none.
CONTINUE_PROMPT = ("Continue the task from where this session stopped. Check the current "
                   "state of the workspace before relying on earlier results.")

# How many outside changes the resume note names before it summarizes.
_NOTE_PATHS = 20


# --- decoding --------------------------------------------------------------


def _text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ResumeError(f"a recorded message block has no text field {key!r}")
    return value


_ARTIFACT_ID = re.compile(r"[0-9a-f]{64}")
IMAGE_NOT_KEPT = "[an image was here; it was not kept, so it is not shown again]"


def image_loader(artifacts: Path):
    """Reads a recorded image's bytes back from the session's artifacts, each
    once however many requests repeat it. None for one that is not there."""
    cache: dict[str, str | None] = {}

    def load(artifact: Any) -> str | None:
        if not isinstance(artifact, str) or not _ARTIFACT_ID.fullmatch(artifact):
            return None
        if artifact not in cache:
            try:
                cache[artifact] = base64.b64encode(
                    (artifacts / artifact).read_bytes()).decode("ascii")
            except OSError:
                cache[artifact] = None
        return cache[artifact]

    return load


def _image(payload: Any, load) -> ImageBlock | None:
    if not isinstance(payload, dict) or payload.get("kind") != "image":
        raise ResumeError("a recorded image is not an image block")
    data = load(payload.get("artifact")) if load is not None else None
    if data is None:
        return None
    name = payload.get("name")
    return ImageBlock(media_type=_text(payload, "media_type"), data=data,
                      name=name if isinstance(name, str) else "")


def decode_block(payload: Any, load=None) -> ContentBlock:
    """The inverse of ``AgentRuntime._block_payload``. ``load`` reads a
    recorded image back (``image_loader``); an image it cannot read becomes a
    note saying so, never a failed resume."""
    if not isinstance(payload, dict):
        raise ResumeError("a recorded message block is not an object")
    kind = payload.get("kind")
    if kind == "text":
        return TextBlock(_text(payload, "text"))
    if kind == "tool_use":
        return ToolUseBlock(call_id=_text(payload, "call_id"), name=_text(payload, "name"),
                            arguments=_text(payload, "arguments"))
    if kind == "tool_result":
        content = _text(payload, "content")
        recorded = payload.get("images") or []
        if not isinstance(recorded, list):
            raise ResumeError("a recorded tool result's images are not a list")
        images = [_image(i, load) for i in recorded]
        if None in images:
            content += "\n" + IMAGE_NOT_KEPT
        return ToolResultBlock(call_id=_text(payload, "call_id"), content=content,
                               is_error=payload.get("is_error") is True,
                               images=tuple(i for i in images if i is not None))
    if kind == "opaque":
        return OpaqueBlock(provider=_text(payload, "provider"), data=_text(payload, "data"))
    if kind == "image":
        return _image(payload, load) or TextBlock(IMAGE_NOT_KEPT)
    raise ResumeError(f"a recorded message block has an unknown kind: {kind!r}")


def decode_message(payload: Any, load=None) -> Message:
    if not isinstance(payload, dict) or not isinstance(payload.get("blocks"), list):
        raise ResumeError("a recorded message is not an object with blocks")
    try:
        role = Role(payload.get("role"))
    except ValueError:
        raise ResumeError(f"a recorded message has an unknown role: {payload.get('role')!r}")
    return Message(role=role, blocks=tuple(decode_block(b, load) for b in payload["blocks"]))


def _call_ids(message: Message) -> tuple[str, ...]:
    return tuple(b.call_id for b in message.blocks
                 if isinstance(b, (ToolUseBlock, ToolResultBlock)))


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


# --- the state a journal leaves --------------------------------------------


@dataclass(frozen=True, slots=True)
class Repair:
    """A tool call that had no recorded reply, and the reply written for it."""

    call_id: str
    tool: str
    outcome: str  # finish_accepted | finish_interrupted | not_run | unknown | output_lost

    def as_payload(self) -> dict[str, str]:
        return {"call_id": self.call_id, "tool": self.tool, "outcome": self.outcome}


@dataclass(frozen=True, slots=True)
class JournalState:
    session_id: str
    workspace: str
    profile: str
    model: str
    foundry_version: str
    baseline: GitBaseline
    history: tuple[Message, ...]
    turns: tuple[int, ...]
    turn: int
    usage: Usage
    status: TerminalStatus
    reason: str
    first_task: str
    # Files the session asked apply_patch to change, normalized.
    patched_paths: frozenset[str]
    repairs: tuple[Repair, ...]
    # Recorded before resume existed: rounds are estimated, and the last
    # response's provider blocks and tool output were never kept.
    legacy: bool
    # Files an undo put back after the model last heard from the session, so
    # its history still shows its own patches in them (D-036).
    undone: tuple[str, ...] = ()
    # The plan its last update_plan set (D-037); the model's history holds the
    # same call, so only the runtime and the user need it back.
    plan: tuple[PlanStep, ...] = ()
    plan_explanation: str = ""
    # The mode the user last switched the session to (D-038), "" if never. A
    # resume starts in the configured mode, as it starts every grant over.
    mode: str = ""
    # The model /model last chose (D-047); a resume runs on the configured one.
    switched_model: str = ""
    # The last request of the conversation as sent (D-048): a resume compares
    # its first request with it.
    last_sent: tuple[Message, ...] = ()
    # What the user typed for a message that says more (D-108): (the message's
    # text, the words typed), for a resume to show the conversation as typed.
    typed: tuple[tuple[str, str], ...] = ()
    # The name /rename last gave it (D-125), "" when none.
    name: str = ""
    # The session it is a fork of, while it has not run since it was forked
    # (D-126); "" otherwise.
    fork_of: str = ""
    # The goal as its last record left it (D-127), with what the responses
    # after that record spent while it was active; None for none.
    goal: Goal | None = None


def _status(raw: Any) -> TerminalStatus:
    try:
        return TerminalStatus(raw)
    except ValueError:
        return TerminalStatus.INTERRUPTED


def read_journal(path: Path) -> JournalState:
    """Everything a resumed session needs, from its journal alone.

    Raises ResumeError for a journal this version cannot continue; never
    anything else, whatever the file holds.
    """
    try:
        records = list(SessionStore.read_records(path))
    except (OSError, UnicodeDecodeError) as exc:
        raise ResumeError(f"cannot read the journal: {exc}") from exc

    meta = next((r for r in records if r.type == EventType.SESSION_META), None)
    if meta is None:
        raise ResumeError("the journal has no session header")
    if (_int(meta.payload.get("schema_version")) > SCHEMA_VERSION
            or any(_int(r.v) > SCHEMA_VERSION for r in records)):
        raise ResumeError("this session was recorded by a newer Foundry; resume it with that version")
    if meta.payload.get("ir_version", IR_VERSION) != IR_VERSION:
        raise ResumeError("this session was recorded with a conversation format this "
                          "version does not read")
    base = next((r for r in records
                 if r.type == EventType.GIT_BASELINE and "phase" not in r.payload), None)
    if base is None:
        raise ResumeError("the journal has no git baseline")
    try:
        baseline = GitBaseline.from_payload(base.payload)
    except ValueError as exc:
        raise ResumeError(f"the journal's git baseline is unreadable: {exc}") from exc

    load = image_loader(path.parent / "artifacts")
    history, turns, last_request, turn = _fold_requests(records, load)
    tail_turn = turn
    for record in records[last_request + 1:]:
        message = _tail_message(record, load)
        if message is not None:
            history.append(message)
            turns.append(tail_turn)
    history, turns, repairs = _repair(history, turns, records, last_request)

    status, reason = TerminalStatus.INTERRUPTED, ""
    usage = Usage()
    executed: set[str] = set()
    first_task = ""
    typed: list[tuple[str, str]] = []
    for record in records:
        if record.type == EventType.RESUMED:
            status, reason = TerminalStatus.INTERRUPTED, ""
        elif record.type == EventType.TERMINATION:
            status = _status(record.payload.get("status"))
            reason = str(record.payload.get("reason", ""))
        elif record.type == EventType.TOKEN_USAGE:
            usage = usage + Usage(_int(record.payload.get("input_tokens")),
                                  _int(record.payload.get("output_tokens")),
                                  _int(record.payload.get("cached_input_tokens")))
        elif record.type == EventType.TOOL_RESULT:
            executed.add(str(record.payload.get("call_id")))
        elif record.type == EventType.USER_INPUT:
            text = str(record.payload.get("text", ""))
            said = record.payload.get("typed")
            if isinstance(said, str):
                typed.append((text, said))
            if not first_task:
                first_task = said if isinstance(said, str) else text
    if not first_task:
        first_task = next((b.text for m in history if m.role is Role.USER
                           for b in m.blocks if isinstance(b, TextBlock)), "")

    patched: set[str] = set()
    for record in records:
        paths = record.payload.get("paths")
        if (record.type == EventType.TOOL_CALL and isinstance(paths, list)
                and str(record.payload.get("call_id")) in executed):
            patched.update(normalize_path(p) for p in paths if isinstance(p, str))

    plan: tuple[PlanStep, ...] = ()
    plan_explanation = ""
    mode = ""
    switched_model = ""
    name = ""
    goal: Goal | None = None
    for record in records:
        if (named := _named(record)) is not None:
            name = named
        elif record.type == EventType.GOAL:
            goal = None if record.payload.get("cleared") else Goal.from_payload(record.payload)
        elif record.type == EventType.TOKEN_USAGE and goal is not None and goal.status == ACTIVE:
            # Spent after the record, by a session that did not get to say so.
            goal = replace(goal, tokens_used=goal.tokens_used + spent(Usage(
                _int(record.payload.get("input_tokens")),
                _int(record.payload.get("output_tokens")),
                _int(record.payload.get("cached_input_tokens")))))
        elif record.type == EventType.TOOL_RESULT and "plan" in record.payload:
            plan, plan_explanation = steps_from_payload(record.payload["plan"])
        elif record.type == EventType.MODE_CHANGE and isinstance(record.payload.get("to"), str):
            mode = record.payload["to"]
        elif record.type == EventType.MODEL_CHANGE and isinstance(record.payload.get("to"), str):
            switched_model = record.payload["to"]

    undone: list[str] = []
    for record in records[last_request + 1:]:
        if record.type == EventType.UNDO:
            for entry in record.payload.get("files") or []:
                name = entry.get("path") if isinstance(entry, dict) else None
                if isinstance(name, str) and name not in undone:
                    undone.append(name)

    requests = [r for r in records if r.type == EventType.MODEL_REQUEST]
    return JournalState(
        session_id=str(meta.payload.get("session_id", path.parent.name)),
        workspace=str(meta.payload.get("workspace", "")),
        profile=str(meta.payload.get("profile", "")),
        model=str(meta.payload.get("model", "")),
        foundry_version=str(meta.payload.get("foundry_version", "")),
        baseline=baseline,
        history=tuple(history),
        turns=tuple(turns),
        turn=turn,
        usage=usage,
        status=status,
        reason=reason,
        first_task=first_task,
        patched_paths=frozenset(patched),
        repairs=tuple(repairs),
        legacy=any("turn" not in r.payload for r in requests),
        undone=tuple(undone),
        plan=plan,
        plan_explanation=plan_explanation,
        mode=mode,
        switched_model=switched_model,
        typed=tuple(typed),
        last_sent=_last_sent(records, load),
        name=name,
        fork_of=_fork_of(meta, records),
        goal=goal,
    )


def _fork_of(meta: JournalRecord, records: list[JournalRecord]) -> str:
    """The session this one was forked from, if nothing was recorded since."""
    source = meta.payload.get("forked_from")
    forked_at = _int(meta.payload.get("forked_at"))
    if not isinstance(source, str) or any(_int(r.ordinal) > forked_at for r in records):
        return ""
    return source


def _fold_requests(records: list[JournalRecord],
                   load=None) -> tuple[list[Message], list[int], int, int]:
    """History from the requests, each message where it first appears.

    A message that first appears in round T's request was appended during
    round T-1: the user's text before the round began, the model's reply and
    the tool results during it.
    """
    history: list[Message] = []
    turns: list[int] = []
    responses = 0
    answered = False  # a response was recorded since the last request
    original = None   # the opaque block of the latest response, as received
    last_request, last_turn = -1, 0
    for index, record in enumerate(records):
        # A compaction's own request and response are not the conversation;
        # the compacted record that follows says what the history became.
        if record.payload.get("purpose"):
            continue
        if record.type == EventType.COMPACTED:
            raw = record.payload.get("messages")
            if not isinstance(raw, list) or not raw:
                raise ResumeError(f"inconsistent journal at record {record.ordinal}: "
                                  "a compaction without the history it left")
            history = [decode_message(m, load) for m in raw]
            turn = max(_int(record.payload.get("turn")), last_turn)
            turns = [turn] * len(history)
            answered = False
            original = None
            last_request, last_turn = index, turn
            continue
        if record.type == EventType.REWOUND:
            # /rewind (D-056): the history it kept, with each message's round.
            raw, kept = record.payload.get("messages"), record.payload.get("turns")
            turn = max(_int(record.payload.get("turn")), last_turn)
            if (not isinstance(raw, list) or not isinstance(kept, list)
                    or len(raw) != len(kept) or not all(type(t) is int for t in kept)
                    or any(a > b for a, b in zip(kept, kept[1:]))
                    or (kept and not (0 <= kept[0] and kept[-1] <= turn))):
                raise ResumeError(f"inconsistent journal at record {record.ordinal}: "
                                  "a rewind without the history it kept")
            history = [decode_message(m, load) for m in raw]
            turns = list(kept)
            answered = False
            original = None
            last_request, last_turn = index, turn
            continue
        if record.type == EventType.MODEL_RESPONSE:
            responses += 1
            answered = True
            opaque = record.payload.get("opaque")
            original = (decode_block({"kind": "opaque", **opaque}) if isinstance(opaque, dict)
                        else None)
            continue
        if record.type != EventType.MODEL_REQUEST:
            continue
        raw = record.payload.get("messages")
        if not isinstance(raw, list) or not raw or not isinstance(raw[0], dict) \
                or raw[0].get("role") != Role.SYSTEM.value:
            raise ResumeError(f"inconsistent journal at record {record.ordinal}: "
                              "a request does not start with its system message")
        body = [decode_message(m, load) for m in raw[1:]]
        # Journals written before resume existed have no round number; a
        # request follows one response per completed round.
        turn = _int(record.payload.get("turn")) or responses + 1
        # Each request carries everything before it; after a response, it
        # carries that response next. Only a retried request adds nothing.
        if len(body) < len(history) or any(
                old.role is not new.role or _call_ids(old) != _call_ids(new)
                for old, new in zip(history, body)) or (
                answered and (len(body) == len(history)
                              or body[len(history)].role is not Role.ASSISTANT)):
            raise ResumeError(f"inconsistent journal at record {record.ordinal}: a request "
                              "does not continue the conversation before it")
        answered = False
        for message in body[len(history):]:
            history.append(_as_received(message, original))
            turns.append(max(turn - 1, turns[-1] if turns else 0))
        last_request, last_turn = index, max(last_turn, turn)
    return history, turns, last_request, last_turn


def _as_received(message: Message, original) -> Message:
    """The reply as it was received. A request sent after an edit carries it
    without its bound reasoning (D-048), and if that request was the first to
    carry it, that is the copy the fold sees; the response record kept the
    original, and the live history has it."""
    if message.role is not Role.ASSISTANT or original is None:
        return message
    whole = Message(role=message.role, blocks=(
        *(b for b in message.blocks if not isinstance(b, OpaqueBlock)), original))
    return whole if whole != message and unbind(whole) == message else message


def _last_sent(records: list[JournalRecord], load=None) -> tuple[Message, ...]:
    """The conversation's last request as it was sent, system message
    included; nothing after a compaction, which starts a new prefix (D-048)."""
    sent: list = []
    for record in records:
        if record.payload.get("purpose"):
            continue
        if record.type == EventType.COMPACTED:
            sent = []
        elif record.type == EventType.REWOUND:
            # What was kept is unchanged, so it is still the prefix sent.
            sent = sent[:_int(record.payload.get("keep")) + 1]
        elif record.type == EventType.MODEL_REQUEST:
            raw = record.payload.get("messages")
            sent = raw if isinstance(raw, list) else []
    return tuple(decode_message(m, load) for m in sent)


def _tail_message(record: JournalRecord, load=None) -> Message | None:
    payload = record.payload
    if payload.get("purpose"):
        return None
    if record.type == EventType.MODEL_RESPONSE:
        calls = payload.get("tool_calls", [])
        if not isinstance(calls, list):
            raise ResumeError(f"a recorded response has no tool call list (record {record.ordinal})")
        opaque = payload.get("opaque")
        block = decode_block({"kind": "opaque", **opaque}) if isinstance(opaque, dict) else None
        return ModelTurn(
            text=payload.get("text", "") if isinstance(payload.get("text"), str) else "",
            tool_calls=tuple(decode_block({"kind": "tool_use", **c}) if isinstance(c, dict)
                             else decode_block(c) for c in calls),
            opaque=block,
        ).as_message()
    if record.type == EventType.TOOL_REPLY:
        return Message(role=Role.TOOL,
                       blocks=(decode_block({"kind": "tool_result", **payload}, load),))
    if record.type == EventType.USER_INPUT:
        images = payload.get("images") or []
        if not isinstance(images, list):
            raise ResumeError(f"a recorded user input's images are not a list "
                              f"(record {record.ordinal})")
        return Message(role=Role.USER, blocks=(
            *(decode_block(i, load) for i in images), TextBlock(_text(payload, "text"))))
    return None


def _repair(history: list[Message], turns: list[int], records: list[JournalRecord],
            last_request: int) -> tuple[list[Message], list[int], list[Repair]]:
    """Give every tool call a result, right after the results it already has."""
    dispatched = {r.payload["call_id"] for r in records if r.type == EventType.TOOL_CALL
                  and isinstance(r.payload.get("call_id"), str)}
    results = {r.payload["call_id"]: r.payload.get("is_error") is True for r in records
               if r.type == EventType.TOOL_RESULT and isinstance(r.payload.get("call_id"), str)}
    ending = next((r for r in records[last_request + 1:] if r.type == EventType.TERMINATION),
                  None)

    out_history: list[Message] = []
    out_turns: list[int] = []
    repairs: list[Repair] = []
    index = 0
    while index < len(history):
        message = history[index]
        out_history.append(message)
        out_turns.append(turns[index])
        index += 1
        if message.role is not Role.ASSISTANT:
            continue
        uses = [b for b in message.blocks if isinstance(b, ToolUseBlock)]
        answered: set[str] = set()
        while index < len(history) and history[index].role is Role.TOOL:
            answered.update(_call_ids(history[index]))
            out_history.append(history[index])
            out_turns.append(turns[index])
            index += 1
        for use in uses:
            if use.call_id in answered:
                continue
            outcome, content, is_error = _explain(use, dispatched, results, ending)
            out_history.append(Message(role=Role.TOOL, blocks=(
                ToolResultBlock(call_id=use.call_id, content=content, is_error=is_error),)))
            out_turns.append(out_turns[-1])
            repairs.append(Repair(use.call_id, use.name, outcome))
    return out_history, out_turns, repairs


def _explain(use: ToolUseBlock, dispatched: set, results: dict,
             ending: JournalRecord | None) -> tuple[str, str, bool]:
    if use.name == "finish":
        if ending is not None:
            status = ending.payload.get("status", "unknown")
            reason = str(ending.payload.get("reason", ""))
            detail = "" if reason in ("", "reported by the agent") else f": {reason}"
            return ("finish_accepted",
                    f"finish was accepted: the session ended with status {status}{detail}.", False)
        return ("finish_interrupted", "finish was called, but the session was interrupted "
                "before the outcome was recorded.", True)
    if use.call_id in results:
        failed = results[use.call_id]
        return ("output_lost",
                f"This call ran{', and reported an error,' if failed else ''} but its output was "
                "not kept in a form that can be restored. Call it again if you need the result.",
                failed)
    if use.call_id in dispatched:
        return ("unknown", "No result recorded: the session was interrupted while this call was "
                "being handled, so whether it ran, and what it changed, is not known. Check "
                "before relying on it.", True)
    why = str(ending.payload.get("reason", "")) if ending is not None else ""
    return ("not_run", f"Not run: the session stopped before this call was executed "
            f"({why or 'it was interrupted'}). Nothing was changed by it; call it again if it "
            "is still needed.", True)


# --- finding a session and checking it can continue ---------------------------


def same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


# A session's name (D-125): one line, short enough for a listing.
MAX_NAME = 80


def session_name(raw: str) -> str:
    """``raw`` as a session's name, or ValueError saying why it cannot be
    one. It must be typeable after `foundry resume`: one line, no control
    character or double quote, not ``last`` and not an option."""
    name = raw.strip()
    if not name:
        raise ValueError("a name cannot be empty")
    if "\n" in name or "\r" in name:
        raise ValueError("a name is one line")
    if any(ord(c) < 32 or ord(c) == 127 or 0x80 <= ord(c) < 0xA0 for c in name):
        raise ValueError("a name cannot hold a control character")
    if '"' in name:
        raise ValueError("a name cannot hold a double quote")
    if len(name) > MAX_NAME:
        raise ValueError(f"a name is at most {MAX_NAME} characters")
    if name == "last":
        raise ValueError("`last` already means the newest session")
    if name.startswith("-"):
        raise ValueError("a name cannot start with -, which starts an option")
    return name


def _named(record: JournalRecord) -> str | None:
    name = record.payload.get("name")
    return name if record.type == EventType.RENAMED and isinstance(name, str) else None


def journal_name(journal: Path) -> str:
    """The name /rename last gave the session, "" when it has none."""
    name = ""
    for record in SessionStore.read_records(journal):
        if (named := _named(record)) is not None:
            name = named
    return name


def named_session(sessions: Path, name: str, workspace: Path | None) -> str | None:
    """The newest session of this workspace -- of any, for None -- that is
    named ``name`` (D-125)."""
    for entry in _workspace_sessions(sessions, workspace):
        try:
            if journal_name(entry / "events.jsonl") == name:
                return entry.name
        except (OSError, UnicodeDecodeError):
            continue
    return None


def fork_journal(sessions: Path, source: Path) -> str:
    """A new session holding ``source``'s conversation, as codex's fork
    makes one (D-126); its id. The journal is copied as it is -- each record
    with its ordinal, so what the conversation cites still names it -- but
    for its header, which names the new session, the one it came from and
    where, and for the source's names, which stay the source's. The
    artifacts it cites come too. The source is only read."""
    records: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue    # a line a killed process left half written
            if isinstance(record, dict) and record.get("type") != EventType.RENAMED:
                records.append(record)
    forked = new_session_id()
    forked_at = max((_int(r.get("ordinal")) for r in records), default=0)
    for record in records:
        if record.get("type") == EventType.SESSION_META and isinstance(
                record.get("payload"), dict):
            record["payload"] = {**record["payload"], "session_id": forked,
                                 "forked_from": source.parent.name, "forked_at": forked_at}
    target = sessions / forked
    target.mkdir(parents=True)
    try:
        if (source.parent / "artifacts").is_dir():
            shutil.copytree(source.parent / "artifacts", target / "artifacts")
        with (target / "events.jsonl").open("x", encoding="utf-8", newline="\n") as out:
            out.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    except OSError:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return forked


def find_journal(sessions: Path, session_id: str) -> Path | None:
    """A session id is a name, not a path: ``../notes`` names nothing."""
    target = sessions / session_id / "events.jsonl"
    try:
        inside = target.parent.resolve().parent == sessions.resolve()
    except (OSError, ValueError):
        inside = False
    return target if inside and target.is_file() else None


def resolve_session(sessions: Path, session_id: str, workspace: Path) -> Path:
    """The journal a resume names: an id, ``last`` for this workspace's
    newest, or the name /rename gave one of its sessions (D-125) -- an id
    first, as codex looks."""
    if session_id == "last":
        latest = latest_session(sessions, workspace)
        if latest is None:
            raise ResumeError(f"no recorded session in {workspace}")
        session_id = latest
    journal = find_journal(sessions, session_id)
    if journal is None:
        named = named_session(sessions, session_id, workspace)
        journal = find_journal(sessions, named) if named is not None else None
    if journal is None:
        if session_named(sessions.parent / ARCHIVED, session_id) is not None:
            raise ResumeError(f"session {session_id} is archived; foundry unarchive "
                              f"{session_id} brings it back")
        raise ResumeError(f"no such session: {session_id}")
    return journal


# Where an archived session's folder is kept (D-128), beside sessions/, as
# codex keeps archived_sessions: out of every list and lookup, kept whole.
ARCHIVED = "archived_sessions"


def session_named(sessions: Path, target: str) -> str | None:
    """The session ``target`` names: its id, or the name /rename gave it --
    the newest of that name, in any workspace (D-128); None for none."""
    if find_journal(sessions, target) is not None:
        return target
    return named_session(sessions, target, None)


def move_session(source: Path, destination: Path) -> None:
    """Archive or unarchive (D-128): the session's folder moved whole --
    never while a store holds it, never over another."""
    if is_locked(source):
        raise SessionLocked(f"session {source.name} is in use by a Foundry session")
    if destination.exists():
        raise ResumeError(f"{destination} already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)


def delete_session(folder: Path) -> None:
    """Delete (D-128): the session's folder -- its journal and artifacts --
    for good; never while a store holds it."""
    if is_locked(folder):
        raise SessionLocked(f"session {folder.name} is in use by a Foundry session")
    shutil.rmtree(folder)


def latest_session(sessions: Path, workspace: Path | None) -> str | None:
    """The newest session recorded in this workspace -- in any, for None
    (D-088). Ids sort by start time."""
    return next((entry.name for entry in _workspace_sessions(sessions, workspace)), None)


def recorded_workspace(session_dir: Path) -> str:
    """The workspace a session's header names; empty if it names none."""
    try:
        first = next(SessionStore.read_records(session_dir / "events.jsonl"), None)
    except (OSError, UnicodeDecodeError):
        return ""
    recorded = first.payload.get("workspace") if first is not None else None
    return recorded if isinstance(recorded, str) else ""


def _workspace_sessions(sessions: Path, workspace: Path | None) -> Iterator[Path]:
    """The folders of the sessions recorded in this workspace, newest first;
    with None, every session that names its workspace (D-088)."""
    if not sessions.is_dir():
        return
    for entry in sorted((p for p in sessions.iterdir() if p.is_dir()), reverse=True):
        journal = entry / "events.jsonl"
        if not journal.is_file():
            continue
        try:
            first = next(SessionStore.read_records(journal), None)
        except (OSError, UnicodeDecodeError):
            continue
        recorded = first.payload.get("workspace") if first is not None else None
        if isinstance(recorded, str) and recorded and (
                workspace is None or same_path(recorded, workspace)):
            yield entry


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """What the resume picker and `foundry sessions` show of one (D-057)."""
    session_id: str
    status: TerminalStatus
    first_task: str
    in_use: bool
    # The workspace it was recorded in, for a list of every workspace's (D-088).
    workspace: str = ""
    # The name /rename gave it (D-125).
    name: str = ""


def summarize(session_dir: Path) -> SessionSummary:
    """How the session ended (``interrupted`` without a record of it), the
    first message the user sent, and whether a store holds it now."""
    journal = session_dir / "events.jsonl"
    first_task = name = ""
    try:
        for record in SessionStore.read_records(journal):
            if record.type == EventType.USER_INPUT and not first_task:
                said = record.payload.get("typed")
                first_task = (said if isinstance(said, str)
                              else str(record.payload.get("text", "")))
            elif (named := _named(record)) is not None:
                name = named
        status = SessionStore.terminal_status(journal)
    except (OSError, UnicodeDecodeError):
        status = TerminalStatus.INTERRUPTED
    return SessionSummary(session_dir.name, status, first_task, is_locked(session_dir),
                          recorded_workspace(session_dir), name)


def recent_sessions(sessions: Path, workspace: Path | None, limit: int) -> list[SessionSummary]:
    """This workspace's newest ``limit`` sessions, newest first (D-057); every
    workspace's, for None (D-088)."""
    return [summarize(entry) for entry in islice(_workspace_sessions(sessions, workspace), limit)]


def outside_changes(state: JournalState, current: GitBaseline) -> frozenset[str]:
    """Refuse a moved HEAD; name the files that changed outside the session.

    The session's evidence and its account of what it changed are relative to
    the commit it started on. A file that is dirty now, was clean then, and
    was not patched by the session itself is a change it did not make, and the
    dirty-file guard protects it like one.
    """
    if current.head != state.baseline.head:
        raise ResumeError(
            f"HEAD moved since this session started ({state.baseline.head[:12] or 'no commit'} "
            f"-> {current.head[:12] or 'no commit'}); its evidence and its account of what it "
            f"changed are relative to {state.baseline.head[:12] or 'the start'}. Check that "
            "commit out to resume, or start a new session.")
    before = {normalize_path(p) for p in state.baseline.dirty_paths | state.baseline.untracked_paths}
    return frozenset(p for p in current.dirty_paths | current.untracked_paths
                     if normalize_path(p) not in before
                     and normalize_path(p) not in state.patched_paths)


def _names(names: list[str]) -> str:
    shown = ", ".join(names[:_NOTE_PATHS])
    if len(names) > _NOTE_PATHS:
        shown += f", and {len(names) - _NOTE_PATHS} more"
    return shown


def resume_note(state: JournalState, outside: frozenset[str], mode: str = "") -> str:
    """What the model is told before its first task after a resume, in ``mode``."""
    ended = state.status.value + (f" ({state.reason})" if state.reason else "")
    lines = [f"This session was resumed. When it stopped, its status was {ended}."]
    if state.fork_of:
        # A fork's first run (D-126): the conversation is another session's.
        lines = [f"This session is a fork of session {state.fork_of}: the conversation so "
                 "far is that session's, and it goes on here apart from it."]
    if outside:
        shown = _names(sorted(outside))
        lines.append("These files have changes this session did not make with apply_patch "
                     f"(made outside it, or by a command it ran); patching them asks first: {shown}.")
    if state.undone:
        lines.append("After it stopped, the user undid patches it had made: these files hold "
                     "what they held before them again, or were removed if the session had "
                     f"created them: {_names(sorted(state.undone))}.")
    if state.mode and mode and state.mode != mode:
        lines.append(f"Before it stopped, the user had switched this session to {state.mode} "
                     f"mode. That does not carry over a resume: this run is in {mode} mode, "
                     "as the system prompt says.")
    lines.append("Commands run before this resume cannot be cited in finish claims; run them "
                 "again. Read a file again before patching it.")
    return "\n".join(lines)
