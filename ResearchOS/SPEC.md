# ResearchOS -- specification (v1: earnings)

ResearchOS trains an analyst agent to read a company's quarterly earnings
documents (the results release, the call transcript, what the market expected)
and judge how the stock will react -- without knowing which company it is.

One training step on one case:

1. **Mask.** Hide the company: deterministic masking (company names, money
   scaled by a secret factor, years shifted), then a masker agent proposes more
   redactions and a guesser agent tries to name the company; repeat until the
   guesser fails or the round limit is hit.
2. **Analyze.** The analyst reads the masked documents through several
   perspectives (growth, profitability, ...), each giving a probability that
   the stock beats the market on the first session that can react; the
   perspectives are combined with learned weights, and a judge gives its own
   probability.
3. **Seal.** The prediction is written to disk *before* the outcome is read.
4. **Reveal.** The outcome is read and every probability is scored.
5. **Reflect.** A reflector compares prediction and outcome and writes
   general lessons; perspective weights are updated from each perspective's
   loss. Lessons and weights form the playbook the next case is analyzed with.

Evaluation runs steps 1-4 on held-out cases with the playbook frozen.

Package: `researchos` (Python 3.12). Dependencies: the standard library,
`anthropic` (>=1.13,<2), and the local package `tools` (already installed;
see "Using tools" below). Every behaviour below is tested offline with fake
LLMs; nothing in the tests touches the network.

## Using `tools`

`tools` is installed. Use it rather than reimplementing:
`tools.textmask.redact(text, mapping, *, case_sensitive=False)` (whole-word,
longest key first, case-insensitive by default, never rescans replacements),
`tools.textmask.find_terms(text, terms, *, case_sensitive=False)`,
`tools.textmask.scale_money(text, factor)`, `tools.textmask.shift_years(text,
offset)`, `tools.stats.brier_score(probs, outcomes)`, `tools.stats.log_loss(probs,
outcomes)`, `tools.stats.directional_accuracy(predicted, actual)`,
`tools.stats.hedge_update(weights, losses, eta)`,
`tools.stats.combine_probabilities(probs, weights)`.

## researchos.cases

```python
@dataclass
class Document:
    kind: str            # "release" | "transcript" | "expectations"
    title: str
    text: str
    source: str = ""

@dataclass
class Outcome:
    abnormal_return_1d: float          # stock minus market, first reacting session
    stock_return_1d: float | None = None
    market_return_1d: float | None = None
    notes: str = ""
    sources: list[str] = field(default_factory=list)

@dataclass
class Case:
    case_id: str
    company: str
    ticker: str
    aliases: list[str]                 # other names that identify it (brands, CEO, ...)
    fiscal_period: str                 # e.g. "Q4 FY2026"
    event_date: str                    # ISO date of the first session that can react
    documents: list[Document]
```

A `Case` never contains its outcome. On disk, under a root directory:
`<root>/<case_id>/case.json` (the Case, as `dataclasses.asdict` JSON) and
`<root>/<case_id>/outcome.json` (the Outcome).

- `save_case(root, case, outcome) -> None`: writes both files (creating the
  directory), UTF-8, `indent=2`.
- `load_case(root, case_id) -> Case`: reads `case.json` only. It must never
  open `outcome.json`.
- `load_outcome(root, case_id) -> Outcome`.
- `list_cases(root) -> list[str]`: the case ids (directories containing a
  `case.json`), sorted.
- `identity_terms(case) -> list[str]`: `company`, `ticker`, then `aliases`,
  without empty strings or duplicates (case-insensitive), in that order.

## researchos.llm

The only module that talks to the Claude API. Every agent role calls an
object with this method:

```python
def json(self, system: str, prompt: str, schema: dict, *, prefix: str | None = None) -> dict
```

It sends one request and returns the parsed JSON the model produced. Tests
replace it with fakes, so every other module depends only on this method.

`class ClaudeJSON` implements it with an injected client (anything with
`.beta.messages.create(**kwargs)`), constructed as
`ClaudeJSON(client, model="claude-opus-5-5", effort="high", max_tokens=16000)`.
The request:

