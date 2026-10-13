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
- `is_identified(guesses, case, top_k) -> bool`: True if any of the first
  `top_k` guesses has a `company` whose `normalize_name` equals the
  `normalize_name` of `case.company` or of an alias (and is not empty), or a
  `ticker` equal to `case.ticker` ignoring case and surrounding spaces.
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
  round the status is `"identifiable"`. 3. Guard, applied to the returned
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
- `train(root, case_ids, playbook_path, roles, seed=0, mask_config=MaskConfig()) -> list[dict]`:
  for each case in order, with `rng = random.Random(f"{seed}:{case_id}")`:
  mask (reuse `masked.json` if it exists, else `mask_case` and save), analyze
  with the current playbook, **write `<root>/<case_id>/prediction.json`
  (the analysis) before calling `load_outcome`**, score, `update_weights`,
  `reflect`, `add_lessons` with the case's identity terms as forbidden terms,
  append the score (plus `case_id`, `split: "train"`, and the number of
  lessons rejected) to `history`, save the playbook. Returns the scores.
  A case whose masking ends `"identifiable"` is skipped (recorded in history
  with `"skipped": "identifiable"`), because its analysis could rely on
  recognizing the company.
- `evaluate(root, case_ids, playbook_path, roles, seed=0, mask_config=MaskConfig()) -> dict`:
  the same masking, analysis, sealing and scoring, but the playbook is loaded
  and **never written or changed**, and nothing is reflected. Returns
  `{"cases": [scores...], "n": count scored, "brier_weighted",
  "brier_judge", "brier_uniform", "brier_coin", "accuracy_weighted",
  "accuracy_judge"}` where `brier_uniform` combines the views with equal
  weights and `brier_coin` forecasts 0.5 every time; means over scored cases,
  None when no case was scored.

## researchos.__main__

`python -m researchos train --root DIR --playbook FILE --ids ID[,ID...] [--seed N]`
and `python -m researchos evaluate ...` (same arguments): build `Roles` from
`ClaudeJSON(anthropic.Anthropic())` (one instance per role; masker and
guesser at effort `"medium"`, the others at `"high"`), run, and print the
scores to stdout as JSON -- nothing else goes to stdout; messages go to
stderr. `main(argv=None, *, client_factory=None) -> int`: `client_factory` is
a zero-argument callable returning a client, `anthropic.Anthropic` when None.
Exit 0 on success, 2 on bad arguments, 1 on any `LLMError` or missing case
(with a message on stderr).
