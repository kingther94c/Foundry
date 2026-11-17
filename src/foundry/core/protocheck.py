"""The protocol level of ``foundry checkup`` (D-205).

A gateway that is "OpenAI-compatible" may be so for a chat and not for an
agent: it may drop the tools from a request, mangle a call's arguments,
refuse the tool result that continues it, ignore a ceiling on the reply, or
hold a stream back until it ends. Each of these is asked here once, through
the adapter a session uses, with fixed prompts and a toy tool -- nothing
from the workspace -- and each says in one line what came back.

A failure's code is the case's stem and what went wrong: ``TXS401`` is the
streamed text case answered HTTP 401; ``TCS0`` the streamed tool case
answered with no call. The stems: TXS and TXO the text streamed and once,
STR the stream's pace, TCS and TCO the tool call streamed and once, CON the
continuation, CUT the reply at a ceiling, BAD a model that does not exist.
After them, an HTTP status, or a letter: D the stream was cut, T no answer
in time, N no connection, P an answer that could not be read, X Foundry
failed reading it, E no text, R a refusal, 0 no call, J arguments that are
not an object, A other arguments, I a ceiling ignored, U a cut not said.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from foundry.core.backends.base import TextDelta, TurnFinished
from foundry.core.checkup import FAIL, OK, SKIP, WARN, Case, Found
from foundry.core.conversation import (Message, ModelTurn, Role, StopReason, ToolResultBlock,
                                       ToolSchema, TurnRequest)
from foundry.core.errors import FoundryError, ProtocolError, StreamDropped
from foundry.core.httptap import Exchange, TapClient

ADD = ToolSchema(
    name="add",
    description="Add two whole numbers and return their sum.",
    parameters={"type": "object",
                "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"], "additionalProperties": False})
SAY_READY = "Reply with exactly one word: ready"
COUNT = "Write the whole numbers from 1 to 40, separated by single spaces, and nothing else."
USE_ADD = "Use the add tool to add 2 and 3. Call the tool; do not answer in text."
LONG = "Write the whole numbers from 1 to 300, separated by single spaces, and nothing else."
CEILING = 16
NO_SUCH_MODEL = "foundry-checkup-no-such-model"
# A stream is held back when its events came within this share of the wait.
HELD_SHARE = 0.1
HELD_AFTER_S = 1.0
# The first case's failures that every later case would meet too.
_STOPPING = ("401", "403", "407", "404", "N", "T")


@dataclass(slots=True)
class Asked:
    turn: ModelTurn | None
    error: Exception | None
    exchange: Exchange | None
    seconds: float
    deltas: int = 0


@dataclass(slots=True)
class Spent:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    captures: list[tuple[str, Exchange]] = field(default_factory=list)


def ask(backend, tap: TapClient, request: TurnRequest, *, stream: bool, spent: Spent,
        name: str) -> Asked:
    """One request through the adapter, as a session sends it: the turn, or
    what went wrong, and what came back as it came."""
    backend.stream = stream
    before = len(tap.exchanges)
    began = time.monotonic()
    turn, error, deltas = None, None, 0
    try:
        for event in backend.stream_turn(request):
            if isinstance(event, TextDelta):
                deltas += 1
            elif isinstance(event, TurnFinished):
                turn = event.turn
        if turn is None:
            raise ProtocolError("the answer ended without a finished turn")
    except Exception as exc:      # an adapter's own fault is reported, not raised
        turn, error = None, exc
    exchange = tap.exchanges[-1] if len(tap.exchanges) > before else None
    # An endpoint that does not stream is asked again, once (D-060).
    spent.requests += len(tap.exchanges) - before
    if turn is not None:
        spent.input_tokens += turn.usage.input_tokens
        spent.output_tokens += turn.usage.output_tokens
    if exchange is not None:
        spent.captures.append((name, exchange))
    return Asked(turn, error, exchange, time.monotonic() - began, deltas)


def _quoted(exchange: Exchange | None, limit: int = 160) -> str:
    if exchange is None:
        return ""
    return " ".join(exchange.body.decode("utf-8", "replace").split())[:limit]


def failure(stem: str, asked: Asked) -> tuple[str, str, str]:
    """The code, the line and what to do for a request that failed."""
    exc = asked.error
    status = asked.exchange.status if asked.exchange is not None else None
    quoted = _quoted(asked.exchange)
    if status == 401:
        return (f"{stem}401", "the endpoint does not accept the credential (HTTP 401)",
                "check that it is the key or token this endpoint issues, and still valid")
    if status == 403:
        return (f"{stem}403", "the endpoint refuses the credential here (HTTP 403)",
                "ask for access to this endpoint and model")
    if status == 407:
        return (f"{stem}407", "the proxy wants a sign-in (HTTP 407)",
                "see the network level's proxy line")
    if status == 404:
        return (f"{stem}404", f"the endpoint has no such address (HTTP 404): {quoted}",
                "set protocol to the one the gateway speaks, and base_url to the address "
                "its documentation gives")
    if status == 429:
        return (f"{stem}429", f"rate limited (HTTP 429): {quoted}",
                "the gateway limits how often it is asked; run the checkup again later")
    if status is not None and status >= 500:
        return (f"{stem}{status}", f"the endpoint failed (HTTP {status}): {quoted}",
                "a fault on the endpoint's side, or a proxy's before it; try again, and say "
                "if it repeats")
    if status is not None and status >= 400:
        return (f"{stem}{status}", f"the endpoint refused the request (HTTP {status}): {quoted}",
                "its answer names what it did not take: a field, the model, or a size")
    if status is not None and not isinstance(exc, FoundryError):
        return (f"{stem}X", f"Foundry failed reading the answer: {type(exc).__name__}: {exc}",
                "the answer as it came is kept beside the report; say what this line says")
    if status is not None:
        return (f"{stem}P", f"the answer could not be read: {exc}",
                "the answer as it came is kept beside the report; say what this line says")
    if isinstance(exc, StreamDropped):
        return (f"{stem}D", f"the stream was cut: {exc}",
                "a proxy that ends long responses does this; try again, and say if it repeats")
    if "timed out" in str(exc) or "stalled" in str(exc):
        return (f"{stem}T", f"no answer in time: {exc}",
                "the endpoint took the request and did not answer")
    return (f"{stem}N", f"the request failed: {exc}",
            "the network level reached the endpoint; try again, and say if it repeats")


def _streamed(exchange: Exchange | None) -> bool:
    return exchange is not None and "event-stream" in exchange.headers.get("content-type", "")


def text_case(name: str, stem: str, asked: Asked, *, stream: bool) -> Case:
    if asked.error is not None:
        return Case("P", name, FAIL, *_said(failure(stem, asked)))
    turn = asked.turn
    if turn.stop_reason is StopReason.REFUSAL:
        return Case("P", name, FAIL, f"the model refused: {turn.text[:80]!r}", f"{stem}R",
                    "a content filter on the gateway may have answered")
    if not turn.text.strip():
        return Case("P", name, FAIL, "the answer had no text", f"{stem}E",
                    "the endpoint answered in a shape with no text Foundry finds")
    words = f"{turn.text.strip()[:40]!r} in {asked.seconds:.1f} s"
    if stream and not _streamed(asked.exchange):
        return Case("P", name, WARN, f"asked for a stream, the endpoint answered once: {words}",
                    "NOSSE", "Foundry reads it, but nothing shows until each answer is whole")
    how = f"streamed in {asked.deltas} piece(s)" if stream else "answered once"
    return Case("P", name, OK, f"{how}: {words}")


def pace_case(asked: Asked) -> Case:
    """Whether the stream came as it was written, or all at once at the end."""
    if asked.error is not None:
        return Case("P", "stream", FAIL, *_said(failure("STR", asked)))
    if not _streamed(asked.exchange):
        return Case("P", "stream", SKIP, "the endpoint does not stream")
    data = [moment for moment, piece in asked.exchange.pieces if piece.startswith(b"data:")]
    if len(data) < 5:
        return Case("P", "stream", OK, f"{len(data)} event(s): too few to tell its pace")
    spread = data[-1] - data[0]
    waited = data[-1] - asked.exchange.started
    if waited > HELD_AFTER_S and spread < HELD_SHARE * waited:
        return Case("P", "stream", WARN, f"all {len(data)} events came within "
                    f"{spread * 1000:.0f} ms, {waited:.1f} s after the request: something on "
                    "the way holds the stream back", "BUF",
                    "a proxy that buffers responses: each answer shows only when it is "
                    "whole, and a long one may meet the proxy's time limit")
    return Case("P", "stream", OK, f"{len(data)} events over {spread:.1f} s, as written")


def tool_case(name: str, stem: str, asked: Asked) -> Case:
    if asked.error is not None:
        return Case("P", name, FAIL, *_said(failure(stem, asked)))
    turn = asked.turn
    if not turn.tool_calls:
        return Case("P", name, FAIL, f"answered in text, with no call: {turn.text.strip()[:60]!r}",
                    f"{stem}0", "the endpoint may drop the tools from a request, or the model "
                    "not use them: an agent cannot work through it")
    call = turn.tool_calls[0]
    if call.name != ADD.name:
        return Case("P", name, FAIL, f"called {call.name!r}, which it was not given",
                    f"{stem}N", "the tool's name is changed on the way")
    try:
        arguments = json.loads(call.arguments)
    except ValueError:
        arguments = None
    if not isinstance(arguments, dict):
        return Case("P", name, FAIL, f"the call's arguments are not an object: "
                    f"{call.arguments[:80]!r}", f"{stem}J",
                    "the arguments are changed on the way, or cut")
    if arguments != {"a": 2, "b": 3}:
        return Case("P", name, WARN, f"called add with {call.arguments[:80]}", f"{stem}A",
                    "the model chose other numbers; the call itself came through")
    return Case("P", name, OK, f"called add({call.arguments}) in {asked.seconds:.1f} s, "
                f"stop reason {turn.stop_reason.value}")


def continue_case(asked: Asked) -> Case:
    if asked.error is not None:
        return Case("P", "continue", FAIL, *_said(failure("CON", asked)))
    text = asked.turn.text.strip()
    if "5" not in text:
        return Case("P", "continue", WARN, f"went on, without the result: {text[:60]!r}", "CONR",
                    "the tool's result may not reach the model")
    return Case("P", "continue", OK, f"went on with the result: {text[:60]!r}")


def cut_case(asked: Asked) -> Case:
    if asked.error is not None:
        code, said, fix = failure("CUT", asked)
        status = asked.exchange.status if asked.exchange is not None else None
        if status is not None and 400 <= status < 500:
            fix = (f"a session sends a ceiling only when one is configured; this endpoint "
                   f"refuses one of {CEILING} tokens")
        return Case("P", "cut", WARN, said, code, fix)
    turn = asked.turn
    out = turn.usage.output_tokens
    if turn.stop_reason is StopReason.MAX_TOKENS:
        return Case("P", "cut", OK, f"cut at the ceiling of {CEILING} tokens, and said so"
                    + (f" ({out} out)" if out else ""))
    if out > 2 * CEILING:
        return Case("P", "cut", WARN, f"{out} tokens came for a ceiling of {CEILING}: the "
                    "ceiling is ignored", "CUTI",
                    "max_output_tokens does not hold on this endpoint")
    return Case("P", "cut", WARN, f"the reply stopped at the ceiling and was said to be "
                f"{turn.stop_reason.value}", "CUTU",
                "a session cannot tell a cut reply from a whole one here")


def wrong_model_case(asked: Asked) -> Case:
    status = asked.exchange.status if asked.exchange is not None else None
    if asked.error is not None and status is not None and 400 <= status < 500:
        return Case("P", "badmodel", OK, f"refused, HTTP {status}: {_quoted(asked.exchange, 80)}")
    if asked.error is not None:
        code, said, _ = failure("BAD", asked)
        return Case("P", "badmodel", WARN, said, code,
                    "a wrong model name is not refused plainly here")
    named = asked.turn.model or "a model it did not name"
    return Case("P", "badmodel", WARN, f"{NO_SUCH_MODEL} was answered, as {named}", "BAD0",
                "the gateway gives a model it does not know to one of its own: check the "
                "model name in config.toml against its list")


def _said(found: tuple[str, str, str]) -> tuple[str, str, str]:
    """failure()'s code, line and fix, in Case's order."""
    code, said, fix = found
    return said, code, fix


def protocol_cases(backend, tap: TapClient, model: str, *, said=lambda case: None,
                   spent: Spent | None = None) -> tuple[list[Case], Spent]:
    """Every case of the protocol level, each told to ``said`` as it is
    found, and what they spent and received."""
    spent = spent if spent is not None else Spent()
    backend.max_retries = 1
    cases = Found(said)
    user = Message.text(Role.USER, SAY_READY)

    first = ask(backend, tap, TurnRequest((user,), (), model), stream=True, spent=spent,
                name="text-stream")
    cases.append(text_case("text", "TXS", first, stream=True))
    if cases[-1].status == FAIL and cases[-1].code[3:] in _STOPPING:
        for name in ("text-once", "stream", "tool", "tool-once", "continue", "cut", "badmodel"):
            cases.append(Case("P", name, SKIP, "not tried: the first request failed"))
        return cases, spent

    once = ask(backend, tap, TurnRequest((user,), (), model), stream=False, spent=spent,
               name="text-once")
    cases.append(text_case("text-once", "TXO", once, stream=False))
    pace = ask(backend, tap, TurnRequest((Message.text(Role.USER, COUNT),), (), model),
               stream=True, spent=spent, name="stream")
    cases.append(pace_case(pace))

    asking = Message.text(Role.USER, USE_ADD)
    tool_turn = None
    for name, stem, stream in (("tool", "TCS", True), ("tool-once", "TCO", False)):
        asked = ask(backend, tap, TurnRequest((asking,), (ADD,), model), stream=stream,
                    spent=spent, name=name)
        case = tool_case(name, stem, asked)
        cases.append(case)
        if case.status != FAIL and tool_turn is None:
            tool_turn = asked.turn
    if tool_turn is None:
        cases.append(Case("P", "continue", SKIP, "not tried: no call came to continue"))
    else:
        call = tool_turn.tool_calls[0]
        result = Message(role=Role.TOOL, blocks=(ToolResultBlock(call.call_id, "5"),))
        asked = ask(backend, tap, TurnRequest((asking, tool_turn.as_message(), result), (ADD,),
                                              model), stream=True, spent=spent, name="continue")
        cases.append(continue_case(asked))

    cut = ask(backend, tap, TurnRequest((Message.text(Role.USER, LONG),), (), model,
                                        max_output_tokens=CEILING),
              stream=True, spent=spent, name="cut")
    cases.append(cut_case(cut))
    wrong = ask(backend, tap, TurnRequest((user,), (), NO_SUCH_MODEL), stream=False,
                spent=spent, name="badmodel")
    cases.append(wrong_model_case(wrong))
    return cases, spent