```python
client.beta.messages.create(
    model=model, max_tokens=max_tokens,
    betas=["server-side-fallback-2026-07-01"], fallbacks="default",
    output_config={"effort": effort,
                   "format": {"type": "json_schema", "schema": schema}},
    system=system,
    messages=[{"role": "user", "content": blocks}],
)
```

where `blocks` is `[{"type": "text", "text": prefix, "cache_control":
{"type": "ephemeral"}}, {"type": "text", "text": prompt}]` when `prefix` is
given (the shared part -- documents -- gets the cache breakpoint, so several
requests with the same prefix read it from cache), and `[{"type": "text",
"text": prompt}]` otherwise. Never send `thinking`, `temperature`, `top_p`,
`top_k` or `tool_choice`.

Response handling: `stop_reason == "refusal"` raises `LLMError` (message
includes `stop_details.category` when present; `stop_details` may be None);
`"max_tokens"` raises `LLMError`; otherwise the first block with
`type == "text"` is parsed as JSON (no text block or invalid JSON raises
`LLMError`). `ClaudeJSON` keeps running totals in `self.usage`, a dict with
`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
`cache_creation_input_tokens` (None counts as 0).

Schemas: every object sets `"additionalProperties": False` and lists all its
properties in `required`; no nullable or union types (the API limits them).

## researchos.masking

```python
@dataclass
class MaskConfig:
    max_rounds: int = 4
    factor_range: tuple[float, float] = (0.3, 3.0)
    year_offset_range: tuple[int, int] = (-9, -3)
    guess_top_k: int = 3
    give_up_confidence: float | None = None

@dataclass
class MaskedCase:
    case_id: str
    documents: list[Document]       # masked; same kinds and order as the case's
    status: str                     # "masked" or "identifiable"
    rounds: int                     # guesser rounds run
    mapping: dict[str, str]         # every redaction applied (term -> replacement)
    factor: float
    year_offset: int
    final_guesses: list[dict]       # the last guesser output's "guesses"
```

- `pre_mask(case, rng, config) -> tuple[list[Document], dict[str, str], float, int]`:
  draws `factor = rng.uniform(*config.factor_range)` and then
  `year_offset = rng.randint(*config.year_offset_range)`, in that order. The
  base mapping sends `case.company` and every alias to `"the Company"` and
  `case.ticker` to `"TICKER"`. Each document's text becomes
  `shift_years(scale_money(redact(text, mapping), factor), year_offset)`;
  titles are masked the same way; `source` becomes `""` (a URL identifies the
  company). Returns the masked documents, the mapping, the factor and the
  offset.
- `normalize_name(name) -> str`: lowercase; replace every character that is
  not a letter, digit or space with a space; drop the words "the", "inc",
  "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
  "plc", "holdings", "group"; collapse whitespace; strip.
- `identifying_guess(guesses, case, top_k) -> dict | None`: the first of the
  first `top_k` guesses that has a `company` whose `normalize_name` equals the
  `normalize_name` of `case.company` or of an alias (and is not empty), or a
  `ticker` equal to `case.ticker` ignoring case and surrounding spaces; `None`
  if there is none.
- `is_identified(guesses, case, top_k) -> bool`: `identifying_guess(guesses,
  case, top_k) is not None`.
- Masker role. `system` is `MASKER_SYSTEM`; the prompt holds the current
  masked documents and, after a failed round, the guesser's clues. Schema
  `MASKER_SCHEMA`: `{"redactions": [{"term": str, "replacement": str,
  "reason": str}]}`. The masker is told to redact anything that identifies
  the company or lets one look up the stock reaction: product and brand
  names, people, places, competitors named as such, URLs, distinctive figures
  that are public facts, and any remark about the share price or how the
  stock traded.
- Guesser role. `system` is `GUESSER_SYSTEM`; the prompt holds only the
  masked documents. Schema `GUESSER_SCHEMA`: `{"guesses": [{"company": str,
  "ticker": str, "confidence": number}], "clues": [str]}`.
- `mask_case(case, masker, guesser, rng, config=MaskConfig()) -> MaskedCase`:
  1. `pre_mask`. 2. Up to `config.max_rounds` rounds: call the masker
  (previous clues included from round 2 on), add each proposed redaction with
  a non-empty term to the mapping (an existing term keeps its first
  replacement), re-mask the *original* documents with the full mapping
  (redact, then scale_money, then shift_years), then call the guesser. If
  `is_identified` is False the status is `"masked"` and the loop ends;
  otherwise the guesser's clues go to the next masker round. After the last
  round the status is `"identifiable"`. Giving up early: when
  `config.give_up_confidence` is set and the identifying guess's
  `confidence` is at least that, the loop ends at once with status
  `"identifiable"`. A guesser that sure after a round of redaction is naming
  the company from what its business is, not from a stray name, and further
  rounds only cost tokens: in the first real run all fifteen large companies
  were still named at 0.97 or more after four rounds, and masking was the
  most expensive step. A confidence that is missing or not a number never
  gives up. 3. Guard, applied to the returned
  documents whatever the agents did: any identity term still found
  (`find_terms`) is redacted to `"the Company"` and added to the mapping.
  The guesser never receives the case's company, ticker or aliases in any
  form other than the masked text.
- `save_masked(root, masked)` writes `<root>/<case_id>/masked.json`
  (documents, status, rounds, final_guesses) and `<root>/<case_id>/mask_key.json`
  (mapping, factor, year_offset) separately; `load_masked(root, case_id)` reads
  both back into a `MaskedCase`. The analyst only ever receives documents.

## researchos.perspectives

```python
PERSPECTIVES = ["growth", "profitability", "guidance", "cash_flow",
                "management_tone", "expectations_gap", "risks"]

