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
