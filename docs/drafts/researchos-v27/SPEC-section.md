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