@dataclass
class View:
    stance: str          # "bullish" | "bearish" | "neutral"
    prob_up: float       # P(abnormal_return_1d > 0), clamped to [0.01, 0.99]
    conviction: str      # "low" | "medium" | "high"
    key_points: list[str]

@dataclass
class Analysis:
    case_id: str
    views: dict[str, View]       # one per perspective, in PERSPECTIVES order
    prob_weighted: float         # combine_probabilities(view probs, playbook weights)
    prob_judge: float            # the judge's, clamped to [0.01, 0.99]
    thesis: str
```

- `analyze(masked, playbook, analyst, judge) -> Analysis`: one analyst call
  per perspective, all with the same `prefix` (the masked documents rendered
  as text, kind and title as headings) so they share the cache. The prompt's
  first line is `Perspective: <name>`; the rest says what that perspective
  looks at and includes `render_for_prompt(playbook)`. Schema `VIEW_SCHEMA`
  (stance and conviction as enums). Then one judge call (`prefix` the same documents)
  whose prompt includes every view and the playbook; schema `JUDGE_SCHEMA`:
  `{"prob_up": number, "thesis": str}`. If `combine_probabilities` returns
  None, `prob_weighted` is the plain mean of the view probabilities.
- Nothing sent to the analyst or the judge may contain the outcome, the
  case's identity terms, or the mask key.

## researchos.playbook

```python
@dataclass
class Lesson:
    text: str
    applies_when: str
    perspective: str
    case_id: str

@dataclass
class Playbook:
    weights: dict[str, float]
    lessons: list[Lesson]
    history: list[dict]
