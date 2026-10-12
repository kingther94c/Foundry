# earnings_pm

An agent that reads a company's latest quarterly earnings release and writes the
memo a portfolio manager wants: what happened, what it means for the position,
and what to watch. It runs on the Claude API.

## Setup

You need Python 3.12 or newer.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
python -m earnings_pm MSFT
python -m earnings_pm MSFT --effort medium --max-requests 15 --out memos
```

```text
python -m earnings_pm TICKER [--model M] [--effort low|medium|high|xhigh|max]
                             [--max-requests N] [--out DIR]
```

Installing the package also adds an `earnings-pm` command that takes the same
arguments.

- The markdown memo is printed to **stdout**. Progress goes to **stderr**, one
  line per event (each request, web search, page fetch and tool call), so
  `python -m earnings_pm MSFT > msft.md` gives you just the memo.
- `--out DIR` also writes `DIR/TICKER.md` and `DIR/TICKER.json`. The JSON file
  holds the structured memo plus the model, effort, request count, token totals
  (`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
  `cache_creation_input_tokens`), a per-request `usage` list with the same four
  counts, and `calculations`: every `compute_metrics` call in order, as
  `{"tool_use_id", "input", "output"}`, or `{"tool_use_id", "input", "error"}`
  when the call failed.
- Defaults: `--model claude-opus-5-5`, `--effort high`, `--max-requests 20`.

Exit codes:

| Code | Meaning |
| ---- | ------- |
| 0 | memo written |
| 1 | the memo was produced but the `--out` files could not be written |
| 2 | bad arguments |
| 3 | the model declined the request |
| 4 | the agent could not produce a memo (cut-off response, no `submit_memo`, request limit reached) |
| 5 | Claude API error (authentication, rate limit, bad request, connection) |

## Environment variables

| Variable | Required | Purpose |
| -------- | -------- | ------- |
| `ANTHROPIC_API_KEY` | yes | Claude API key. An authentication failure exits with code 5 and names this variable. |
| `EARNINGS_PM_USER_AGENT` | no | Turns on the SEC EDGAR cross-check. SEC requires a contact User-Agent, e.g. `"Jane Doe jane@example.com"`. When it is not set, EDGAR is left out entirely: the model is not offered the tool and not told about it. |

## How data flows

1. **Finding and reading the release: Anthropic's server-side web tools.** The
   model uses `web_search` and `web_fetch`, which run on Anthropic's side, to
   find the latest earnings release on the company's investor-relations site or
   a newswire and read it.
2. **Optional EDGAR cross-check: `get_company_facts`.** Offered only when
   `EARNINGS_PM_USER_AGENT` is set. It runs locally (`earnings_pm/edgar.py`),
   reads SEC's companyfacts JSON, and returns single-quarter values for revenue,
   gross profit, operating income, net income, diluted EPS, operating cash flow
   and capex. Nothing is derived; a metric SEC files only year-to-date is
   reported as missing.
3. **Every number: local `compute_metrics`.** The model passes the raw figures
   it read. Growth rates, margins, margin changes and free cash flow are
   computed in `earnings_pm/metrics.py`, so they can be reproduced. The model
   is told not to do this arithmetic itself. A missing input or a division by
   zero gives `null`, never 0. Every call's input and output (or error) is
   recorded and saved under `calculations` in the `--out` JSON, so any number
   in the memo can be traced to the figures it came from and recomputed with
   `earnings_pm.metrics.run_metrics_tool(input)`.
4. **The memo: `submit_memo`.** The model hands back the memo as structured
   input checked against a strict JSON schema (`earnings_pm/memo.py`), and that
   ends the run. If the model stops without calling it, it gets one reminder; a
   second miss is an error. Each run is capped at `--max-requests` requests.

## Known limits

- **`web_fetch` refuses sec.gov URLs** (`url_not_allowed`). The model therefore
  reads the release from investor-relations pages and newswires, never from SEC
  filings. SEC data comes only through the optional EDGAR tool, which fetches
  locally.
- **EDGAR needs a contact User-Agent.** SEC rejects anonymous requests. Without
  `EARNINGS_PM_USER_AGENT` there is no cross-check, and the memo rests on the
  release alone.
- **A run costs real API tokens.** Each run makes several requests to a large
  model with high effort, and web search and fetch results count as input
  tokens. Every request re-sends the whole conversation so far, so automatic
  prompt caching is on (`cache_control={"type": "ephemeral"}`): each request
  writes its prefix to the cache (`cache_creation_input_tokens`) and the next
  one reads it back (`cache_read_input_tokens`) at a tenth of the input price.
  The system prompt and tool list are fixed for the whole run and the history
  is append-only, so the prefix stays cacheable. `input_tokens` counts only the
  uncached part. The totals, including both cache counts, are printed on the
  stderr `[done]` line, and the `--out` JSON also has them per request under
  `usage`. Use `--effort` and `--max-requests` to bound the cost.
- Money is reported in the release's currency and units as reported. Nothing
  is converted.
- The memo is a starting point for a human reader, not investment advice.
  Check figures against the cited sources.

## Running the tests

```powershell
python -m pytest -q
```

The tests run offline. The Claude client and the HTTP fetcher are injected, so
no network access and no API key are needed. All fixtures are synthetic.

## Layout

```text
earnings_pm/metrics.py   pure arithmetic: growth, margins, free cash flow
earnings_pm/memo.py      the memo schema (submit_memo tool) and markdown rendering
earnings_pm/edgar.py     SEC EDGAR client (companyfacts JSON)
earnings_pm/agent.py     the agentic loop over the Claude API
earnings_pm/__main__.py  the CLI
tests/                   offline tests
```
