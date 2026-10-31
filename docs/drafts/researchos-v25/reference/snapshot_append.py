

# ---- resolving a snapshot (v25 reference) ---------------------------------------------

import dataclasses as _v25_dataclasses
from pathlib import Path as _v25_Path

from tools.dates import first_on_or_after as _v25_first_on_or_after
from tools.dates import horizon_end as _v25_horizon_end
from tools.dates import last_on_or_before as _v25_last_on_or_before
from tools.dates import parse_iso_date as _v25_parse_iso_date
from tools.finance import level_change as _v25_level_change

from researchos import cases as _v25_cases
from researchos import series as _v25_series


def _v25_observations(series_dir, series_id):
    try:
        return _v25_series.load_series(series_dir, series_id)
    except OSError:
        return []


def _v25_check(root, case_ids):
    checked, seen = [], set()
    for case_id in case_ids:
        if case_id in seen:
            raise ValueError(f"case {case_id} is given twice")
        seen.add(case_id)
        case = _v25_cases.load_case(root, case_id)
        if case.domain != "macro":
            raise ValueError(f"case {case_id}: its domain is {case.domain!r}; only a macro "
                             f"case resolves")
        _v25_cases.resolution_manifest(case)
        keys = _v25_cases.target_keys(case)
        if (_v25_Path(root) / case_id / _v25_cases.OUTCOME_FILE).exists():
            outcome = _v25_cases.load_outcome(root, case_id)
        else:
            outcome = _v25_cases.Outcome()
        for key in outcome.targets:
            if key not in keys:
                raise ValueError(f"case {case_id}: outcome.json has the target key {key!r}, "
                                 f"which the case does not have")
        checked.append((case_id, case, outcome))
    return checked


def _v25_states(case, outcome, series_dir):
    as_of = _v25_parse_iso_date(case.event_date)
    done, new, pending, unmeasurable = {}, [], [], []
    for t in case.targets:
        obs = None
        for h in t.horizons:
            key = _v25_cases.target_key(t.id, h)
            if key in outcome.targets:
                done[key] = outcome.targets[key]
                continue
            if obs is None:
                obs = _v25_observations(series_dir, t.id)
            end_day = _v25_horizon_end(as_of, h)
            if _v25_first_on_or_after(obs, end_day) is None:
                pending.append(key)
                continue
            end = _v25_last_on_or_before(obs, end_day)
            change = None
            if end is not None and end[0] >= _v25_parse_iso_date(t.as_of_date):
                change = _v25_level_change(t.as_of_value, end[1], t.kind)
            if change is None:
                unmeasurable.append(key)
                continue
            done[key] = _v25_cases.TargetOutcome(
                target=t.id, horizon=h, kind=t.kind, start_date=t.as_of_date,
                start=t.as_of_value, end_date=end[0].isoformat(), end=end[1],
                change=change, up=change > 0)
            new.append(key)
    return done, new, pending, unmeasurable


def resolve_cases(root, case_ids, *, series_dir) -> list:
    checked = _v25_check(root, case_ids)
    planned = [(case_id, case, outcome, _v25_states(case, outcome, series_dir))
               for case_id, case, outcome in checked]
    results = []
    for case_id, case, outcome, (done, new, pending, unmeasurable) in planned:
        if done:
            _v25_cases.save_outcome(root, case_id,
                                    _v25_dataclasses.replace(outcome, targets=done))
        _v25_cases.save_resolution(root, case_id,
                                   _v25_cases.resolution_manifest(case, list(done)))
        results.append({"case_id": case_id, "resolved": list(done), "new": new,
                        "pending": pending, "unmeasurable": unmeasurable})
    return results


def resolve_case(root, case_id, *, series_dir) -> dict:
    return resolve_cases(root, [case_id], series_dir=series_dir)[0]