```

- `Playbook.initial()`: uniform weights over `PERSPECTIVES`, no lessons, no
  history.
- `save_playbook(path, playbook)` / `load_playbook(path)`: JSON round trip.
- `update_weights(playbook, views, actual_up, eta=0.5) -> None`: each
  perspective's loss is `(view.prob_up - y) ** 2` with `y = 1.0 if actual_up
  else 0.0`; the weights become `hedge_update(weights, losses, eta)`.
- `add_lessons(playbook, lessons, forbidden_terms) -> int`: appends the
  lessons whose `text` and `applies_when` contain none of `forbidden_terms`
  (whole word, case-insensitive, via `find_terms`); returns how many were
  rejected. Lessons must stay general: a lesson naming the company would leak
  the answer into every later case.
- `render_for_prompt(playbook, max_lessons=20) -> str`: the current weights
  and the most recent `max_lessons` lessons, as plain text; a playbook with no
  lessons says so.

## researchos.reflect

- `reflect(case, masked, analysis, outcome, reflector) -> list[Lesson]`: one
  call after the reveal. The prompt holds the views, both probabilities, the
  outcome and the masked documents' text, and asks why the market moved the
  way it did, where the analysis went wrong or right, and for lessons that
  would generalize to other companies -- never naming this one. Schema
  `REFLECT_SCHEMA`: `{"why_market_moved": str, "where_we_were_wrong": str,
  "lessons": [{"lesson": str, "applies_when": str, "perspective": str}]}`.
  Returns `Lesson`s with this `case_id`; a perspective not in
  `PERSPECTIVES` becomes `"general"`.

## researchos.loop

```python
@dataclass
class Roles:
    masker: Any
    guesser: Any
    analyst: Any
    judge: Any
    reflector: Any
```

- `score(analysis, outcome) -> dict`: `y = 1 if outcome.abnormal_return_1d > 0
  else 0`; returns `{"actual_up": bool, "brier_weighted", "brier_judge",
  "brier_by_perspective": {name: ...}, "correct_weighted": (prob_weighted >
  0.5) == actual_up, "correct_judge": ...}` (Brier for one forecast is
  `(p - y) ** 2`).
- `train(root, case_ids, playbook_path, roles, seed=0, mask_config=MaskConfig(), knowledge_cutoff=None) -> list[dict]`:
  for each case in order, with `rng = random.Random(f"{seed}:{case_id}")`:
  mask (reuse `masked.json` if it exists, else `mask_case` and save), analyze
  with the current playbook, **write `<root>/<case_id>/prediction.json`
  (the analysis) before calling `load_outcome`**, score, `update_weights`,
  `reflect`, `add_lessons` with the case's identity terms as forbidden terms,
  append the score (plus `case_id`, `split: "train"`, and the number of
  lessons rejected) to `history`, save the playbook. Returns the scores.
  A case whose masking ends `"identifiable"` is skipped (recorded in history
  with `"skipped": "identifiable"`), because its analysis could rely on
  recognizing the company -- unless `knowledge_cutoff` (an ISO date string:
  the last date the models can know anything about) is given and the case's
  `event_date` is after it. Then the models cannot have seen how the stock
  reacted: recognizing the company gives them priors, not the answer. Large
  companies are rarely maskable (their segment structure and ratios name
  them), so this is what lets them be used. Such a case is analyzed, scored,
  and reflected on like any other. Every scored record carries
  `"identifiable": bool` (whether masking ended `"identifiable"`).
- `evaluate(root, case_ids, playbook_path, roles, seed=0, mask_config=MaskConfig(), knowledge_cutoff=None) -> dict`:
  the same masking, skipping (with the same `knowledge_cutoff` rule),
  analysis, sealing and scoring, but the playbook is loaded
  and **never written or changed**, and nothing is reflected. Returns
  `{"cases": [scores...], "n": count scored, "brier_weighted",
  "brier_judge", "brier_uniform", "brier_coin", "accuracy_weighted",
  "accuracy_judge", "n_identifiable"}` where `n_identifiable` counts the
  scored cases whose masking ended `"identifiable"`, and `brier_uniform` combines the views with equal
  weights and `brier_coin` forecasts 0.5 every time; means over scored cases,
  None when no case was scored.

## researchos.__main__

`python -m researchos train --root DIR --playbook FILE --ids ID[,ID...] [--seed N] [--knowledge-cutoff YYYY-MM-DD]`
and `python -m researchos evaluate ...` (same arguments; `--knowledge-cutoff`
is passed on as `knowledge_cutoff`, and a value that is not an ISO date is a
bad argument; `--mask-give-up CONF` runs with
`MaskConfig(give_up_confidence=CONF)`, and `CONF` must be a number with
`0 < CONF <= 1`, else it is a bad argument): build `Roles` from
`ClaudeJSON(anthropic.Anthropic())` (one instance per role; masker and
guesser at effort `"medium"`, the others at `"high"`), run, and print the
scores to stdout as JSON -- nothing else goes to stdout; messages go to
stderr. `main(argv=None, *, client_factory=None) -> int`: `client_factory` is
a zero-argument callable returning a client, `anthropic.Anthropic` when None.
Exit 0 on success, 2 on bad arguments, 1 on any `LLMError` or missing case
(with a message on stderr).

## researchos.sources (v2: building real cases)

Builds a `Case` and its `Outcome` from the web and from daily closes. The
documents are found and fetched through the Claude API's server-side web
tools; prices are not fetched here -- they arrive as JSON files. Tests use
fakes; nothing in them touches the network.

### Prices and the outcome

- `load_closes(path) -> list[tuple[date, float]]`: reads a JSON file holding
  `[["2026-07-16", 1234.5], ...]` (ISO date, close), returned sorted by date.
- `event_date_for(report_date: date, timing: str, trading_days) -> date`:
  `trading_days` is a sequence of dates (the dates of a closes series).
  `timing == "bmo"` (released before the open): the first trading day on or
  after `report_date`. `timing == "amc"` (after the close): the first trading
  day strictly after `report_date`. Raises ValueError for any other timing or
  when there is no such day.
- `outcome_from_closes(stock, market, event_date, *, notes="", sources=()) -> Outcome`:
  `stock_return_1d = tools.returns.event_return(stock, event_date)`, the same
  for `market`, `abnormal_return_1d = tools.returns.abnormal_return(stock_return_1d,
  market_return_1d)`. Raises ValueError if any of them is None.

### Fetching pages

```python
@dataclass
class FetchedPage:
    url: str
    title: str
    text: str
