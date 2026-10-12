"""The agentic loop over the Claude API.

The Claude client is injected (anything with ``.beta.messages.create(**kwargs)``)
so tests never need the network or an API key. Server tools (web_search,
web_fetch) run on Anthropic's side; the loop executes only the client tools
(compute_metrics, get_company_facts, submit_memo) and passes every other block
through untouched. The conversation is append-only.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .edgar import COMPANY_FACTS_TOOL, EdgarClient, run_company_facts_tool
from .memo import SUBMIT_MEMO_TOOL, render_markdown
from .metrics import METRICS_TOOL, run_metrics_tool

DEFAULT_MODEL = "claude-opus-5-5"
BETAS = ["server-side-fallback-2026-07-01"]
MAX_TOKENS = 16000

_PROMPT_INTRO = """\
You are an equity analyst. You write for a portfolio manager who holds the stock \
or is considering it. Your job: read the company's latest quarterly earnings \
release and write a memo saying what happened, what it means for the position, \
and what to watch.
"""

_STEP_FIND = (
    "Find the most recent quarterly earnings release for the ticker on the "
    "company's investor-relations site or on a newswire. Do not try sec.gov: it "
    "cannot be fetched."
)
_STEP_READ = "Read the release with web_fetch."
_STEP_EDGAR = (
    "Call get_company_facts for the ticker to cross-check the figures. If it "
    "returns an error, carry on with the release."
)
_STEP_METRICS = (
    "Call compute_metrics with the raw figures for the current quarter and, when "
    "the release gives them, the same quarter a year earlier and the preceding "
    "quarter. Pass figures in the reporting currency's units as reported, and null "
    "for any figure the release does not give."
)
_STEP_SUBMIT = "Call submit_memo with the finished memo."

_PROMPT_RULES = """\
Rules:
- Every growth rate, margin and free-cash-flow number in the memo comes from \
compute_metrics output. Do not calculate them yourself.
- If the sources do not give a figure, leave it out. Never estimate.
- List every source you read in the memo's sources.
- Be plain and direct. The reader is busy.
"""


def build_system_prompt(edgar_available: bool) -> str:
    """The system prompt; the EDGAR step appears only when EDGAR is available."""
    steps = [_STEP_FIND, _STEP_READ]
    if edgar_available:
        steps.append(_STEP_EDGAR)
    steps += [_STEP_METRICS, _STEP_SUBMIT]
    numbered = "\n".join(f"{i}. {step}" for i, step in enumerate(steps, start=1))
    return f"{_PROMPT_INTRO}\nProcess:\n{numbered}\n\n{_PROMPT_RULES}"

WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 5,
}
WEB_FETCH_TOOL: dict[str, Any] = {
    "type": "web_fetch_20260209",
    "name": "web_fetch",
    "max_uses": 6,
}


def build_tools(edgar_available: bool) -> list[dict[str, Any]]:
    """The tool list; get_company_facts is offered only when EDGAR is available."""
    tools: list[dict[str, Any]] = [WEB_SEARCH_TOOL, WEB_FETCH_TOOL, METRICS_TOOL]
    if edgar_available:
        tools.append(COMPANY_FACTS_TOOL)
    tools.append(SUBMIT_MEMO_TOOL)
    return tools


# The with-EDGAR configuration, kept for callers that import these names.
SYSTEM_PROMPT = build_system_prompt(edgar_available=True)
TOOLS: list[dict[str, Any]] = build_tools(edgar_available=True)

REMINDER = (
    "You ended your turn without calling submit_memo. Finish the work and call "
    "submit_memo with the memo now."
)

EventCallback = Callable[[str, str], None]


class AgentError(RuntimeError):
    """The run could not produce a memo."""


class AgentRefused(AgentError):
    """The model declined the request."""

    def __init__(self, category: str | None, explanation: str | None) -> None:
        self.category = category
        self.explanation = explanation
        message = "the model declined the request"
        if category:
            message += f" (category: {category})"
        if explanation:
            message += f": {explanation}"
        super().__init__(message)


@dataclass
class AgentResult:
    memo: dict[str, Any]
    markdown: str
    requests: int
    input_tokens: int
    output_tokens: int


def _short(value: Any, limit: int = 200) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _describe_server_tool(block: Any) -> str:
    name = getattr(block, "name", "?")
    tool_input = getattr(block, "input", None)
    if isinstance(tool_input, dict):
        if name == "web_search" and "query" in tool_input:
            return f"web_search: {tool_input['query']}"
        if name == "web_fetch" and "url" in tool_input:
            return f"web_fetch: {tool_input['url']}"
    return f"{name}: {_short(tool_input)}"


def _run_client_tool(
    block: Any, edgar: EdgarClient | None
) -> tuple[str, bool]:
    """Execute one client tool_use block. Returns (content, is_error)."""
    name = getattr(block, "name", None)
    tool_input = getattr(block, "input", None)
    try:
        if name == "compute_metrics":
            result = run_metrics_tool(tool_input)
        elif name == "get_company_facts":
            if edgar is None:
                return (
                    "EDGAR is not configured for this run; use the web tools on "
                    "the company's investor-relations page or a newswire instead.",
                    True,
                )
            result = run_company_facts_tool(edgar, tool_input)
        else:
            return f"unknown tool: {name!r}", True
    except Exception as exc:  # any tool failure goes back to the model
        return f"{type(exc).__name__}: {exc}", True
    return json.dumps(result), False


def run_agent(
    ticker: str,
    *,
    client: Any,
    edgar: EdgarClient | None = None,
    model: str = DEFAULT_MODEL,
    effort: str = "high",
    max_requests: int = 20,
    max_continuations: int = 5,
    on_event: EventCallback | None = None,
) -> AgentResult:
    """Run the agent for ``ticker`` until it submits a memo.

    Raises AgentRefused if the model declines and AgentError for any other
    way the run fails to produce a memo. API errors from the client propagate.
    """
    if not isinstance(ticker, str) or not ticker.strip():
        raise ValueError("ticker must be a non-empty string")
    ticker = ticker.strip().upper()

    def emit(kind: str, detail: str) -> None:
        if on_event is not None:
            on_event(kind, detail)

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": f"Write the earnings memo for {ticker}.",
        }
    ]
    system_prompt = build_system_prompt(edgar is not None)
    tools = build_tools(edgar is not None)
    requests = 0
    input_tokens = 0
    output_tokens = 0
    continuations = 0
    reminded = False

    while True:
        if requests >= max_requests:
            raise AgentError(
                f"no memo after {requests} requests (max_requests={max_requests})"
            )
        requests += 1
        emit("request", f"request {requests}")
        response = client.beta.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            betas=BETAS,
            fallbacks="default",
            output_config={"effort": effort},
            system=system_prompt,
            tools=tools,
            messages=messages,
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            input_tokens += getattr(usage, "input_tokens", 0) or 0
            output_tokens += getattr(usage, "output_tokens", 0) or 0

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise AgentRefused(
                getattr(details, "category", None) if details is not None else None,
                getattr(details, "explanation", None) if details is not None else None,
            )

        content = response.content
        messages.append({"role": "assistant", "content": content})

        if stop_reason == "max_tokens":
            raise AgentError("the response was cut off (stop_reason max_tokens)")

        client_calls: list[Any] = []
        for block in content:
            block_type = getattr(block, "type", None)
            if block_type == "server_tool_use":
                emit("server_tool_use", _describe_server_tool(block))
            elif block_type == "tool_use":
                client_calls.append(block)

        for block in client_calls:
            if block.name == "submit_memo":
                emit("tool", "submit_memo")
                memo = block.input
                if not isinstance(memo, dict):
                    raise AgentError(f"submit_memo input is not an object: {memo!r}")
                return AgentResult(
                    memo=memo,
                    markdown=render_markdown(memo),
                    requests=requests,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

        if stop_reason == "pause_turn":
            continuations += 1
            if continuations > max_continuations:
                raise AgentError(
                    f"more than {max_continuations} pause_turn continuations in a row"
                )
            emit("pause_turn", f"continuation {continuations}")
            continue
        continuations = 0

        if stop_reason == "tool_use":
            if not client_calls:
                raise AgentError("stop_reason tool_use without a client tool_use block")
            results: list[dict[str, Any]] = []
            for block in client_calls:
                emit("tool", f"{block.name}: {_short(block.input)}")
                result_content, is_error = _run_client_tool(block, edgar)
                result: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_content,
                }
                if is_error:
                    result["is_error"] = True
                    emit("tool_error", f"{block.name}: {_short(result_content)}")
                results.append(result)
            messages.append({"role": "user", "content": results})
            continue

        if stop_reason == "end_turn":
            if reminded:
                raise AgentError("the model ended its turn twice without calling submit_memo")
            reminded = True
            emit("reminder", "end_turn without submit_memo")
            messages.append({"role": "user", "content": REMINDER})
            continue

        raise AgentError(f"unexpected stop_reason: {stop_reason!r}")