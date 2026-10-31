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
