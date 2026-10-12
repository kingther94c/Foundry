# earnings_pm

A small agent that reads a company's latest earnings report and writes the memo
a portfolio manager wants: what happened, what it means for the position, and
what to watch. It is driven by the Claude API: the model finds and reads the
report with Anthropic's server-side web tools, does every calculation through a
local tool so the numbers are reproducible, and hands back a structured memo.

This project lives in `examples/earnings_pm/` of the Foundry repository and is
built with Foundry as a dogfooding exercise.

## Layout

```text
earnings_pm/metrics.py   pure arithmetic: growth, margins, free cash flow
earnings_pm/memo.py      the Memo schema (JSON schema for the submit_memo tool) + markdown rendering
earnings_pm/edgar.py     SEC EDGAR client (companyfacts JSON) with an injectable fetcher
earnings_pm/agent.py     the agentic loop over the Claude API
earnings_pm/__main__.py  CLI: python -m earnings_pm TICKER
tests/                   offline tests only
```

## Conventions

- Python 3.12, standard library plus `anthropic` (>=1.13,<2). No other runtime
  dependencies.
- Tests never touch the network and never need an API key. The Claude client and
  the HTTP fetcher are injected, so tests pass fakes. Test fixtures are small,
  hand-written, and say in a comment that they are synthetic.
- Every `open()` / `read_text()` / `write_text()` passes `encoding="utf-8"`;
  `EncodingWarning` is an error under pytest.
- Money is in the reporting currency's units as reported (e.g. USD, not
  millions). Ratios are fractions (0.25), rendered as percentages only when
  printed.
- A division by zero or a missing input yields `None` for that metric, never an
  exception and never 0.
- Run the tests with `python -m pytest -q` from this directory.

## The Claude API, as this project uses it

These are the exact shapes to use; do not substitute patterns from memory.

```python
import anthropic

client = anthropic.Anthropic()            # reads ANTHROPIC_API_KEY
response = client.beta.messages.create(
    model="claude-opus-5-5",
    max_tokens=16000,
    betas=["server-side-fallback-2026-07-01"],
    fallbacks="default",                   # re-runs a safety-declined request on another model
    output_config={"effort": "high"},      # low | medium | high | xhigh | max
    system=SYSTEM_PROMPT,
    tools=TOOLS,
    messages=messages,
)
```

- Do not send `thinking`, `temperature`, `top_p` or `top_k`: on claude-opus-5-5
  thinking is always on (adaptive) and sampling parameters return a 400.
- Do not force a tool: `tool_choice` of type `any` or `tool` returns a 400 on
  this model. Leave it out (auto) and steer from the system prompt.
- Server tools are declared by type and run on Anthropic's side; there is no
  local function for them:
  `{"type": "web_search_20260209", "name": "web_search"}` and
  `{"type": "web_fetch_20260209", "name": "web_fetch"}`.
  Do not also declare `code_execution`. These versions filter results by
  running code server-side, so a response can also carry `server_tool_use`
  blocks named `code_execution` and `code_execution_tool_result` blocks. The
  loop must pass every block type it does not handle through untouched.
- Measured on 2026-10-12: `web_fetch` refuses sec.gov URLs (`url_not_allowed`)
  but reads company investor-relations pages and newswires. The system prompt
  should send the model to those for the earnings release.
- A client tool is `{"name", "description", "input_schema"}`, plus
  `"strict": True` where the schema guarantee matters. With `strict`, every
  object in the schema, nested ones included, must set
  `"additionalProperties": False`, and these keywords are not supported:
  `minimum`, `maximum`, `multipleOf`, `minLength`, `maxLength`.
- Measured on 2026-10-12: across all strict tools in one request, at most 16
  parameters may be union-typed (a type array such as `["number", "null"]`, or
  `anyOf`); more is a 400 ("Schemas contains too many parameters with union
  types"). So `strict` is for `submit_memo`, whose shape is the product; a tool
  whose input the code validates itself (compute_metrics) is not strict, and
  its optional values are optional properties rather than nullable ones.
  (Project convention for the memo: list every property in `required` and make
  a value that may be absent nullable, so a memo is never silently missing a
  field.)
- `response.content` is a list of typed blocks (`text`, `thinking`,
  `server_tool_use`, `web_search_tool_result`, `web_fetch_tool_result`,
  `tool_use`, ...). Read `block.type` before touching other attributes.
- The conversation is append-only. Append each response as
  `{"role": "assistant", "content": response.content}` unchanged, and never edit
  or drop an earlier message: thinking blocks are bound to the history they were
  produced in.
- `response.stop_reason`:
  - `"tool_use"`: run every `tool_use` block, then send all results back in ONE
    user message: `{"type": "tool_result", "tool_use_id": block.id, "content": str}`,
    with `"is_error": True` for a failed tool.
  - `"pause_turn"`: the server-side loop paused. Append the assistant content and
    send the request again with no new user message; cap these continuations.
  - `"end_turn"`: the model stopped talking.
  - `"max_tokens"`: the answer was cut off; treat as an error.
  - `"refusal"`: declined; `response.stop_details` (may be None) has `category`
    and `explanation`. Check this before reading content.
- Usage: `response.usage.input_tokens`, `response.usage.output_tokens`.
- Errors to catch, most specific first: `anthropic.AuthenticationError`,
  `anthropic.RateLimitError`, `anthropic.BadRequestError`,
  `anthropic.APIStatusError`, `anthropic.APIConnectionError`. The SDK already
  retries 429 and 5xx twice.

## The agent's contract

Client tools (local functions the loop executes):

- `compute_metrics(periods)`: the arithmetic in `metrics.py`. The model passes
  raw figures it read from the report; every growth rate, margin and free-cash-
  flow figure in the memo must come from this tool's output.
- `get_company_facts(ticker)`: EDGAR lookup through `edgar.py`. When EDGAR is
  unreachable it returns an error result, and the model falls back to the web
  tools.
- `submit_memo(memo)`: the memo, validated by its strict schema. Calling it ends
  the run.

The loop ends when `submit_memo` is called. If the model ends its turn without
calling it, it is reminded once; a second miss is an error. The run is also
bounded by a maximum number of requests.
