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
