# ResearchOS -- specification

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

**Domains.** Every case belongs to one domain, and a command is given
cases of one domain. The steps above are the first, `earnings` (v1-v20).
The second, `macro` (v21 onward), asks of an as-of snapshot of the
markets and the economy whether each of its targets will be higher at
the end of each horizon. Nothing is masked; instead a macro case dated on
or before the model's knowledge cutoff is refused before any call, and a
target's outcome is read only after its horizon has ended and its
forecast is sealed ("Macro cases" and the sections after it). Each
section adds to the ones before it; where a later section changes an
earlier rule, it says so.

Package: `researchos` (Python 3.12). Dependencies: the standard library,
`anthropic` (>=1.13,<2), and the local package `tools` (installed, or on
the path in a clone; see "Using tools" below). Every behaviour below is
tested offline with fake LLMs; nothing in the tests touches the network.

## Using `tools`

Use `tools` rather than reimplementing:
`tools.textmask.redact(text, mapping, *, case_sensitive=False)` (whole-word,
longest key first, case-insensitive by default, never rescans replacements),
`tools.textmask.find_terms(text, terms, *, case_sensitive=False)`,
`tools.textmask.scale_money(text, factor)`, `tools.textmask.shift_years(text,
offset)`, `tools.textmask.scale_table_numbers(text, factor)` (every number
that fills a table cell -- lines containing `" | "` -- times `factor`, keeping
its format; percentages and bare years 1900-2099 are left alone),
`tools.textmask.scale_numbers(text, factor)` (every amount other than money,
whatever the layout: numbers that fill a table cell, including a negative
split as `(22` and `)` across two cells, lines that are just a number, and
comma-grouped numbers in prose; percentages, bare years and numbers right
after `$` stay), `tools.textmask.replace_years_before(text, cutoff,
replacement)` (every year below `cutoff`, as `shift_years` recognizes years,
becomes `replacement`; a year right after a letter, as in "FY2019", stays),
`tools.stats.brier_score(probs, outcomes)`, `tools.stats.log_loss(probs,
outcomes)`, `tools.stats.directional_accuracy(predicted, actual)`,
`tools.stats.hedge_update(weights, losses, eta)`,
`tools.stats.combine_probabilities(probs, weights)`,
`tools.stats.pool_log_odds(probs, weights=None, eps=1e-6)` (the probabilities
taken as independent evidence: the sigmoid of the sum of their log-odds,
each weighted by its weight scaled so the weights used average 1; None
when no key has a number and a positive weight).

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
  replacement), re-mask the *original* documents with the full mapping as
  "Redacting what the masker sees (v15)" describes, then call the guesser. If
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

## researchos.compare: several files a side, training output (v9)

A real comparison rarely sits in two files. Walk-forward training writes one
`train` output per case, each forecast made before its outcome was opened;
held-out cases come from `evaluate`. Comparing them meant merging files by
hand.

- `load_report(path)` also accepts what `train` prints: a JSON list of
  records, bare or wrapped as `{"result": [...], ...}`; it returns
  `{"cases": <that list>}`. In every form the cases must be a list of
  objects; anything else (a list holding a number, a string, `null`, ...)
  raises ValueError naming the path.
- `merge_reports(reports) -> dict`: `{"cases": [...]}` holding the cases of
  every report in turn, in order; `{"cases": []}` for no reports. The
  reports passed in are not modified.
- CLI: besides `compare A.json B.json`, each side can be given as one or
  more files: `compare -a FILE [-a FILE ...] -b FILE [-b FILE ...]`; a side
  is `merge_reports` of its files' `load_report`, in the order given. A case
  scored in two files of the same side is a case scored twice: exit 1 with a
  message on stderr. Bad arguments, exit 2: positional files together with
  `-a`/`-b`, only one of `-a` and `-b`, or a number of positional files other
  than two. `--metric`, `--resamples`, `--seed` and `--out` work as before
  with either form.

## Walk-forward without look-ahead (v10)

A forecast may only use lessons from reactions that had already happened.
Training used to take cases in the order given, so a case could be predicted
with a lesson drawn from a stock that reacted the same day or later -- a
look-ahead, however general the lesson.

- Every record `train` returns, and every record it appends to `history`
  (skipped ones included), carries the case's `event_date`.
- `learned_through(playbook) -> date | None` in `researchos.playbook`: the
  latest `event_date` among the history records the playbook learned from --
  those without `"skipped"` -- as a `datetime.date`; None if there are none.
  Records without an `event_date` (playbooks trained before v10) are ignored.
- `train` takes the cases in order of `event_date` (ties keep the order
  given) and returns its records in that order. Cases sharing an event date
  form a batch: every case of the batch is masked, analyzed and sealed with
  the playbook as it stood before the batch; only then are their outcomes
  opened, one case at a time in batch order (score, update the weights,
  reflect, add lessons, append to history, save).
- `train` and `evaluate` raise ValueError naming the case when a requested
  case's `event_date` is on or before `learned_through` of the playbook they
  start from: its forecast would use reactions it could not have known. A
  requested case whose `event_date` is not an ISO date cannot be placed in
  time and raises ValueError naming it too. They check every requested case
  first, before any agent is called or any file is written. (For `evaluate`
  the same rule keeps a held-out case after everything the playbook learned
  from.)
- CLI: that ValueError exits 1 with the message on stderr.

## researchos.blend (v11): how should the perspectives be combined?

The playbook combines the perspectives with Hedge weights learned case by
case, and on real cases those weights barely moved. Whether another rule
would have explained the reactions better can be answered from records
already scored, without calling a model again: replay each rule
walk-forward over them, every forecast using only what earlier event dates
taught.

