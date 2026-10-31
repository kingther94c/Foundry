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