```

- `fetch_pages(client, request, *, model="claude-opus-5-5", max_searches=5,
  max_fetches=6, max_continuations=4) -> list[FetchedPage]`: asks the model to
  find and fetch what `request` describes. The call:

  ```python
  client.beta.messages.create(
      model=model, max_tokens=16000,
      betas=["server-side-fallback-2026-07-01"], fallbacks="default",
      output_config={"effort": "medium"},
      system=FETCH_SYSTEM,
      tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches},
             {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": max_fetches}],
      messages=messages,
  )
  ```

  `messages` starts as `[{"role": "user", "content": request}]`. While the
  response's `stop_reason` is `"pause_turn"` (the server paused a long turn),
  append `{"role": "assistant", "content": response.content}` and call again
  with the same arguments, at most `max_continuations` more times; then
  return what was collected. A `"refusal"` stop raises `LLMError` (from
  `researchos.llm`).

  Pages are collected from the `content` of every response, in order. A page
  is a block with `type == "web_fetch_tool_result"` whose `content.type ==
  "web_fetch_result"` and whose `content.content.source.type == "text"`; it
  gives `FetchedPage(url=content.url, title=content.content.title or "",
  text=content.content.source.data)`. Anything else (search results, errors,
  PDFs, which arrive as base64) is ignored. A URL already collected is not
  collected again. Blocks are objects with attributes (the SDK's models);
  tests use `types.SimpleNamespace`.

  `FETCH_SYSTEM` tells the model to fetch primary documents (the company's own
  release, a full call transcript) rather than news coverage of them, and not
  to summarize: the fetched text is the product.

### Cutting the document out of the page

A fetched page carries navigation, ads and related links around the document.

- `select_span(agent, page, kind) -> str | None`: one `agent.json(SPAN_SYSTEM,
  prompt, SPAN_SCHEMA)` call (an agent as in `researchos.llm`). The page's
  lines (`page.text.splitlines()`) are numbered from 1, and the prompt holds
  `kind`, the page title and the numbered page, each line written as
  `f"{n}: {line}"`. `SPAN_SCHEMA`: `{"found": bool, "first_line": int,
  "last_line": int}`. Returns `None` if `found` is false or the numbers are not
  `1 <= first_line <= last_line <= number of lines`; otherwise lines
  `first_line` to `last_line` inclusive, joined with `"\n"` and stripped
  (`None` if that is empty).

  Line numbers, not quoted words: asked to quote the first and last words of
  a document deep inside an 80,000-character page, a model paraphrases them,
  and asked for its end it tends to stop at the end of the highlights. A line
  number it reads off the page.

  `SPAN_SYSTEM` asks for the document proper: for a release, from its first
  line to the last line of its financial tables (releases run far past their
  highlights); for a transcript, from the opening to the end of the closing
  remarks, without any commentary about the share price. A page that only
  announces or links to the document (a stub pointing to a PDF, a news story
  about it) does not contain it.

### Expectations

- `expectations_document(agent, pages, company, fiscal_period) -> Document | None`:
  one `agent.json(EXPECT_SYSTEM, prompt, EXPECT_SCHEMA)` call whose prompt
  holds the pages' text. `EXPECT_SCHEMA`: `{"items": [{"metric": str,
  "consensus": str, "source": str}]}`. The agent reports only what analysts
  expected *before* the results (consensus EPS, revenue, segment or guidance
  figures), never the reported results or how the stock traded. `None` when
  there are no items; otherwise `Document("expectations", f"Consensus
  expectations for {fiscal_period}", text)` (its `source` left `""`) where
  `text` has one line per item, `f"- {metric}: {consensus} (source: {source})"`.

### Building a case

- `build_case(root, spec, *, fetch, agent, stock_closes, market_closes) -> Case`:
  `spec` is a `CaseSpec`:

  ```python
  @dataclass
  class CaseSpec:
      case_id: str
      company: str
      ticker: str
      aliases: list[str]
      fiscal_period: str       # "Q2 2026"
      report_date: str         # ISO date the results came out
      timing: str              # "bmo" | "amc"
  ```

  `fetch` is a callable `fetch(request) -> list[FetchedPage]` (in production
  `functools.partial(fetch_pages, client)`). It is called three times, with
  requests that each name the company, ticker, fiscal period and report date:
  the release request contains the word "release" and neither "transcript"
  nor "consensus"; the transcript request contains "transcript"; the
  expectations request contains "consensus" and not "transcript". Steps:
  1. Release: `fetch` a request naming the company, ticker, period and date
     and asking for the official results release; `select_span(agent, page,
     "release")` runs on every page fetched, and the longest span found, in
     characters (the earliest page among equals), becomes `Document("release", page.title,
     span, page.url)`. Taking the first one found would let a stub that only
     links to the release stand in for it.
  2. Transcript: the same with a request for the full earnings call
     transcript and kind `"transcript"`.
  3. Expectations: `fetch` a request for the analyst consensus before the
     report, then `expectations_document`.
  4. Release or transcript missing: raise `LookupError` naming which, before
     anything is written.
  5. `event_date = event_date_for(date.fromisoformat(spec.report_date),
     spec.timing, [d for d, _ in stock_closes])`; the outcome is
     `outcome_from_closes(stock_closes, market_closes, event_date,
     sources=[page urls used])`.
  6. `save_case(root, case, outcome)` with documents in the order release,
     transcript, expectations (when present), and return the case.

### CLI

`python -m researchos build --root DIR --spec FILE --closes FILE --market-closes FILE`
where `--spec` is a JSON file holding one `CaseSpec`. It builds the agent as
`ClaudeJSON(client, effort="medium")` and the fetcher as `fetch_pages` with
the same client (from `client_factory`), prints the case id and the outcome
as JSON to stdout, and exits 0; 2 on bad arguments; 1 on `LLMError`,
`LookupError`, `ValueError` or a missing file, with a message on stderr.

## researchos.compare (v7: is one playbook better than another?)

Compares two evaluation reports case by case. Five or fifteen cases cannot
settle much, so the comparison reports its own uncertainty instead of a bare
difference of means. Lower Brier is better throughout.

- `load_report(path) -> dict`: reads a JSON file holding the dict `evaluate`
  returns, or a wrapper `{"result": <that dict>, ...}`. Returns the dict with
  `"cases"`; raises ValueError if neither form is found.
- `paired(a, b, metric="brier_judge") -> list[tuple[str, float, float]]`:
  `(case_id, a_value, b_value)` for every case scored in both reports, in the
  order of `a["cases"]`. A case is scored in a report when its record has the
  metric as a number (a bool is not a number); skipped records have none.
  A case id that appears twice among one report's scored records raises
  ValueError.
- `bootstrap_ci(diffs, resamples=10000, seed=0, level=0.95) -> tuple[float, float] | None`:
  a percentile bootstrap of the mean. With `rng = random.Random(seed)`, each
  of `resamples` resamples is `rng.choices(diffs, k=len(diffs))` and its mean
  is kept; the result is `(tools.stats.quantile(means, (1 - level) / 2),
  tools.stats.quantile(means, 1 - (1 - level) / 2))`. `None` for no diffs.
  `resamples < 1` or `level` outside `(0, 1)` raises ValueError.
- `compare(a, b, metric="brier_judge", resamples=10000, seed=0) -> dict`: with
  the pairs above and `diff = b_value - a_value` per case (negative: `b` did
  better):
  `{"metric", "n", "mean_a", "mean_b", "mean_diff", "ci_low", "ci_high",
  "b_better", "a_better", "ties", "cases": [{"case_id", "a", "b", "diff"}, ...]}`,
  where `b_better` counts cases with `diff < 0`, `a_better` those with
  `diff > 0`, and the CI is `bootstrap_ci` of the diffs. With no pairs, `n`
  is 0, the means and CI bounds are None and the counts 0. A `metric` that
  does not start with `"brier_"` raises ValueError (the counts assume lower
  is better).

CLI: `python -m researchos compare A.json B.json [--metric NAME]
[--resamples N] [--seed S]` prints `compare(load_report(A), load_report(B),
...)` to stdout as JSON and exits 0; 2 on bad arguments (including a metric
that does not start with `brier_`); 1 on a missing file or a file
`load_report` rejects, with a message on stderr.

## researchos.jsonio (v8: JSON files written on Windows)

ResearchOS runs on Windows, where the JSON files people hand it are often not
plain UTF-8: Windows PowerShell 5.1 writes UTF-16 with a byte order mark when
output is redirected with `>`, and `Out-File -Encoding utf8` (like older
Notepad) puts a UTF-8 byte order mark in front. Such a file holds perfectly
good JSON; refusing it would make the tool fail where it is meant to run, for
a reason the user cannot see.

- `read_json(path) -> object`: reads the file's bytes and decodes them by
  their byte order mark: `EF BB BF` UTF-8, `FF FE` UTF-16 little-endian,
  `FE FF` UTF-16 big-endian, the mark itself dropped; with no mark, UTF-8.
  Returns `json.loads` of the text. Bytes that do not decode, or text that is
  not JSON (an empty file included), raise ValueError with a message naming
  the path. OSError (a missing file, a directory) propagates unchanged.
- `write_json(path, data) -> None`: writes `json.dumps(data, indent=2,
  ensure_ascii=False)` and a newline, as UTF-8 with no byte order mark.
- Every JSON file ResearchOS reads goes through `read_json`: `load_closes`,
  `load_case_spec`, `load_report`, `load_playbook`, `load_case`,
  `load_outcome` and `load_masked`. Nothing else about them changes: a file
  that decodes to JSON of the wrong shape raises what it raised before.

CLI: `train`, `evaluate`, `build` and `compare` take `--out FILE`. The JSON
they would print goes to FILE through `write_json` instead, and nothing goes
to stdout. A FILE that cannot be written exits 1 with a message on stderr
(whatever the command already did, such as saving the playbook, stands).
Without `--out`, output is unchanged.