- Every record `score` returns also has `prob_by_perspective`: each
  perspective's `prob_up`. `evaluate`'s records also carry the case's
  `event_date` (`train`'s already do).
- `perspective_probs(record) -> dict[str, float]`: `record["prob_by_perspective"]`
  when present; otherwise derived from `brier_by_perspective` and
  `actual_up`: a Brier score is `(p - y) ** 2` with `p` in [0, 1], so
  `p = 1 - sqrt(b)` when the stock went up and `p = sqrt(b)` when it fell.
- `RULES = ("uniform", "median", "hedge", "leader")`.
- `replay(records, rule="hedge", *, eta=0.5) -> dict`: replays a rule over
  the scored records (those with `brier_by_perspective`; others are
  ignored). Raises ValueError for an unknown rule, `eta <= 0`, a scored
  record without an ISO `event_date` or without a probability for one of
  `PERSPECTIVES` (naming the case), or a case id scored twice. Records are
  taken in event-date order (ties keep the order given), in batches of one
  event date; every case of a batch is forecast with the state from before
  the batch, and the state is updated only after the whole batch:
  - `uniform`: the mean of the seven probabilities.
  - `median`: their median (`statistics.median`).
  - `hedge`: weights start uniform over `PERSPECTIVES`; the forecast is
    `tools.stats.combine_probabilities(probs, weights)`; after the batch,
    for each of its cases in order, `weights = hedge_update(weights,
    {p: (probs[p] - y) ** 2 for p in PERSPECTIVES}, eta)` with `y` 1.0 if
    the stock went up, else 0.0.
  - `leader`: the probability of the perspective with the lowest mean Brier
    over all cases of earlier batches (ties: the first in `PERSPECTIVES`
    order); the uniform mean while there are no earlier cases.
  Returns `{"rule", "eta", "n", "brier_blend", "cases": [{"case_id",
  "event_date", "actual_up", "prob", "brier_blend"}, ...]}` with the cases
  in replay order; `eta` is the eta used for `hedge` and None for the other
  rules; `brier_blend` is the mean of the cases' `brier_blend`, None when
  `n` is 0. Each case's `brier_blend` is `(prob - y) ** 2`, so two replays
  compare with `compare --metric brier_blend`.

CLI: `python -m researchos blend FILE [FILE ...] [--rule NAME] [--eta X] [--out FILE]`:
the files are read with `load_report` and merged with `merge_reports` in
the order given, and `replay` of their cases is printed as JSON (or written
with `--out`, as for the other commands). Exit 2 on bad arguments (no file,
an unknown rule, an `--eta` that is not a positive number), 1 on a file that
cannot be read or `load_report` rejects, or a ValueError from `replay`, with
a message on stderr.

## Shared helpers (v12)

`tools` now decides what a loosely typed value is: `tools.dates.parse_iso_date`
and `tools.stats.is_number` (the tools package's SPEC.md, "v2"). Several
modules here carry private copies of the same checks, each slightly
different. Use the shared ones and keep no copies:

- Every check in `researchos` of whether a value is a finite number uses
  `tools.stats.is_number`.
- Every parse of an ISO date from a case's or a record's `event_date`, a
  history record, or the knowledge cutoff uses `tools.dates.parse_iso_date`.
  Only `researchos.sources` keeps `date.fromisoformat`, where a malformed
  date in a file it reads must raise.
- Nothing else changes: the same inputs give the same results and raise the
  same errors with the same messages.

## Abstraction masking (v13)

Redaction keeps a document's shape: on the first fifteen real cases the
guesser still named every company after four rounds, from segment
structure, product lines and ratios that survive any renaming. Abstraction
rewrites instead: an abstractor agent turns each pre-masked document into a
brief that keeps what an analyst needs and drops what identifies the
business; the masker and guesser rounds then run on the briefs.

- `MaskConfig.abstract: bool = False`.
- `ABSTRACT_SYSTEM` (a module constant) instructs the abstractor to rewrite
  one document of a company's quarterly report as a brief for an analyst who
  must judge the stock's reaction without knowing the company: keep the
  results against the prior year and against what was expected, the
  direction and size of guidance changes, margins and growth rates, cash
  flow and capital returns, management's tone and the risks raised; state
  them as rates, ratios and changes, not as amounts; describe the business
  and each segment generically ("segment A", "the largest segment"); leave
  out every name of the company, its products, brands, segments, people,
  places, customers and competitors, and any detail found at only one
  company. `ABSTRACT_SCHEMA` is an object with the single required string
  `brief` (strict: no other properties).
- `mask_case(case, masker, guesser, rng, config=MaskConfig(), *, abstractor=None)`:
  with `config.abstract` False the abstractor is never used and nothing
  changes. With it True (ValueError if `abstractor` is None):
  1. pre-masking and the identity guard run as before;
  2. the abstractor is called once per pre-masked document, in order:
     `abstractor.json(ABSTRACT_SYSTEM, prompt, ABSTRACT_SCHEMA)`, the prompt
     holding the document's kind and its pre-masked text; each document
     becomes `Document(kind, "<kind> brief", <the brief>, "")`, and the
     identity guard runs on the briefs;
  3. the masker and guesser rounds run as before, except that a round's
     documents are the briefs redacted with the mapping
     (`tools.textmask.redact`, then the identity guard) -- the briefs were
     written from text already scaled and shifted, so money and years are
     not scaled or shifted again.
- `MaskedCase.abstracted: bool = False`, True for a case masked with
  abstraction; `save_masked` writes it into `masked.json`, `load_masked`
  reads it (False when absent).
- `Roles.abstractor` defaults to None; training and evaluation pass it to
  `mask_case`. CLI: `--mask-abstract` on `train` and `evaluate` runs with
  `MaskConfig(abstract=True)` (together with `--mask-give-up` when both are
  given), the abstractor being `ClaudeJSON` at effort `"high"`.

## Fetching pages by address (v14)

`build` finds its documents through the API's server-side web search and
fetch. When the addresses are already known -- an 8-K exhibit, a transcript
page, a preview article -- the pages can be fetched directly instead, with
no web tools and no API tokens spent on finding them. In `researchos.sources`:

- `html_to_text(html: str) -> tuple[str, str]`: `(title, text)` using only
  `html.parser`. The title is the text of the `<title>` element, whitespace
  collapsed, "" if there is none. The text is the visible text of the page:
  the contents of `script`, `style`, `noscript`, `head`, `svg` and `template`
  are dropped; `p`, `div`, `br`, `li`, `tr`, `table`, `h1`-`h6`, `section`,
  `article`, `header`, `footer`, `pre`, `blockquote`, `ul`, `ol`, `dl`,
  `dt`, `dd` and `hr` each start a new line; within a table row the cells
  (`td`, `th`) are joined by " | "; character references are decoded; runs
  of spaces, tabs and non-breaking spaces within a line become one space;
  lines are stripped, and empty lines dropped.
- `get_page(url, *, user_agent, transport=None) -> FetchedPage`: one GET
  with the header `User-Agent: <user_agent>`. `transport(url, headers) ->
  (body: bytes, content_type: str)`; the default calls
  `urllib.request.urlopen(urllib.request.Request(url, headers=headers),
  timeout=30)` and returns the body and the response's `Content-Type`
  header ("" when absent). The body is decoded with the charset the content
  type names (UTF-8 when none; undecodable bytes replaced). An HTML page --
  a content type of `text/html` or `application/xhtml+xml`, or no content
  type and a body that begins, after whitespace, with `<` -- becomes
  `FetchedPage(url, *html_to_text(body))`; `text/plain` becomes
  `FetchedPage(url, "", body)`; any other content type (a PDF, an image)
  raises ValueError naming the url and the type. Errors from the transport
  (`OSError`, which includes urllib's HTTP and URL errors) propagate.
- `url_fetcher(urls, *, user_agent, transport=None)`: `urls` maps
  `"release"`, `"transcript"` and `"expectations"` to lists of addresses.
  Returns a `fetch(request) -> list[FetchedPage]` for `build_case`, which
  tells the kinds apart by its requests: a request containing "transcript"
  is the transcript's, else one containing "consensus" is the
  expectations', else it is the release's. It returns `get_page` of each
  address of that kind, in order; an address whose fetch raises `OSError`
  or ValueError is left out, with a warning on the `researchos` logger
  naming it, so one dead link does not stop the case. A kind with no
  addresses gives `[]`.
- No contact address is built in. The user agent comes from the caller;
  sites such as SEC EDGAR ask for one that names who is fetching.

CLI: `build` takes `--release-url URL`, `--transcript-url URL` and
`--expectations-url URL` (each may be repeated) and `--user-agent TEXT`
(default: the `RESEARCHOS_USER_AGENT` environment variable, else
`"ResearchOS/0.1 (research use)"`). When any URL option is given, the
fetcher is `url_fetcher` of those addresses and no web tools are used; the
span and expectations agent is `ClaudeJSON` as before. Without URL options
`build` works as before.

## Redacting what the masker sees (v15)

The masker proposes each term exactly as it appears in the text it was
shown, where money amounts are already scaled, years already shifted and
earlier redactions already replaced. Until now each round redacted the
*original* documents before scaling and shifting, so such a term never
matched: on the first small-cap case 16 of the masker's 146 terms changed
nothing (for instance "inception in 2010", a shifted year, and "an
automotive partner", the replacement of an earlier term), and the guesser
kept naming the company from them.

- In redaction mode (`MaskConfig.abstract` False), a round's documents are
  made from the case's original documents: each title and text becomes
  `redact(shift_years(scale_money(redact(original, base), factor), year_offset), mapping)`
  (v18 applies `mapping` round by round instead),
  where `base` is the mapping `pre_mask` returned (company, aliases and
  ticker, redacted before scaling and shifting so that a name containing a
  year or an amount is still caught) and `mapping` is the full mapping. A
  masker term is therefore matched against the scaled and shifted text only:
  a term naming a shifted year never redacts the original year it came
  from, nor a different original year that shifting happens to turn into
  it. `source` stays `""`; the identity guard runs afterwards as before.
  `pre_mask` itself is unchanged.
- (Replaced by "Redaction rounds in order (v18)".) In both modes, when a
  redaction is added to the mapping, every replacement already in the
  mapping that contains its term as a whole word (matched as
  `redact` matches, ignoring case) is rewritten to `redact(replacement,
  {term: new_replacement})`, so text an earlier redaction put in is redacted too and
  one `redact` pass with the mapping still produces the final text. The new
  redaction's own replacement is kept as given, even when it contains its own
  term (it is not rewritten and does not grow from round to round). The
  redactions of one masker output are added in order. A term already in the
  mapping (ignoring case) is skipped as before: its key keeps its place,
  though its replacement may have been rewritten by later terms.
- `MaskedCase.mapping` (and `mask_key.json`) hold the mapping after these
  rewrites.
- Abstraction mode otherwise works as before: its rounds redact the briefs,
  which are written in the scaled and shifted text the masker sees, so only
  the rewriting of earlier replacements is new there.

## Tables and years long past (v16)

On the first small-cap cases, masking left two kinds of real figures in
place. Financial tables kept their reported numbers (in one release 877 of
911 comma-grouped numbers came through unchanged): the tables put `$` in a
cell of its own or print rows with no `$`, and `scale_money` only scales
amounts with an attached `$`. And years long past -- a founding year, the
year of a statute -- let the guesser work out the year shift and then undo
it everywhere ("PSLRA 1995 shown as 1987, an 8-year shift").
`tools.textmask` now has `scale_table_numbers(text, factor)` and
`replace_years_before(text, cutoff, replacement)` (see "Using `tools`");
masking uses both.

- `EARLIER_YEAR = "an earlier year"` (a module constant of
  `researchos.masking`).
- `MaskConfig.history_years: int | None = None`. When it is set, the cutoff
  is the year of `case.event_date` (`int(case.event_date[:4])`) minus
  `history_years`, and every year before the cutoff becomes `EARLIER_YEAR`.
  A negative `history_years` raises ValueError (from `pre_mask`, and so from
  `mask_case`).
- Masking a text with mapping `m` now goes: `redact(text, m)`; then, when
  `history_years` is set, `replace_years_before(..., cutoff, EARLIER_YEAR)`;
  then `scale_money(..., factor)`; then `shift_years(..., year_offset)`;
  then `scale_table_numbers(..., factor)`. Names are redacted first, so a
  name that contains a year is still caught; years long past are dropped
  before the shift, which applies to the years that remain; table numbers
  are scaled after the shift, so a number that scaling brings into the
  range of years is not taken for a year and shifted, while a year that
  heads a table column is shifted and then left alone as a year.
- `pre_mask(case, rng, config)` masks every title and text this way with
  its base mapping (the factor and offset are drawn as before). In
  redaction mode a round's documents are the originals masked this way with
  `base` and then redacted with the full mapping, as in v15:
  `redact(<the masking above with base>, mapping)`. In abstraction mode the
  abstractor receives `pre_mask`'s documents, so it too never sees a table's
  real numbers or a year long past; its briefs are redacted as before.
- The year replacement uses the case, not the text: a case whose
  `event_date` does not start with a four-digit year raises ValueError when
  `history_years` is set. With `history_years` None nothing about years
  changes.
- CLI: `train` and `evaluate` take `--mask-history-years N` (an integer,
  `N >= 0`, else a bad argument), which runs with
  `MaskConfig(history_years=N)` together with the other mask options given.

## Every amount, whatever the layout (v17)

`scale_table_numbers` reads tables as `" | "`-separated rows. With v16 the
16 small-cap cases still showed 404 of their 6,194 comma-grouped real
figures after pre-masking, all in other layouts: one release printed every
table cell on a line of its own (179 of its 179 came through), others split
a negative as `(22` and `)` across two cells, and a slide deck flattened
into long lines kept its figures inline. `tools.textmask.scale_numbers`
(see "Using `tools`") scales every amount whatever the layout; masking uses
it in place of `scale_table_numbers`.

- Masking a text (in `pre_mask` and in every redaction-mode round) is as in
  v16 with the last step changed: `scale_numbers(..., factor)` instead of
  `scale_table_numbers(..., factor)`, still after `shift_years` and before
  the round's `redact(..., mapping)`. Nothing else changes.

## Redaction rounds in order (v18)

v15 matched every term against the scaled and shifted text, and rewrote
earlier replacements that contain a new term. That still misses a term
that wraps around an earlier replacement: after round 1 replaced "ClearPath"
with "the platform", a round-2 term "the platform Forward revenue" occurs
in the text the masker saw but nowhere in the text the mapping is applied
to. On ten small-cap cases 158 of the maskers' 1,668 terms changed nothing,
136 of them for this reason, and two maskers reported it. The masker
proposes each round's terms against the text the earlier rounds left, so
the rounds are now applied in that order.

- `MaskedCase.redaction_rounds: list[dict[str, str]]` (default empty): the
  redactions each masker round added, one dict per round in round order,
  each in the order the masker gave them. A term already in the mapping
  (ignoring case) -- from `pre_mask`, the identity guard or an earlier round
  or earlier in the same output -- is not added again, as before; a round
  that adds nothing still has its (empty) dict. `mapping` still holds every
  redaction (base, guard and all rounds), each replacement exactly as given.
- A round's documents apply the rounds so far in order, each with one
  `redact` pass: with `R1 ... Rk` the dicts of rounds 1 to k,
  `redact(...redact(redact(masked, R1), R2)..., Rk)`, where `masked` is the
  original text masked with the base mapping as in v17 (redaction mode) or
  the brief (abstraction mode). The identity guard runs afterwards as before.
- The rewriting of earlier replacements (v15) is gone: applying the rounds
  in order covers the term inside an earlier replacement as well as the term
  around one, and a replacement is never changed after the masker gives it.
  Tests that pinned the rewriting change accordingly.
- `save_masked` writes `redaction_rounds` into `mask_key.json`;
  `load_masked` reads it back (`[]` when absent).

## Do the perspectives disagree? (v19)

On 21 real cases every combination rule `blend` replays scored the same
(Brier 0.233-0.235), because the seven perspectives say nearly the same
thing: their probabilities correlated at 0.86 on average across the cases.
No weighting can add what seven views that move together do not have, so
whether the perspectives disagree has to be measured before any rule is
tuned. In `researchos.blend`:

- `agreement(records) -> dict`: over the scored records `replay` accepts,
  checked the same way (a ValueError for every reason `replay` gives one),
  in the order given, each perspective's probability as `perspective_probs`
  gives it and `y` 1.0 when the stock went up, else 0.0. Returns:
  - `"n"`: the number of scored records;
  - `"perspectives"`: for every name in `PERSPECTIVES`, in that order,
    `{"mean_prob", "sd_prob", "brier", "calls", "hits"}`: the mean
    probability, its population standard deviation
    (`statistics.pstdev`), the mean Brier score `(p - y) ** 2`, the number
    of records whose probability is not exactly 0.5 (`calls`), and how many
    of those had the direction right (`p > 0.5` exactly when the stock went
    up: `hits`). With no records the three means are None and both counts
    0;
  - `"pairs"`: for every two perspectives `a` before `b` in `PERSPECTIVES`
    order, in that order (21 pairs), `{"a", "b", "correlation"}`: the
    Pearson correlation of their probabilities over the records, None when
    there are fewer than two records or either has a population standard
    deviation of 0;
  - `"mean_correlation"` and `"min_correlation"`: the mean and the smallest
    of the pairs' correlations that are not None; None when every one is.
- CLI: `python -m researchos agreement FILE [FILE ...] [--out FILE]`: the
  files are merged as `blend` merges them and the result printed as JSON
  (or written to `--out`); an unreadable file or a record `agreement`
  refuses exits 1 with the message on stderr, as for `blend`. Files after
  an option are still files, as for `blend`.

## Perspectives built to disagree (v20)

On the 21 real cases of v19 the seven perspectives' probabilities
correlated at 0.86. Every analyst reads the whole report and answers the
same question -- will the stock beat the market? -- so each gives its
overall impression, tinted by its angle, and no rule for combining them can
add what they do not have. An "isolated" mode asks each perspective a
narrower question: how its factor alone would move the stock if everything
else in the report had come in as the market expected. Views like that can
disagree, and they combine by adding their evidence in log-odds
(`tools.stats.pool_log_odds`) rather than by averaging. What `analyze` has
done so far is the "holistic" mode, and it stays the default.

In `researchos.perspectives`:

- `MODES = ("holistic", "isolated")`.
- `analyze(masked, playbook, analyst, judge, mode="holistic") -> Analysis`.
  A mode not in `MODES` raises ValueError naming it, before any call.
  "holistic" is exactly what `analyze` did before. "isolated" makes the same
  calls in the same order, with the same `prefix`, schemas and parsing of
  the replies, and differs only in:
  - the analysts' system prompt: `ISOLATED_ANALYST_SYSTEM` (below);
  - each perspective's prompt: the first line is still
    `Perspective: <name>`; the second is `Your factor: <focus>`, where
    `<focus>` is the text the holistic prompt gives for what the perspective
    looks at; then this paragraph, verbatim, and then
    `render_for_prompt(playbook)` as in the holistic prompt:

    ```text
    Judge only this factor, as if everything else in the report had come in exactly as the market expected. Give your stance on this factor, the probability that the stock beats the market on the first session that can react from this factor alone (0.50 if it gives no signal), your conviction, and the key points behind it.
    ```
  - `prob_weighted`: `pool_log_odds` of the views' probabilities
    (`{name: view.prob_up}`) with the playbook's weights; when that is None
    (no perspective has a positive weight), `pool_log_odds` of the views'
    probabilities with no weights; then clamped to [0.01, 0.99] as every
    probability is;
  - the judge's system prompt: `ISOLATED_JUDGE_SYSTEM` (below);
  - in the judge's prompt, the line giving `prob_weighted` reads
    `Evidence-pooled probability of the views (their log-odds added): <p>`
    (`<p>` to three decimals) instead of
    `Playbook-weighted probability of the views: <p>`. The rest of the
    judge's prompt is unchanged.

`ISOLATED_ANALYST_SYSTEM`, verbatim (one paragraph):

```text
You are an equity analyst judging how a stock will react to a quarterly earnings report. The company's identity is hidden: names are replaced, money amounts are multiplied by a secret factor (absolute sizes are meaningless; ratios, growth rates and margins are intact) and years are shifted. Do not try to identify the company; reason only from the documents. You judge one factor in isolation. Assume that everything in the report outside your factor came in exactly as the market expected, and estimate the probability that the stock beats the market (positive abnormal return, stock minus market) on the first trading session that can react to the report, from your factor alone. 0.50 means your factor gives no signal either way; move away from 0.50 only as far as this factor by itself would move the stock. Do not give an overall view of the report: another analyst combines the factors.
```

`ISOLATED_JUDGE_SYSTEM`, verbatim (one paragraph):

```text
You are the chief analyst. Several analysts have each judged one factor of a quarterly earnings report in isolation, each as if everything else in the report had come in as the market expected. The company's identity is hidden (names replaced, money scaled, years shifted). Their probabilities are evidence from different factors: they add up rather than average, and a view at 0.50 is a factor that gives no signal. Weigh them against the documents and the playbook, and give your own probability that the stock beats the market (positive abnormal return) on the first trading session that can react, with a short thesis.
```

In `researchos.loop`, `train` and `evaluate` take a keyword
`perspective_mode="holistic"` (after `knowledge_cutoff`) and analyze every
case in that mode. A mode not in `MODES` raises ValueError before anything
is masked, called or written. `evaluate`'s result gains
`"perspective_mode"`: the mode it used. Nothing in the records changes; in
"isolated" mode `brier_weighted` scores the pooled forecast, and
`brier_uniform` still scores the plain mean of the views.

In `researchos.blend`, `RULES` becomes `("uniform", "median", "hedge",
"leader", "pool")`. `pool` forecasts `pool_log_odds` of the seven
probabilities with no weights, clamped to [0.01, 0.99]; it learns nothing
from earlier cases, and its `eta` is None. `agreement` is unchanged.

CLI: `train` and `evaluate` take `--perspective-mode {holistic,isolated}`.
When it is given, `perspective_mode=` is passed to `train` or `evaluate`;
when it is not, nothing is passed and their default holds (as for the
masking options). Any other value exits 2, as argparse does.
`blend --rule pool` replays the new rule.

## Macro cases: as-of snapshots, per-target outcomes (v21)

So far a case is one company's earnings report, and its outcome is one
number known at the close of the first session that can react. A macro case
is a snapshot of the markets as of the close of a date: documents that
describe the economy and the markets up to that date, and targets -- market
levels such as the 10-year Treasury yield or the S&P 500 -- each forecast
over one or more horizons (`"1w"`, `"1m"`, `"3m"`, as
`tools.dates.parse_horizon` reads them). Each target and horizon resolves on
its own date, weeks or months after the snapshot, so the outcome is written
later, piece by piece, and a small manifest records which pieces are known.
A macro case has no company to hide, so its documents are analyzed as they
are; and nothing in them stops a model from remembering what followed their
date, so a macro case may only be forecast when its date is after the
model's knowledge cutoff.

This version adds the data types, the files and the checks; nothing calls a
model with a macro case yet. Earnings cases behave exactly as before, and
their files are byte for byte what they were.

### In `researchos.cases`

```python
DOMAINS = ("earnings", "macro")
KINDS = ("bp", "return", "points")   # how a target's change is measured (tools.finance.level_change)
RESOLUTION_FILE = "resolution.json"

@dataclass
class Target:
    id: str                  # e.g. "DGS10"
    name: str                # e.g. "10-year Treasury yield"
    kind: str                # one of KINDS
    horizons: list[str]      # e.g. ["1w", "1m", "3m"]
    as_of_date: str          # ISO date of the observation the forecasts start from
    as_of_value: float       # the level on that date
    trailing: dict[str, float | None] = field(default_factory=dict)
    # horizon -> the change over the same length of time up to as_of_date

@dataclass
class TargetOutcome:
    target: str              # the Target's id
    horizon: str
    kind: str
    start_date: str
    start: float
    end_date: str
    end: float
    change: float            # tools.finance.level_change(start, end, kind)
    up: bool                 # change > 0
```

`Case` gains two fields after `documents`: `domain: str = "earnings"` and
`targets: list[Target] = field(default_factory=list)`.
`Outcome.abnormal_return_1d` becomes `float | None = None`; it stays the
first field, so `Outcome(0.03)` means what it did. `Outcome` gains
`targets: dict[str, TargetOutcome] = field(default_factory=dict)` after
`sources`, keyed by `target_key`.

- `target_key(target_id, horizon) -> str`: `f"{target_id}_{horizon}"`, the
  name a target's forecast over a horizon goes by (a later version makes it
  a property name in a schema). Raises ValueError when `target_id` is not a
  `str` that matches `[A-Za-z0-9_]+` as a whole (ASCII letters, digits and
  underscores only), or when `horizon` is not a valid horizon. A horizon is
  valid when `tools.dates.parse_horizon` accepts it and it has no whitespace
  around it (`horizon == horizon.strip()`): `"1w"` is valid; `" 1w"`, and
  `"1w"` followed by a newline, are not.
- `check_targets(targets) -> None`: raises ValueError, naming the target (by
  its id, or by its position in the list when the id itself is the
  problem), when:
  - `targets` is empty (a macro case forecasts at least one target);
  - an element is not a `Target`;
  - an id is not valid (as for `target_key`), or two targets have the same
    id;
  - a `kind` is not in `KINDS`;
  - `horizons` is not a non-empty list of valid horizons without repeats;
  - `as_of_date` is not a `str` that `tools.dates.parse_iso_date` reads;
  - `as_of_value` is not a number (`tools.stats.is_number`).

  `name` and `trailing` are not checked.
- `target_keys(case) -> list[str]`: the keys of every target of the case
  and every horizon of that target, target by target in the case's order,
  each target's horizons in its order. `[]` when the case has no targets
  (every earnings case); otherwise `check_targets(case.targets)` runs first,
  and its ValueError passes through.
- `macro_case(case_id, as_of, targets, documents) -> Case`: a macro case.
  `as_of` must be a `str` that `parse_iso_date` reads; it is stored in its
  canonical form `YYYY-MM-DD` (`parse_iso_date(as_of).isoformat()`, so
  `" 2026-10-30 "` becomes `"2026-10-30"`). The case has `company=""`,
  `ticker=""`, `aliases=[]`, `fiscal_period=f"as of {as_of}"` (canonical),
  `event_date=as_of` (canonical: every rule that orders or gates cases by
  date applies to the snapshot's date), `documents=list(documents)`,
  `domain="macro"` and `targets=list(targets)`. Raises ValueError when
  `as_of` is not an ISO date string, when `check_targets(targets)` fails,
  or when a target's `as_of_date` is after `as_of` (a snapshot cannot start
  from a level it had not seen yet), naming the target.
- `target_from_dict(data) -> Target`: `id`, `kind`, `horizons`,
  `as_of_date` and `as_of_value` are required (KeyError when one is
  missing); `name` defaults to `""` and `trailing` to `{}`. `horizons`
  becomes a new list and `trailing` a new dict.
- `target_outcome_from_dict(data) -> TargetOutcome`: every field required
  (KeyError when one is missing).

Files:

- `case.json` is `asdict(case)`, except that the keys `domain` and
  `targets` are left out when the case's domain is `"earnings"` and it has
  no targets, so such a file is exactly what it was before v21.
  `outcome.json` is `asdict(outcome)` without the key `targets` when
  `outcome.targets` is empty.
- `save_case(root, case, outcome=None) -> None`: as before when `outcome`
  is given. With `None`, only `case.json` is written; an existing
  `outcome.json` is left as it is.
- `save_outcome(root, case_id, outcome) -> None`: writes `outcome.json`
  under `<root>/<case_id>/` (creating the directory), in the same format.
- `load_case` also reads `domain` (default `"earnings"`) and `targets`
  (default `[]`, each element through `target_from_dict`). It still never
  opens `outcome.json`.
- `load_outcome`: a file without a `targets` key is read exactly as before
  (KeyError when `abnormal_return_1d` is missing). With a `targets` key,
  `abnormal_return_1d` defaults to `None` and every entry of `targets`
  becomes a `TargetOutcome` through `target_outcome_from_dict`, its key
  kept.

The resolution manifest, `<root>/<case_id>/resolution.json`, records which
of a case's targets have resolved. It holds dates and flags, never a level
or a change, so it can be read before a forecast is sealed, as
`outcome.json` cannot. A target's horizon ends on
`tools.dates.horizon_end(as_of, horizon)`, where `as_of` is the case's
`event_date`; that date is also the target's resolution date, the first
date on which its outcome can be known.

- `resolution_manifest(case, resolved=()) -> dict`:

  ```json
  {"as_of": "2026-10-30",
   "targets": {"DGS10_1w": {"horizon_end": "2026-11-06", "resolved": true},
               "DGS10_1m": {"horizon_end": "2026-11-30", "resolved": false}}}
  ```

  `as_of` is the case's `event_date` in its canonical form; `targets` has
  one entry per key of `target_keys(case)`, in that order, with the horizon
  end as an ISO date and `resolved` True exactly for the keys in
  `resolved` (any iterable of keys). Raises ValueError when the case's
  domain is not `"macro"`, when its `event_date` is not a `str` that
  `parse_iso_date` reads, when `target_keys(case)` fails, when a horizon end
  is outside the dates Python can represent (the ValueError of
  `horizon_end`), or when `resolved` names a key the case does not have.
- `save_resolution(root, case_id, manifest) -> None`: writes the manifest
  as `resolution.json` (creating the directory).
- `load_resolution(root, case_id) -> dict | None`: the manifest; `None` when
  there is no `resolution.json`. It never opens `outcome.json`.
- `unresolved_targets(case, manifest) -> list[str]`: the keys of
  `target_keys(case)`, in that order, that the manifest does not mark
  resolved. A key is resolved only when `manifest["targets"][key]` is a
  dict whose `"resolved"` is `True`; anything else counts as unresolved: a
  `manifest` of `None` (or one that is not a dict), a missing or non-dict
  `targets`, a missing key, a `resolved` that is not `True`. Keys the
  manifest has and the case does not are ignored. ValueError from
  `target_keys` passes through.

### In `researchos.masking`

- `unmasked(case) -> MaskedCase`: the case's documents as they are, for a
  case with nothing to hide: `MaskedCase(case_id=case.case_id,
  documents=[...], status="unmasked", rounds=0, mapping={}, factor=1.0,
  year_offset=0, final_guesses=[], abstracted=False, redaction_rounds=[])`,
  where `documents` has one new `Document` per document of the case, in
  order, with the same `kind`, `title` and `text` and `source=""` (as a
  masked case's documents have). Nothing is changed in the text; changing
  the result does not change the case. No agent is called and no file is
  read or written.

### In `researchos.loop`

- `require_after_cutoff(case_id, case, knowledge_cutoff) -> None`: a macro
  case can only be forecast when its date is after the model's knowledge
  cutoff. Raises ValueError naming `case_id` when `knowledge_cutoff` is
  `None`, when the case's `event_date` is not a `str` that
  `parse_iso_date` reads, or when that date is on or before the cutoff.
  `knowledge_cutoff` is anything `parse_cutoff` accepts, and
  `parse_cutoff`'s ValueError passes through.
- `single_domain(cases) -> str`: the domain every case in `cases` (an
  iterable of `Case`) shares; `"earnings"` when there are none. Raises
  ValueError naming the domain when a case's domain is not in `DOMAINS`,
  and naming both when the cases have more than one domain: forecasts,
  playbooks and perspectives differ by domain, so a run is one domain.

Nothing calls these yet; the CLI is unchanged.

## Series from free sources (v22)

A macro snapshot reads market and economic series, and every one it needs
is free to download without an account:

- FRED publishes each series as CSV (`observation_date,DGS10` and then one
  `date,value` line per day, the value left empty on a day without one).
- ALFRED, its archive, publishes the same series as it was known on a past
  date, the column then named `<ID>_<YYYYMMDD>`. An economic release such as
  payrolls or CPI is revised after it comes out, so a snapshot of a past date
  must read the series as it stood on that date; a market series is not
  revised, so its current file is enough.
- The CFTC publishes the weekly Commitments of Traders as JSON. Its numbers
  are strings, and the positions of non-commercial traders, long minus
  short, are the usual measure of speculative positioning.

The new module `researchos.series` fetches these series, checks them and
stores them as the JSON files `sources.load_closes` already reads, so the
stored data can be inspected by eye. Nothing in it calls a model, and the
tests never touch the network: they inject a transport or replace
`urllib.request.urlopen`.

In this section a *date argument* is a `datetime.date` or a string that
`tools.dates.parse_iso_date` reads; anything else raises ValueError. A
*series id* is a `str` that matches `[A-Za-z0-9_]+` as a whole, and a
*contract code* a `str` that matches `[0-9A-Z]{6}` as a whole (`"043602"`,
`"13874A"`, `"1170E1"`). Anything else raises ValueError. URLs carry dates
in the form `YYYY-MM-DD`.

### Addresses

```python
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
ALFRED_CSV = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
CFTC_LEGACY = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
```

- `fred_url(series_id, *, start=None, vintage=None) -> str`: without a
  vintage, `f"{FRED_CSV}?id={series_id}"`. With one,
  `f"{ALFRED_CSV}?id={series_id}&vintage_date={vintage}"`. Either way,
  `&cosd={start}` is appended when `start` is given. `start` and `vintage`
  are date arguments or None.
- `cftc_url(code, *, limit=600) -> str`:
  `f"{CFTC_LEGACY}?cftc_contract_market_code={code}&$order=report_date_as_yyyy_mm_dd%20DESC&$limit={limit}"`,
  the newest reports first. `limit` must be an `int` that is not a `bool`,
  from 1 to 50000; anything else raises ValueError.

### Reading what the sources return

- `parse_fred_csv(text, series_id) -> list[tuple[datetime.date, float]]`:
  - The lines are `text.splitlines()`; a line that is empty after
    stripping its whitespace is skipped, wherever it is.
  - The first other line is the header. It must have exactly two fields
    separated by a comma, each stripped of whitespace. The first is
    `observation_date` or `DATE`. The second is the series id itself, or
    the id followed by `_` and eight ASCII digits (an ALFRED vintage
    column). Anything else raises ValueError naming the series id: an
    error page, or the CSV of another series. So does a text with no
    header at all.
  - Every later line is an observation and must have exactly two fields,
    each stripped. The first is a date: a string `parse_iso_date` reads.
    The second is empty or `.` when the source has no value for that date
    (the observation is left out), and otherwise a decimal: it matches
    `-?[0-9]+(\.[0-9]+)?` as a whole and `float()` of it is finite. Any
    other line raises ValueError naming the series id.
  - The result is sorted by date. When two lines have the same date, the
    later line wins, even when it has no value.
- `parse_cftc(payload, code) -> tuple[list[tuple[datetime.date, float]], list[tuple[datetime.date, float]]]`:
  the payload is the decoded JSON. The result is two series, the net
  non-commercial position and the open interest, both sorted by date.
  - The payload must be a list. Anything else raises ValueError; when it is
    a dict with a `"message"` (how the CFTC's site reports an error), that
    message is included.
  - Every element must be a dict. Its `cftc_contract_market_code`,
    stripped, must equal `code`, so a report of another contract is never
    stored under this one.
  - Its `report_date_as_yyyy_mm_dd` is a string whose part before the first
    `T` (the whole string when there is none) `parse_iso_date` reads:
    `"2026-10-27T00:00:00.000"` is 2026-10-27.
  - Its `noncomm_positions_long_all`, `noncomm_positions_short_all` and
    `open_interest_all` are each an `int` that is not a `bool`, or a string
    that, stripped, matches `-?[0-9]+`.
  - The net position is long minus short. Both series hold floats.
  - Anything else in a row raises ValueError naming the code: a missing
    field (never KeyError), a row that is not a dict, a bad date, a bad
    count, a row of another contract.
  - When two rows have the same date, the later row wins in both series.
  - An empty list gives two empty series.

### Files

A series file is the JSON list `[["YYYY-MM-DD", value], ...]` that
`sources.load_closes` reads, sorted by date. Under a series directory, the
current file of a series is `<dir>/<series_id>.json`. The file of the series
as it was known on a date is `<dir>/<series_id>@<YYYY-MM-DD>.json`.

- `save_series(path, observations) -> None`: writes `observations`, pairs of
  a `datetime.date` that is not a `datetime.datetime` and a number
  (`tools.stats.is_number`), as a series file through `write_json`.
  - The parent directories are created.
  - The pairs are sorted by date, and of two pairs with the same date the
    later one is kept.
  - Any other pair raises ValueError before anything is written, so an
    existing file is left as it was.
- `load_series(series_dir, series_id, as_of=None) -> list[tuple[datetime.date, float]]`:
  the series through `sources.load_closes` (its ValueError and OSError,
  for a missing file, pass through).
  - Without `as_of`, the current file is read.
  - With `as_of` (a date argument), the file for that date is read when it
    exists, and the current file otherwise. Either way, only the
    observations dated on or before `as_of` are returned, so a current file
    never gives a snapshot a value from after its date.

### Fetching

- `fetch_text(url, *, user_agent, transport=None) -> str`: one GET through
  `transport(url, headers) -> (body, content_type)` with the headers
  `{"User-Agent": user_agent}`. Without a transport it goes through urllib
  as `sources.get_page` does: 30 seconds' timeout, and HTTP errors are
  OSErrors.
  - The body is decoded by the charset its Content-Type names, as
    `get_page` decodes, and as UTF-8 when it names none or one Python does
    not know. A byte order mark is dropped.
  - Unlike `get_page`, it accepts any content type (FRED serves
    `application/csv`).
  - The transport's errors propagate.
- `fetch_fred(series_id, *, user_agent, start=None, vintage=None, transport=None)`:
  `parse_fred_csv(fetch_text(fred_url(series_id, start=start, vintage=vintage), ...), series_id)`.
  The arguments are checked before anything is fetched.
- `fetch_cftc(code, *, user_agent, limit=600, transport=None)`: the text
  `fetch_text` returns for `cftc_url(code, limit=limit)`, decoded as JSON
  (text that is not JSON raises ValueError), then `parse_cftc(payload,
  code)`. The arguments are checked before anything is fetched.

### CLI

```text
python -m researchos fetch-series --source {fred,cftc} --id ID --file FILE
    [--start YYYY-MM-DD] [--vintage YYYY-MM-DD] [--limit N] [--oi-file FILE]
    [--user-agent TEXT] [--out FILE]
```

- `--source fred`: `fetch_fred` with `--id` as the series id, and
  `--start` and `--vintage` when they are given; the series is saved to
  `--file`.
- `--source cftc`: `fetch_cftc` with `--id` as the contract code, and
  `--limit` when it is given. The net position is saved to `--file`, and
  the open interest to `--oi-file` when that is given.
- The user agent is chosen as `build` chooses it: `--user-agent`, else
  `RESEARCHOS_USER_AGENT`, else the default.
- Exit 2, as argparse exits, on bad arguments:
  - `--start` or `--vintage` not an ISO date, or given with `--source cftc`;
  - `--limit` not an integer from 1 to 50000, or given with `--source fred`;
  - `--oi-file` given with `--source fred`;
  - a missing `--source`, `--id` or `--file`.
- Exit 1, with a message on stderr, when fetching or saving raises OSError
  or ValueError (a bad id or code included). When fetching or reading fails,
  nothing is written: every series is parsed before the first file is saved.
- On success, exit 0 and emit, as every command does (printed, or written
  to `--out`), `{"source": ..., "id": ..., "observations": <n>, "first":
  <first date or null>, "last": <last date or null>, "file": <FILE>}`.
  For `cftc` there is also `"oi_file"`: the `--oi-file` path, or null. A
  source with no observations is still a success.

## Snapshot tables (v23)

A macro snapshot shows the analyst where the markets and the economy stood
on its date, as plain-text tables built from the stored series:
- where each target was and how it had moved;
- the economic data as published by then;
- how speculators were positioned;
- what was scheduled next.

The tables are deterministic: the same series give the same text byte for
byte, with no clock and nothing random. They can only see the past: every
function here ignores observations dated after the snapshot's date.
Assembling a snapshot from them comes in the next version.

The new module `researchos.snapshot` holds them. Its *observations* are
pairs `(datetime.date, number)` in any order, as `series.load_series`
returns them. An `as_of` is a date argument as v22 defines it (a
`datetime.date`, or a string `parse_iso_date` reads); anything else raises
ValueError. Every text a function returns is its lines joined by `"\n"`,
with a final `"\n"`.

```python
LOOKBACKS = ("1w", "1m", "3m", "1y")
```

### Changes and how they are written

- `trailing_change(observations, as_of, horizon, kind) -> float | None`:
  how far the level moved over `horizon` up to `as_of`, as
  `tools.finance.level_change` measures it for `kind`.
  - The end is the last observation on or before `as_of`
    (`tools.dates.last_on_or_before`).
  - The start is the last observation on or before
    `tools.dates.shift_date(as_of, -n, unit)`, where `(n, unit) =
    tools.dates.parse_horizon(horizon)`.
  - Returns None when either is missing, when they are the same
    observation (the same date: nothing was observed in between), or when
    `level_change` returns None.
  - `horizon` and `kind` are checked first, whatever the observations: a
    horizon `parse_horizon` rejects, or a kind not in `cases.KINDS`, raises
    ValueError.
- `format_level(value) -> str`: `f"{value:.2f}"`, or `"n/a"` when `value`
  is not a number (`tools.stats.is_number`).
- `format_change(change, kind) -> str`: `"n/a"` when `change` is not a
  number. Otherwise:
  - `"bp"`: `f"{change:+.0f} bp"`;
  - `"return"`: `f"{100 * change:+.1f}%"`;
  - `"points"`: `f"{change:+.2f}"`.

  So `12.0` in bp is `"+12 bp"`, `-0.0123` as a return is `"-1.2%"`, and
  `0.85` in points is `"+0.85"`. Another kind raises ValueError, whatever
  `change` is.

### The tables

Each table function returns an introduction (one line), an empty line, and
then a Markdown table: a header line, the line `|---|...|` with one `---`
per column, and one line per entry, in the order given. A line is the
cells, each with one space either side, between `|` characters:
`| a | b |`. Wherever a value is missing, the cell is `n/a`.

- `target_table(as_of, targets, series) -> str`: `targets` is a list of
  dicts with the keys `id`, `name`, `unit` and `kind`. `series` maps an id
  to its observations; an id it does not have has none.
  - The introduction: `Each level is the last observation on or before
    <as_of>; each change runs to it from the last observation on or before
    the date that far back.`
  - The header: `| Target | Level | On | 1w | 1m | 3m | 1y | 1y range | 1y
    percentile |`.
  - A row: `<name> (<id>, <unit>)`, then:
    - the level, `format_level` of the last observation on or before
      `as_of`, and its date (`On`);
    - `format_change(trailing_change(obs, as_of, L, kind), kind)` for each
      `L` in `LOOKBACKS`;
    - the range, `<low> to <high>`, from `format_level` of the smallest and
      largest values among the observations dated after
      `shift_date(as_of, -1, "y")` and on or before `as_of`;
    - the percentile, `str(round(100 * tools.stats.percentile_rank(level,
      values)))` over those same values.
  - With no level, every cell after the name is `n/a`; with no value in
    the year, so are the range and the percentile.
- `context_table(as_of, entries, series, published) -> str`: `entries` is a
  list of dicts with the keys `id`, `name` and `unit`. `series` is as
  above. `published` is a collection of the ids whose observations are the
  series as it was published by `as_of` (its vintage file).
  - The introduction: `Economic data, the latest observation on or before
    <as_of>.`
  - The header: `| Series | Latest | For | Previous | Change | A year
    earlier | Vintage |`.
  - A row: `<name> (<id>, <unit>)`, then:
    - the latest value, `format_level` of the last observation on or
      before `as_of`, and its date (`For`);
    - the previous value, the last observation before that date;
    - the change from the previous value to the latest,
      `format_change(latest - previous, "points")`;
    - the value a year earlier, the last observation on or before
      `shift_date(<the latest date>, -1, "y")`;
    - `as published by <as_of>` when the id is in `published`, and
      `current values` otherwise.
  - With no latest value, every cell after the name but the last is `n/a`.
- `positioning_table(as_of, entries, series) -> str`: `entries` is a list of
  dicts with the keys `id`, `name`, `net` (the id of the net-position
  series), `open_interest` (the id of the open-interest series, or None)
  and `release_lag_days`.
  - A report dated `D` became public `release_lag_days` days later, so only
    the reports of the `net` series dated on or before `as_of` minus that
    many days are used. `release_lag_days` must be an `int` that is not a
    `bool`, at least 0; anything else raises ValueError naming the entry's
    id.
  - The introduction: `Speculative positioning: non-commercial net
    contracts from the CFTC's Commitments of Traders, each report as it
    was public on <as_of>.`
  - The header: `| Contract | Net | Report of | Weekly change | 52-week
    percentile | Share of open interest |`.
  - A row: `<name> (<id>)`, then:
    - the net position of the latest report used, `f"{net:,.0f}"`, and its
      date;
    - the change from the report before it, `f"{change:+,.0f}"`;
    - `str(round(100 * percentile_rank(net, values)))` over the net
      positions of the reports used that are dated after
      `shift_date(<the latest date>, -52, "w")` and on or before that
      date;
    - the share of open interest, `f"{100 * net / oi:+.1f}%"`, where `oi`
      is the open interest observed on exactly the latest report's date.
      It is `n/a` when there is no such observation, when `oi` is 0, or
      when `open_interest` is None.
  - With no report, every cell after the name is `n/a`.
- `calendar_list(as_of, events, through) -> str`: `events` is a list of
  dicts with the keys `date` (an ISO date string) and `event`. `through`
  is a date argument.
  - The first line is `Scheduled after <as_of>, through <through>:`; then
    an empty line.
  - Then one line `- <date>: <event>` per event dated after `as_of` and on
    or before `through`, by date. Events on the same date keep their
    order. The date is written `YYYY-MM-DD`.
  - With no such event, the one line `- none listed`.
  - An event whose `date` is not a string `parse_iso_date` reads raises
    ValueError naming the event.

Every date written into a table is in the form `YYYY-MM-DD`, `<as_of>`
included.

## Building a snapshot (v24)

One configuration file describes every snapshot of a run. It lists:
- the targets and their horizons;
- the economic series and the positioning to show;
- the official texts to include, each with the date it was published;
- the calendar of scheduled events.

`snapshot` turns it into the macro case of one date, using only what was
known on that date:
- the series as stored by `fetch-series`, cut at the date (v22);
- for each kind of text, the latest one published by then;
- the events scheduled after it.

The tables of v23 are the case's first documents. No outcome is written:
the case's targets resolve later.

In `researchos.snapshot`:

- `load_macro_config(path) -> dict`: the configuration, read with
  `read_json`. It returns the config with these defaults filled in:
  - `context`, `positioning`, `documents` and `calendar` default to `[]`;
  - a target's `optional` defaults to `False`;
  - a positioning entry's `open_interest` defaults to `None`.

  It raises ValueError naming `path` when:
  - the file is not a JSON object;
  - `case_prefix` is not a `str` that matches `[A-Za-z0-9_-]+` as a whole;
  - `horizons` is not a non-empty list of valid horizons (as
    `cases.target_key` accepts them) without repeats;
  - `targets` is not a non-empty list of objects, each with:
    - an `id` that `cases.target_key` accepts, the ids all different;
    - a `name` and a `unit` that are strings;
    - a `kind` in `cases.KINDS`;
    - an `optional`, if given, that is a `bool`;
  - `context` is not a list of objects, each with an `id` that is a series
    id (v22) and a `name` and `unit` that are strings;
  - `positioning` is not a list of objects, each with:
    - an `id` and a `net` that are series ids;
    - a `name` that is a string;
    - an `open_interest` that is a series id or null;
    - a `release_lag_days` that is an `int`, not a `bool`, of at least 0;
  - `documents` is not a list of objects, each with:
    - a `kind` that is a non-empty string;
    - a `title` that is a string;
    - a `dated` that is a string `parse_iso_date` reads;
    - exactly one of `url` and `text`, which is a string;
  - `calendar` is not a list of objects, each with a `date` that is a string
    `parse_iso_date` reads and an `event` that is a string.

  Keys it does not name are ignored. Its OSError (a missing file) passes
  through.

- `build_snapshot(root, config, as_of, *, series_dir, fetch_page) -> dict`:
  builds and saves the macro case of `as_of` (a date argument) from a
  config as `load_macro_config` returns it. `fetch_page(url)` returns an
  object with a `text` attribute (`sources.get_page` with a user agent, in
  the CLI).

  1. The case id is `f"{config['case_prefix']}-{as_of}"`, with `as_of` in
     the form `YYYY-MM-DD`.
  2. When `<root>/<case_id>/prediction.json` or `outcome.json` exists, it
     raises ValueError naming the case before anything is read or written:
     a case is never rebuilt under a forecast that was already sealed or
     an outcome that was already written.
  3. Each target's series is `series.load_series(series_dir, id, as_of)`; a
     missing file (OSError) counts as no observations. A target whose
     series has no observation on or before `as_of` is missing.
     - A missing optional target is left out of the case, with a warning
       on the `researchos` logger naming it.
     - When any target that is not optional is missing, it raises
       ValueError naming every such target, before anything is written.
     - When every target is left out, it raises ValueError before anything
       is fetched or written: a macro case forecasts at least one target.
  4. Each other target becomes a `cases.Target` with:
     - `id`, `name` and `kind` from the config;
     - `horizons` a new list of the config's horizons;
     - `as_of_date` and `as_of_value` from the last observation on or before
       `as_of` (the date in the form `YYYY-MM-DD`);
     - `trailing` mapping each horizon `h` to `trailing_change(obs, as_of,
       h, kind)`.
  5. The documents, in this order, each with `source=""` unless said
     otherwise:
     - `Document("dashboard", f"Markets as of {as_of}",
       target_table(as_of, <the config entries of the targets kept>,
       <their series>))`;
     - when `context` is not empty, `Document("economy", f"Economic data as
       of {as_of}", context_table(as_of, context, <their series>,
       <published>))`, where each series is `load_series(series_dir, id,
       as_of)`, a missing file giving no observations, and `<published>`
       holds the ids whose file `<series_dir>/<id>@<as_of>.json` exists;
     - when `positioning` is not empty, `Document("positioning",
       f"Positioning as of {as_of}", positioning_table(as_of, positioning,
       <series>))`, where `<series>` maps every `net` and `open_interest` id
       to `load_series(series_dir, id, as_of)`, a missing file giving no
       observations;
     - when `calendar` is not empty, `Document("calendar", f"Calendar after
       {as_of}", calendar_list(as_of, calendar, <through>))`, where
       `<through>` is the latest `tools.dates.horizon_end(as_of, h)` over
       the config's horizons;
     - one document per `kind` of the config's `documents`, in the order in
       which the kinds first appear there. Of the entries of that kind dated
       on or before `as_of` it takes the one with the latest `dated` (of two
       on the same date, the later entry); a kind with no such entry gives
       no document. Its title is `f"{title} ({dated})"`, with `dated` in the
       form `YYYY-MM-DD`. With `text`, the document's text is that text.
       With `url`, its text is `fetch_page(url).text` and its `source` is
       the url. When `fetch_page` raises OSError or ValueError, the document
       is left out, with a warning naming the url. Nothing is fetched for an
       entry that is not taken.
  6. The case is `cases.macro_case(case_id, as_of, targets, documents)`. It
     is saved with `save_case(root, case)` (no outcome). Its manifest is
     saved with `save_resolution(root, case_id,
     resolution_manifest(case))`, nothing resolved.
  7. It returns `{"case_id": ..., "as_of": ..., "targets": [<ids kept>],
     "left_out": [<optional ids missing>], "documents": [<kind of each
     document, in order>], "keys": target_keys(case)}`.

  The same series, config and pages give the same files byte for byte.
  Building the case again before it is sealed or resolved rewrites it.

CLI:

```text
python -m researchos snapshot --root DIR --config FILE --as-of YYYY-MM-DD --series-dir DIR
                              [--user-agent TEXT] [--out FILE]
```

- It runs `load_macro_config(--config)`, then `build_snapshot` with
  `fetch_page = lambda url: sources.get_page(url, user_agent=...)`. The user
  agent is chosen as `build` chooses it.
- It emits the summary as every command does. No client is built.
- Exit 2 on bad arguments (`--as-of` not an ISO date, a missing option).
- Exit 1, with a message on stderr, on OSError or ValueError.

## Resolving a snapshot (v25)

A snapshot's targets resolve one horizon at a time, each when its horizon
is over. `resolve` reads each target's series as it stands now (fetched
again with `fetch-series`, v22). For each horizon that has ended it
records how far the target moved, from the level the case started from, in
`outcome.json`, and marks the key resolved in the manifest (v21). It never
opens a forecast: what a target did does not depend on what was forecast.

In `researchos.snapshot`:

- `resolve_cases(root, case_ids, *, series_dir) -> list[dict]`. Steps 1 and
  2 run for every case before step 3 writes anything, so a case that cannot
  be resolved stops the call with nothing written.

  1. **Checks.** For each id of `case_ids`, in order:
     - an id that appears a second time raises ValueError naming it;
     - the case is `cases.load_case(root, id)`; its OSError (no such case)
       passes through;
     - a case whose `domain` is not `"macro"` raises ValueError naming it;
     - `cases.resolution_manifest(case)` is computed, which checks the
       case's targets and date; its ValueError passes through;
     - the outcome is `cases.load_outcome(root, id)` when
       `<root>/<id>/outcome.json` exists, and `cases.Outcome()` when it does
       not; the errors of `load_outcome` pass through. A key of the
       outcome's `targets` that is not one of `cases.target_keys(case)`
       raises ValueError naming the case and the key.

     `<root>/<id>/prediction.json` is never opened.
  2. **States.** Each key of `target_keys(case)` -- target `t`, horizon
     `h` -- is in exactly one of three states: resolved, pending or
     unmeasurable.
     - A key in the outcome's `targets` already is resolved and kept
       exactly as it is, even when the series has changed since. Its
       series is not read for it.
     - For any other key, `obs = series.load_series(series_dir, t.id)`: the
       current file, not cut at any date (a missing file, OSError, gives no
       observations; a malformed one raises ValueError, which passes
       through). `end_day = tools.dates.horizon_end(as_of, h)`, where
       `as_of` is the case's `event_date`.
       - The key is **pending** when `tools.dates.first_on_or_after(obs,
         end_day)` is None: the series has nothing on or after the end of
         the horizon yet, so a later observation could still fall inside
         it. A horizon that ends on a weekend or a holiday waits for the
         next observation.
       - Otherwise `end = tools.dates.last_on_or_before(obs, end_day)`.
         The key is **unmeasurable** when `end` is None or its date is
         before the date `t.as_of_date` names (the file no longer holds the
         window), or when `change = tools.finance.level_change(
         t.as_of_value, <end's value>, t.kind)` is None (a return from a
         level at or below zero). An unmeasurable key is not resolved; it
         may resolve later, once the file holds the window again.
       - Otherwise the key is **newly resolved** with
         `cases.TargetOutcome(target=t.id, horizon=h, kind=t.kind,
         start_date=t.as_of_date, start=t.as_of_value, end_date=<end's
         date, YYYY-MM-DD>, end=<end's value>, change=change, up=change >
         0)`. The start is the level the case recorded -- the level its
         forecast started from -- not what the series now holds for that
         date.
  3. **Files**, case by case, in order:
     - when the case has at least one resolved key,
       `cases.save_outcome(root, id, dataclasses.replace(outcome,
       targets=...))`, where `targets` maps every resolved key, in the
       order of `target_keys(case)`, to its `TargetOutcome`; the outcome's
       other fields are kept. When it has none, no `outcome.json` is
       written;
     - `cases.save_resolution(root, id, cases.resolution_manifest(case,
       <the resolved keys>))`, whether or not any key is resolved.
  4. It returns one dict per case, in the order of `case_ids`:

     ```json
     {"case_id": "macro-2026-10-30",
      "resolved": ["DGS10_1w", "SP500_1w"], "new": ["SP500_1w"],
      "pending": ["DGS10_1m", "SP500_1m"], "unmeasurable": []}
     ```

     Each list holds keys in the order of `target_keys(case)`. `resolved`,
     `pending` and `unmeasurable` together hold every key once; `new` holds
     the keys this call resolved, which are also in `resolved`.

  Resolving again with the same series writes the same bytes and reports
  no new key. Once a key has an outcome it never changes. After a call,
  `cases.unresolved_targets(case, cases.load_resolution(root, id))` holds
  the keys of `pending` and `unmeasurable`, in key order.
- `resolve_case(root, case_id, *, series_dir) -> dict`:
  `resolve_cases(root, [case_id], series_dir=series_dir)[0]`.

CLI:

```text
python -m researchos resolve --root DIR --ids ID[,ID...] --series-dir DIR [--out FILE]
```

- `--ids` is read as `train` and `evaluate` read it: comma-separated, each
  id stripped, blanks dropped. When no id is left, it exits 2 with a
  message on stderr.
- It runs `resolve_cases(--root, <the ids>, series_dir=--series-dir)` and
  emits `{"cases": <its list>}` as every command does. No client is built.
- Exit 2 on bad arguments (a missing option, no id).
- Exit 1, with a message on stderr, on OSError or ValueError; a case that
  fails its checks stops the command before anything is written.

## Many targets, one analysis (v26)

A macro snapshot is analyzed the way an earnings report is (v20): one call
per perspective, then a judge. Each call covers every target and horizon of
the case at once: a perspective's reply holds one probability per target
key (`target_key`, v21), the probability that the target's level is higher
at the end of the horizon than the level the case starts from. The
perspectives are the themes a macro portfolio manager weighs, not the
earnings report's. The prompts never show the case id, an outcome or the
manifest. This version adds the analysis; nothing calls it yet.

### In the new module `researchos.targets`

```python
MACRO_PERSPECTIVES = ["growth", "inflation", "labor", "monetary_policy", "fiscal_supply",
                      "liquidity_conditions", "positioning_sentiment", "global_fx",
                      "commodities_energy", "politics_geopolitics"]
PERSPECTIVE_SETS = {"earnings": PERSPECTIVES, "macro": MACRO_PERSPECTIVES}
```

(`PERSPECTIVES` is `researchos.perspectives.PERSPECTIVES`, which does not
change.)

- `perspective_set(domain) -> list[str]`: a new list of the perspectives of
  `domain`, a key of `PERSPECTIVE_SETS`; any other value raises ValueError
  naming it.

`MACRO_PERSPECTIVE_FOCUS` maps each macro perspective to what it looks at,
verbatim:

| Perspective | Focus |
|---|---|
| `growth` | `real activity: GDP and its nowcasts, industrial production, retail sales, housing, surveys of businesses and consumers, and whether growth is speeding up or slowing down.` |
| `inflation` | `prices: consumer, producer and import prices, their core and services parts, inflation expectations and breakevens, and wages as a source of inflation.` |
| `labor` | `the labor market: payrolls, unemployment, jobless claims, job openings, participation and wage growth, and whether it is tightening or loosening.` |
| `monetary_policy` | `the Federal Reserve and other central banks: their statements, projections and speeches, how they react to the data, and what the market prices for the path of policy rates against what they are likely to do.` |
| `fiscal_supply` | `fiscal policy and the supply of government debt: deficits, Treasury issuance and refunding, auctions, the Treasury's cash balance and the term premium.` |
| `liquidity_conditions` | `liquidity and financial conditions: the Fed's balance sheet, bank reserves, money-market rates and funding stress, credit spreads, bank lending and the dollar.` |
| `positioning_sentiment` | `positioning and sentiment: speculative futures positions and how they changed, how crowded a trade is, and volatility and the appetite for risk it implies.` |
| `global_fx` | `the rest of the world: growth and policy in the euro area, Japan, China and the UK, capital flows and exchange rates, and how they reach US markets.` |
| `commodities_energy` | `commodities: the supply, demand and inventories of oil and natural gas, OPEC, metals, and how they pass through to inflation and growth.` |
| `politics_geopolitics` | `politics and geopolitics: elections, legislation, trade policy and tariffs, sanctions, conflicts and other shocks that can move markets.` |

A perspective that is not a key of `MACRO_PERSPECTIVE_FOCUS` looks at its
name with each `_` replaced by a space.

The system prompts, verbatim, each one paragraph:

`MACRO_ANALYST_SYSTEM`:

```text
You are a macro strategist at a hedge fund. You see a snapshot of the markets and the economy as of the close of a given date: the targets you forecast with their levels and recent changes, economic data, positioning, the calendar of scheduled events and official texts. Assume nothing about what happened after that date; reason only from the documents. Analyze through the single perspective you are given and, for each target and horizon, estimate the probability that the target's level is higher at the end of the horizon than the level the snapshot starts from. What the market already prices is the baseline: 0.50 means no edge over the market.
```

`MACRO_JUDGE_SYSTEM`:

```text
You are the portfolio manager. Several strategists have each judged a snapshot of the markets and the economy, as of the close of a given date, from one perspective. Assume nothing about what happened after that date. Weigh their views against the documents and the playbook and give, for each target and horizon, your own probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, with a short thesis. 0.50 means no edge over the market.
```

`MACRO_ISOLATED_ANALYST_SYSTEM`:

```text
You are a macro strategist at a hedge fund. You see a snapshot of the markets and the economy as of the close of a given date: the targets you forecast with their levels and recent changes, economic data, positioning, the calendar of scheduled events and official texts. Assume nothing about what happened after that date; reason only from the documents. You judge one factor in isolation. Assume that everything outside your factor stays as the market already prices it, and estimate, for each target and horizon, the probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, from your factor alone. 0.50 means your factor gives no signal either way; move away from 0.50 only as far as this factor by itself would move the target. Do not give an overall view: another strategist combines the factors.
```

`MACRO_ISOLATED_JUDGE_SYSTEM`:

```text
You are the portfolio manager. Several strategists have each judged one factor of a snapshot of the markets and the economy in isolation, as of the close of a given date, each as if everything else stayed as the market already prices it. Assume nothing about what happened after that date. Their probabilities are evidence from different factors: they add up rather than average, and a view at 0.50 is a factor that gives no signal. Weigh them against the documents and the playbook and give, for each target and horizon, your own probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, with a short thesis.
```

The instructions, verbatim, each one line:

`MACRO_INSTRUCTION`:

```text
Judge the snapshot above only from this perspective. For each target and horizon give the probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, and the key points behind your view.
```

`MACRO_ISOLATED_INSTRUCTION`:

```text
Judge only this factor, as if everything else stayed as the market already prices it. For each target and horizon give the probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, from this factor alone (0.50 if it gives no signal), and the key points behind your view.
```

#### Schemas

Every object in these schemas lists all its properties as `required`, in
the order of `properties`, and has `"additionalProperties": False` (the
structured-output rules of `llm.py`).

- `forecasts_schema(targets) -> dict`: `{"type": "object", "properties":
  {<id>: <target schema>, ...}, "required": [<ids>], "additionalProperties":
  False}`, one property per target in the order of `targets`. A target's
  schema is `{"type": "object", "properties": {"prob_up_<h>": {"type":
  "number"}, ...}, "required": [...], "additionalProperties": False}`, one
  property per horizon of the target, in its order. `cases.check_targets(
  targets)` runs first, and its ValueError passes through (no targets, a
  repeated or invalid id, an invalid horizon, ...).
- `view_schema(targets)`: an object with the properties `forecasts`
  (`forecasts_schema(targets)`) and `key_points` (`{"type": "array",
  "items": {"type": "string"}}`), in that order.
- `judge_schema(targets)`: an object with the properties `forecasts`
  (`forecasts_schema(targets)`) and `thesis` (`{"type": "string"}`), in
  that order.

#### Results

```python
@dataclass
class TargetView:
    probs: dict[str, float]   # target key -> P(up), clamped to [0.01, 0.99], in key order
    key_points: list[str]

@dataclass
class TargetAnalysis:
    case_id: str
    mode: str                          # "holistic" | "isolated"
    perspectives: list[str]
    keys: list[str]                    # cases.target_keys(case)
    views: dict[str, TargetView]       # one per perspective, in its order
    prob_weighted: dict[str, float]    # key -> the views combined, in key order
    prob_judge: dict[str, float]       # key -> the judge's probability, in key order
    thesis: str
    model: str = ""
    knowledge_cutoff: str = ""
```

- `target_analysis_from_dict(data) -> TargetAnalysis`: the inverse of
  `dataclasses.asdict`: every field is required except `model` and
  `knowledge_cutoff` (default `""`), a missing one raising KeyError; each
  entry of `views` becomes a `TargetView`; lists and dicts are new
  objects.

#### `analyze_targets(masked, case, playbook, analyst, judge, *, perspectives=MACRO_PERSPECTIVES, mode="holistic") -> TargetAnalysis`

`masked` is what the analysts read (`masking.unmasked(case)` for a macro
case); `case` gives the targets and the date. Before any call it raises
ValueError when:
- `mode` is not in `perspectives.MODES` (`check_mode`);
- `case.domain` is not `"macro"` (naming the case);
- `perspectives` is not a non-empty list of distinct strings;
- `cases.target_keys(case)` fails (its ValueError passes through).

Then, with `as_of` the case's `event_date`:

1. The shared prefix of every call is `f"Macro snapshot as of {as_of}:\n\n"`
   followed by `render_documents(masked.documents)`.
2. The targets block is `Targets:` and then one line per target of the
   case, in order: `- <id>: <name>; <measure>; from <level> on
   <as_of_date>; horizons <h1>, <h2>, ...`, where `<measure>` is `change in
   basis points` for kind `"bp"`, `simple return` for `"return"` and
   `change in points` for `"points"`, and `<level>` is
   `snapshot.format_level(as_of_value)`. The lines are joined with `\n`.
3. One analyst call per perspective, in order: `analyst.json(system,
   prompt, view_schema(case.targets), prefix=prefix)`. In mode
   `"holistic"`, `system` is `MACRO_ANALYST_SYSTEM` and `prompt` is:

   ```text
   Perspective: <name>
   Look at <focus>
   <MACRO_INSTRUCTION>

   <targets block>

   <render_for_prompt(playbook)>
   ```

   In mode `"isolated"`, `system` is `MACRO_ISOLATED_ANALYST_SYSTEM`, the
   second line is `Your factor: <focus>` and the third
   `MACRO_ISOLATED_INSTRUCTION`; the rest is the same.

   Each reply is read before the next call: it must be a dict whose
   `forecasts` is a dict holding, for each target id, a dict with a
   `prob_up_<h>` for each of its horizons. Each probability goes through
   `perspectives.clamp_prob` (a `bool`, a non-number or NaN raises
   LLMError, as does a missing one). A reply that is not a dict, a
   `forecasts` that is not a dict, or a target that is missing or not a
   dict raises LLMError naming the perspective and, when there is one, the
   target; an LLMError about a probability names the perspective and the
   key. Keys the schema does not ask for are ignored. `key_points` is
   its strings, in order, when it is a list, and `[]` otherwise.
4. The views are combined key by key, each over `{name:
   view.probs[key]}`:
   - `"holistic"`: `tools.stats.combine_probabilities` with the
     playbook's weights; when that is None, the plain mean of the views'
     probabilities;
   - `"isolated"`: `tools.stats.pool_log_odds` with the playbook's
     weights; when that is None, `pool_log_odds` with no weights; then
     clamped to [0.01, 0.99].
5. One judge call: `judge.json(system, prompt, judge_schema(case.targets),
   prefix=prefix)`, `system` being `MACRO_JUDGE_SYSTEM` or, in mode
   `"isolated"`, `MACRO_ISOLATED_JUDGE_SYSTEM`. The prompt is these parts
   joined by an empty line (`\n\n`):
   - `The strategists' views:`;
   - one part per perspective, in order: `### <name>`, then a line
     `P(up): <key> <p>, <key> <p>, ...` over every key in order, `<p>` to
     two decimals, then one line `- <point>` per key point;
   - in mode `"holistic"` `Playbook-weighted probabilities of the views:`,
     in mode `"isolated"` `Evidence-pooled probabilities of the views
     (their log-odds added):`, followed on the next lines by `- <key>:
     <p>` for every key, `<p>` to three decimals;
   - the targets block;
   - `render_for_prompt(playbook)`;
   - `Give, for each target and horizon, your own probability that the
     target's level is higher at the end of the horizon than the level the
     snapshot starts from, and your thesis.`

   The lines within a part are joined with `\n`. The judge's reply is
   read as the analysts' are (LLMError naming `judge`); its `thesis` is
   the reply's when it is a string, and `""` otherwise.
6. It returns `TargetAnalysis(case_id=case.case_id, mode=mode,
   perspectives=<a new list of perspectives>, keys=target_keys(case),
   views=..., prob_weighted=..., prob_judge=..., thesis=...)`, `model` and
   `knowledge_cutoff` left empty.

No file is read or written, and the case id appears in no system prompt,
prompt or prefix.

## Scoring and reflecting on many targets (v27)

Once some of a snapshot's targets have resolved (v25), its sealed analysis
(v26) can be scored and reflected on. A record is written per target key,
not per case. Its `case_id` is `<case id>/<key>`, and it has the keys an
earnings record has (`actual_up`, `prob_by_perspective`, `brier_weighted`,
`brier_judge`, `brier_by_perspective`, ...), so `compare`, `blend` and
`agreement` read it as they read an earnings record. It also keeps the
combined probabilities themselves and what the target did. Nothing calls
these yet.

In `researchos.targets`:

- `score_targets(analysis, outcome, case) -> list[dict]`: one record per
  key of `analysis.keys`, in that order, that has a `TargetOutcome` in
  `outcome.targets`; a key without one is not scored yet and gives no
  record. It raises ValueError when `analysis.case_id` is not
  `case.case_id`, or when `analysis.keys` is not `cases.target_keys(case)`
  (the analysis is of another case, or of other targets). With `t` the
  case's target and `h` the horizon of the key, `o` its `TargetOutcome`,
  `y` 1 when `o.up` and 0 otherwise, and `brier(p)` being
  `tools.stats.brier_score([p], [y])`, the record has these keys, in this
  order:

  | Key | Value |
  |---|---|
  | `case_id` | `f"{case.case_id}/{key}"` |
  | `case` | `case.case_id` |
  | `key`, `target`, `horizon`, `kind` | the key, `t.id`, `h`, `t.kind` |
  | `event_date` | `case.event_date` |
  | `resolution_date` | `tools.dates.horizon_end(<event_date>, h)`, as `YYYY-MM-DD` |
  | `start_date`, `start`, `end_date`, `end`, `change` | from `o` |
  | `trailing_change` | `t.trailing.get(h)` (None when it has none) |
  | `actual_up` | `o.up` |
  | `prob_weighted`, `prob_judge` | `analysis.prob_weighted[key]`, `analysis.prob_judge[key]` |
  | `prob_uniform` | the plain mean of the perspectives' probabilities for the key |
  | `prob_by_perspective` | `{name: analysis.views[name].probs[key]}`, in the order of `analysis.perspectives` |
  | `brier_weighted`, `brier_judge`, `brier_uniform` | `brier` of the three probabilities above |
  | `brier_coin` | `0.25` |
  | `brier_by_perspective` | `{name: brier(<its probability>)}`, in the same order |
  | `correct_weighted`, `correct_judge` | `(prob_weighted > 0.5) == o.up`, and the same for `prob_judge` |

  The record holds no text from the documents or the analysis, so it can
  be shared without them.

- `MACRO_REFLECTOR_SYSTEM`, verbatim, one paragraph:

  ```text
  You are the reflector in a research pipeline that trains macro strategists to forecast where market levels go in the weeks and months after a snapshot of the markets and the economy. The forecasts were sealed before the outcomes were known; now you see both. Explain why each target moved the way it did, where the forecasts went wrong or right, and write lessons that would hold on other dates. A lesson must not make a rule of a particular date, level or event: it is shown to the strategists on every later snapshot. Each lesson says when it applies and which perspective it belongs to.
  ```

- `reflect_targets(case, analysis, outcome, reflector, *, keys=None) ->
  list[Lesson]` (`researchos.playbook.Lesson`): one reflector call after
  some of the case's targets have resolved.
  - `keys` are the keys to reflect on; None means every key of
    `analysis.keys` that has a `TargetOutcome` in `outcome.targets`, in
    that order. A given `keys` is taken in the order of `analysis.keys`,
    each once; a key that is not in `analysis.keys`, or has no
    `TargetOutcome`, raises ValueError naming it, before any call. The same
    checks of the analysis against the case as `score_targets` makes come
    first.
  - With no key to reflect on, it returns `[]` and makes no call.
  - The call is `reflector.json(MACRO_REFLECTOR_SYSTEM, prompt,
    reflect.REFLECT_SCHEMA)`, with no prefix. The prompt is these parts
    joined by an empty line (`\n\n`), the lines within a part joined with
    `\n`:
    - `Snapshot as of <event_date>: the sealed forecasts and what happened.`
    - one part per key, in order:
      - `### <key> (<target name>, <horizon>)`
      - `Perspectives: <name> <p>, <name> <p>, ...`, every perspective of
        `analysis.perspectives` in order, `<p>` to two decimals;
      - `Combined: <prob_weighted>; judge: <prob_judge>`, each to three
        decimals;
      - `Outcome: from <start> on <start_date> to <end> on <end_date>,
        <change>: <up or not up>`, with `<start>` and `<end>` through
        `snapshot.format_level`, `<change>` through
        `snapshot.format_change(o.change, o.kind)`, and `up` when `o.up`,
        `not up` otherwise;
    - `Judge's thesis: <analysis.thesis>`
    - `Documents` followed, after an empty line, by
      `render_documents(case.documents)`;
    - `Why did these targets move the way they did? Where were the forecasts wrong, and where right? Write lessons that would hold on other dates; never make a rule of a particular date, level or event.`
    - `Each lesson's perspective is one of: <name>, <name>, ..., or "general".`,
      listing `analysis.perspectives` in order.
  - The reply is read as `reflect.reflect` reads its own: each item of
    `lessons` that is a dict with a `lesson` that is a non-empty string
    once stripped becomes `Lesson(text=<lesson, stripped>,
    applies_when=<applies_when, stripped, or "" when it is not a string>,
    perspective=<perspective, stripped and lower-cased, or "general" when
    it is not one of analysis.perspectives>, case_id=case.case_id)`, in
    order. Anything else in the reply is ignored; a reply that is not a
    dict, or has no list `lessons`, gives `[]`.

  No file is read or written.

## Forecast now, score later (v28)

`train` and `evaluate` open a case's outcome as soon as its forecast is
sealed. An earnings reaction is known the day it happens, but a macro
snapshot's targets resolve weeks or months after its date (v25). This
version separates the two halves. `forecast` seals forecasts and opens no
outcome. `score_sealed` scores what was sealed against what has resolved
since, and calls no model. Both take earnings cases as well as macro ones.
The playbook is read but never changed: learning from macro outcomes, walk
forward by the date each one resolves, comes later. Until then `train` and
`evaluate` refuse a macro case.

### In `researchos.cases`

- `PREDICTION_FILE = "prediction.json"` moves here from `researchos.loop`.
  `loop` imports it from `cases`, so `loop.PREDICTION_FILE` is unchanged.
  `snapshot` now imports it from `cases` too, so that `loop` can import
  `targets` (which uses `snapshot`) without an import cycle.

### In `researchos.playbook`

- `Playbook.initial(perspectives=None)`: the weights are `1 / n` for each
  of the `n` names of `perspectives`, in order (`PERSPECTIVES` when it is
  None), with no lessons and no history. `Playbook.initial()` is unchanged.

### In `researchos.loop`

- `train` and `evaluate` refuse a macro case. When any requested case has
  domain `"macro"`, they raise ValueError naming it before anything is
  masked, called or written: its outcome is not known on its date. Such a
  case is forecast with `forecast` and scored with `score_sealed`.
  Everything else they do is unchanged.

- `forecast(root, case_ids, playbook_path, roles, seed=0, mask_config=MaskConfig(), knowledge_cutoff=None, perspective_mode="holistic") -> list[dict]`
  seals a forecast for each case and returns what it forecast. It checks
  the following, in order, before anything is masked, called or written.
  The first check that fails raises, and each step is made for every case
  before the next step. With no case id it returns `[]` after step 1,
  having read nothing:
  1. `check_mode(perspective_mode)` and `parse_cutoff(knowledge_cutoff)`
     (their ValueError passes through).
  2. An id given more than once raises ValueError naming it.
  3. `load_case` of each id, in order (its OSError passes through).
  4. `single_domain` of the cases (its ValueError passes through). Call its
     result the run's domain.
  5. The playbook is `load_playbook(playbook_path)` when that file exists,
     and `Playbook.initial(targets.perspective_set(<domain>))` otherwise.
     A loaded playbook whose weights do not name exactly the domain's
     perspectives (as a set) raises ValueError naming the file and the
     domain: the playbook belongs to another domain.
  6. The refusals `train` makes for each case's date: an event date that
     is not an ISO date, or one on or before `learned_through(playbook)`.
     The ValueError names every such case, and its message begins
     `forecast refused: `.
  7. In a macro run, `require_after_cutoff(case_id, case,
     knowledge_cutoff)` for each case in order.
  8. A case that already has `<root>/<case_id>/prediction.json` raises
     ValueError naming it. A forecast is sealed once and never replaced.

  Then each case is forecast and sealed in the order given:
  - **An earnings case** is masked, skipped or analyzed, and sealed as
    `evaluate` does it, with the split `"forecast"`.
    - Masking reuses `masked.json`, or calls `mask_case` with
      `random.Random(f"{seed}:{case_id}")` and saves the result.
    - A case whose masking ends `"identifiable"` is skipped, unless its
      event date is after the knowledge cutoff.
    - Otherwise it is analyzed with `perspectives.analyze` in
      `perspective_mode`, on the playbook without the lessons that name
      the case, and sealed as `prediction.json`.

    Its entry is `{"case_id", "event_date", "domain": "earnings",
    "sealed": True, "prob_weighted", "prob_judge", "prob_by_perspective":
    {name: view.prob_up}, "identifiable": <masking ended
    "identifiable">}`. A skipped case's entry is `{"case_id",
    "event_date", "domain": "earnings", "sealed": False, "skipped":
    "identifiable"}`.
  - **A macro case** is analyzed with
    `targets.analyze_targets(masking.unmasked(case), case, playbook,
    roles.analyst, roles.judge, perspectives=perspective_set("macro"),
    mode=perspective_mode)`.
    - The result's `model` is `roles.analyst.model` when the analyst has a
      `model` attribute that is a `str`, and `""` otherwise. Its
      `knowledge_cutoff` is the cutoff as `YYYY-MM-DD`.
    - It is sealed as `prediction.json` (`dataclasses.asdict`).
    - No `masked.json` is read or written.

    Its entry is `{"case_id", "event_date", "domain": "macro", "sealed":
    True, "mode": perspective_mode, "model", "keys", "prob_weighted",
    "prob_judge", "prob_by_perspective": {name: view.probs}}`, the last
    four taken from the analysis.

  The playbook is never written or changed. No `outcome.json` and no
  `resolution.json` is opened. A failure part way, such as an LLMError,
  leaves the forecasts sealed before it. Run `forecast` again with the
  other cases: step 8 refuses the sealed ones.

- `load_prediction(root, case_id) -> Analysis | TargetAnalysis`: reads
  `<root>/<case_id>/prediction.json` with `jsonio.read_json` (a missing
  file's OSError passes through).
  - A value that is not a dict raises ValueError.
  - A dict with the key `"keys"` is a macro forecast, read with
    `targets.target_analysis_from_dict`.
  - Any other dict is an earnings forecast: `Analysis(case_id,
    views={name: View(stance, prob_up, conviction, key_points)},
    prob_weighted, prob_judge, thesis)`, every field from the file (the
    `key_points` a new list) and the views in the file's order. A missing
    field raises KeyError.

- `score_sealed(root, case_ids) -> dict` scores the sealed forecasts
  against whatever has resolved. It calls no model and writes nothing.
  First, in order (the first check that fails raises):
  - an id given more than once raises ValueError;
  - `load_case` of each id (its OSError passes through);
  - `single_domain` of the cases;
  - a case without `prediction.json` raises ValueError naming it (it was
    never forecast);
  - `load_prediction`. A forecast whose `case_id` is not the case's raises
    ValueError naming both. A forecast of the other kind raises ValueError
    naming the case: a macro case needs a `TargetAnalysis`, an earnings
    case an `Analysis`.

  Each check is made for every case before the next check. A case that
  `forecast` skipped has no `prediction.json`, so leave it out.

  Then case by case, in the order given:
  - **A macro case.** Its outcome is `load_outcome` when `outcome.json`
    exists, and `Outcome()` otherwise. Its records are
    `targets.score_targets(analysis, outcome, case)`. Every key of
    `analysis.keys` that has no `TargetOutcome` is pending, as
    `f"{case_id}/{key}"`.
  - **An earnings case** without `outcome.json` is pending, as `case_id`.
    Otherwise its record is `{"case_id", "event_date",
    **score(analysis, outcome), "brier_uniform"}`. `brier_uniform` is the
    Brier score of the plain mean of the views' `prob_up`.

  It returns `{"cases": <the records>, "n", "pending", "brier_weighted",
  "brier_judge", "brier_uniform", "brier_coin", "accuracy_weighted",
  "accuracy_judge"}`, in that order. `n` is the number of records and
  `pending` lists the pending entries in order. The means are taken as
  `evaluate` takes them: over the records, and None when there is none.
  `compare` reads the result as it reads `evaluate`'s report.

### CLI

- `python -m researchos forecast` takes the arguments of `train` and
  `evaluate` and runs `loop.forecast`, passing on only the options that
  were given, as `train` does. It prints `{"cases": <the list>}`. It exits
  as `train` does: 2 with no id; 1 for a case that is not found, before
  any client is built; 1 for an LLMError, a FileNotFoundError or a
  ValueError, with the reason on stderr.
- `python -m researchos score --root DIR --ids ID[,ID...] [--out FILE]`
  prints `loop.score_sealed` of the cases. It builds no client. It exits 2
  with no id, and 1 for an OSError, a ValueError or a KeyError, with the
  reason on stderr.

## Blend and agreement over the macro perspectives (v29)

`score_sealed` (v28) writes macro records that `blend` and `agreement`
could read, except for two things. The records hold the ten macro
perspectives, while both functions expect the seven of `PERSPECTIVES`. And
a macro record is not known on its event date: a 1-month view of a
snapshot is known a month later. A replay that learned from it after the
snapshot's own batch would let the next week's forecasts use an outcome
that nobody had yet. This version passes the perspectives in, and replays
by the date each record resolved. With neither, every result is what it
was before, to the bit.

In `researchos.blend`:

- `replay(records, rule="hedge", *, eta=0.5, perspectives=None)` and
  `agreement(records, *, perspectives=None)` take the perspectives to read.
  `perspectives` is None, meaning `PERSPECTIVES`, or a non-empty list of
  distinct strings. Anything else raises ValueError before any record is
  read.
  - Every rule, check and result that named `PERSPECTIVES` now names
    these perspectives, in their order.
  - A scored record needs a probability for each of them (the ValueError
    names the case and the missing perspectives). Other probabilities in
    the record are ignored.
  - The hedge weights start at `1 / n` over the `n` perspectives.
  - `leader` breaks a tie in favor of the first perspective in this order.
  - `uniform`, `median` and `pool` are taken over these perspectives.
  - `agreement` gives one entry per perspective in this order, and the
    `n (n - 1) / 2` pairs a-before-b in this order.

- **Resolution dates.** A scored record's resolution date is its
  `resolution_date`, a `str` that `parse_iso_date` reads, when the record
  has that key, and its event date when it does not. A value that is not
  such a string, or a date before the record's event date, raises
  ValueError naming the case, with the other checks and before anything is
  replayed. `replay` still takes the records in event-date order (ties keep
  the order given) and forecasts them in batches of one event date `b`,
  each case of a batch with the state from before the batch. The state now
  learns from a record only once it has resolved:
  1. Before batch `b` is forecast, the state learns from every record not
     yet learned from whose resolution date is before `b`.
  2. After the batch is forecast, its records join those waiting, and the
     state learns from every waiting record whose resolution date is on or
     before `b`.

  Learning is what it was (a `hedge_update`, and the `leader` sums and
  count), one record at a time, in the order of their resolution dates.
  Records that resolve on the same day keep their replay order. When every
  record resolves on its event date, this is the rule of v11 exactly. A
  record that resolves after the last batch is never learned from, which
  changes no forecast. `agreement` takes no account of dates.

- A replayed case is `{"case_id", "event_date", "resolution_date",
  "actual_up", "prob", "brier_blend"}` when its record has a
  `resolution_date` (the date as `YYYY-MM-DD`), and is unchanged when it
  has none.

CLI: `blend` and `agreement` take `--perspectives {earnings,macro}`.
`targets.perspective_set` of that name is passed on as `perspectives`. It
is passed only when the option is given, so without it the call is
unchanged. A ValueError from `replay` or `agreement`, such as a macro
record read without `--perspectives macro`, exits 1 as before.

## Walk forward by resolution date (v30)

`train` learns as it goes: the playbook that forecasts a case has learned
from every earlier case. For an earnings case, "earlier" means an earlier
event date, because the reaction is known the same day. A macro snapshot's
targets resolve at the end of each horizon: a week, a month or three months
after its date. A forecast on date `b` may learn only from the outcomes
known by then. So a target key of a snapshot of date `a` with horizon `h`
is learned from only once its resolution date `r = horizon_end(a, h)` has
passed. This version teaches `train` to walk forward over macro snapshots
that way, in place of v28's refusal of a macro case. An earnings run is
unchanged, and `evaluate` still refuses a macro case (`forecast` and
`score_sealed` do its work).

The rule, for a run whose forecasts are made on the dates `b1 < b2 < ...`:
the forecasts of date `b` use exactly the keys with `r < b`. A key with
`r == b` is learned once the forecasts of `b` are sealed, as an earnings
case of date `b` is. When every `r` equals its snapshot's date, this is the
earnings rule exactly.

### In `researchos.playbook`

- `learned_through(playbook)`: the date of a history record is its
  `resolution_date` when that is present and not None, and its
  `event_date` otherwise. Everything else is unchanged: records with
  `"skipped"` do not count, a record with neither date is ignored, and a
  date that is present but is not an ISO date raises ValueError. Earnings records carry no
  `resolution_date`, so their result is unchanged.
- `update_weights_probs(playbook, probs, actual_up, eta=0.5) -> None`: the
  Hedge update of `update_weights`, from bare probabilities.
  `playbook.weights = hedge_update(playbook.weights, {name: (p - y) ** 2 for
  name, p in probs.items()}, eta)`, with `y` 1.0 when `actual_up` and 0.0
  otherwise. `update_weights` is unchanged.

### In `researchos.loop`

- `train(root, case_ids, playbook_path, roles, seed=0,
  mask_config=MaskConfig(), knowledge_cutoff=None,
  perspective_mode="holistic")` takes a macro run. It checks the following,
  in order, before anything is called or written. Each step is made for
  every case before the next step, and the first check that fails raises:
  1. `check_mode(perspective_mode)` and `parse_cutoff(knowledge_cutoff)`.
  2. `load_case` of each id, in order (its OSError passes through). When
     none of the cases is a macro case, the run is an earnings run and goes
     on exactly as before. Otherwise it is a macro run, and the remaining
     steps apply.
  3. An id given more than once raises ValueError naming it.
  4. `single_domain` of the cases: a macro case cannot share a run with an
     earnings case.
  5. The playbook is `load_playbook(playbook_path)` when that file exists,
     and `Playbook.initial(perspective_set("macro"))` otherwise. A loaded
     playbook whose weights do not name exactly the macro perspectives
     raises ValueError naming the file, as in `forecast`.
  6. The refusals `train` makes for each case's date: an event date that
     is not an ISO date, or one on or before `learned_through(playbook)`.
     The ValueError names every such case, and its message begins `train
     refused: `.
  7. `require_after_cutoff(case_id, case, knowledge_cutoff)` for each case.
  8. Every key of every case has resolved: `load_resolution` of the case
     must be a manifest, and `unresolved_targets(case, manifest)` must be
     empty. Otherwise ValueError names the case and its unresolved keys.
     Only `resolution.json` is read here: no `outcome.json` is opened.
  9. A case that already has `prediction.json` raises ValueError naming
     it. A macro forecast is sealed once.

  Then the cases are taken in event-date order (ties keep the order given),
  in batches of one event date `b`:
  1. Before the batch, the playbook learns from every waiting key whose
     resolution date is before `b`.
  2. Each case of the batch is forecast and sealed, in order, as `forecast`
     does it: `analyze_targets(masking.unmasked(case), case, playbook,
     roles.analyst, roles.judge, perspectives=perspective_set("macro"),
     mode=perspective_mode)` with the playbook as it stands. Its `model` and
     `knowledge_cutoff` are set as `forecast` sets them, and it is sealed
     as `prediction.json`.
  3. The batch's keys join the waiting keys, case by case and each case's
     keys in `target_keys` order. The playbook then learns from every
     waiting key whose resolution date is on or before `b`.

  After the last batch, it learns from the keys still waiting. Waiting
  keys are learned from one at a time, in the order of their resolution
  dates. Keys that resolve on the same day keep the order they joined in.
  Learning from a key `k` of case `c` goes like this:
  - The first time a key of `c` is learned from, its outcome is
    `load_outcome(root, c)`. This happens only after `c` is sealed.
  - The record is the entry for `k` in `targets.score_targets(analysis,
    outcome, case)`. When the outcome has no `TargetOutcome` for `k`
    (`outcome.json` disagrees with the manifest), ValueError names the case
    and the key.
  - `update_weights_probs(playbook, record["prob_by_perspective"],
    record["actual_up"])`.
  - `{"case_id": record["case_id"], "split": "train", ...}`, followed by
    every other key of the record in its order, is appended to
    `playbook.history`. The record already holds `event_date` and
    `resolution_date`.
  - When `k` is the last key of `c` with its horizon to be learned from,
    the horizon's keys are reflected on with one call:
    `targets.reflect_targets(case, analysis, outcome, roles.reflector,
    keys=<the case's keys of that horizon, in target_keys order>)`. The
    lessons are added with `add_lessons(playbook, lessons,
    identity_terms(case))`.
  - The playbook is saved.

  `train` returns the history records it appended, in that order. Masking
  plays no part in a macro run, so `seed` and `mask_config` are unused
  there and no `masked.json` is read or written.

  Because a key is learned from after the last batch however late it
  resolves, `learned_through` afterwards is the latest resolution date. A
  later `train` or `forecast` takes only snapshots after that date. To
  learn from a stretch of weekly snapshots, train on them in one call once
  all of their horizons have resolved.

- `evaluate` still refuses a macro case, as in v28.

### CLI

`python -m researchos train` takes macro ids, and prints what `train`
returns. A macro run without `--knowledge-cutoff` exits 1, with the reason
on stderr.

## The main text of a fetched page (v31)

A page that `snapshot` fetches by address (an FOMC statement, the minutes)
brings its site with it: the menus, the links to other releases, the
footer. On the Fed's pages that is about 8,300 characters a page, most of
a statement's text. Pages mark the content itself with a `main` element or
with `role="main"`. This version lets `get_page` keep only that element's
text, and `snapshot` asks for it. Every other caller, `build` among them,
gets a page's whole text as before.

### In `researchos.sources`

- `html_to_text(html, *, main_only=False) -> tuple[str, str]`. With
  `main_only` false, everything is as in v14. With `main_only` true, the
  title is as before, and the text is that of the page's main element
  when the page has exactly one:
  - A *main element* is a start tag whose text the page's text keeps (not
    `title`, not one of the dropped elements `script`, `style`, `noscript`,
    `head`, `svg` and `template`, and not inside one of them), and whose
    name is `main` or that has a `role` attribute with the value `"main"`
    after `str.strip()` and `str.lower()`. A `role` attribute written
    without a value (`<div role>`) is not `"main"`. Tag and attribute
    names are compared as `html.parser` reports them, in lower case.
  - The element runs from its start tag to the end tag that closes it.
    Counting from its start tag, each start tag of the same name adds one
    and each end tag of that name takes one away, counting only tags whose
    text the page keeps (an end tag inside a dropped element counts for
    nothing). The element ends at the end tag that brings the count to
    zero. If none does, it runs to the end of the page. A start tag
    written as self-closing (`<div role="main"/>`) ends at once, since
    `html.parser` reports it as a start tag and then an end tag.
  - Its text is the page's text between its start tag and that end tag,
    built by the same rules as the whole text (line breaks, table cells
    joined by " | ", `pre`, character references, runs of spaces, stripped
    lines, empty lines dropped). What its own start and end tags add is
    left out: an element that is a table cell does not begin with " | ".
  - When the page has no main element, or more than one (one inside
    another counts as two), or when its main element's text is empty, the
    text is the page's whole text, as with `main_only` false.
- `get_page(url, *, user_agent, transport=None, main_only=False) ->
  FetchedPage`: an HTML page becomes `FetchedPage(url,
  *html_to_text(body, main_only=main_only))`. Everything else is as in
  v14; plain text is kept whole whatever `main_only` is.
- `url_fetcher` is unchanged, so `build` reads whole pages.

### CLI

`snapshot` fetches with `fetch_page = lambda url: sources.get_page(url,
user_agent=..., main_only=True)`. Nothing else changes: `build_snapshot`
still takes `fetch_page`, and a page that cannot be fetched is still left
out with a warning.

A snapshot built before v31 holds each page's whole text, so the same
snapshot built again now has other bytes. A run uses its snapshots as they
were built; `runs/2026-11-macro-calibration-run` hashed its own in
`inputs.sha256`.
