

# ---- walk forward by resolution date (v30 reference) ----------------------------------

from dataclasses import asdict as _v30_asdict
from itertools import groupby as _v30_groupby
from pathlib import Path as _V30_Path

from tools.dates import horizon_end as _v30_horizon_end
from tools.dates import parse_iso_date as _v30_parse_iso_date

from researchos import cases as _v30_cases
from researchos import masking as _v30_masking
from researchos import playbook as _v30_playbook
from researchos import targets as _v30_targets
from researchos.jsonio import write_json as _v30_write_json
from researchos.masking import MaskConfig as _V30_MaskConfig
from researchos.perspectives import check_mode as _v30_check_mode

_v30_earnings_train = train


def train(root, case_ids, playbook_path, roles, seed=0, mask_config=_V30_MaskConfig(),
          knowledge_cutoff=None, perspective_mode="holistic"):
    _v30_check_mode(perspective_mode)
    cutoff = parse_cutoff(knowledge_cutoff)
    ids = list(case_ids)
    loaded = [(case_id, _v30_cases.load_case(root, case_id)) for case_id in ids]
    if not any(case.domain == "macro" for _, case in loaded):
        return _v30_earnings_train(root, ids, playbook_path, roles, seed=seed,
                                   mask_config=mask_config, knowledge_cutoff=knowledge_cutoff,
                                   perspective_mode=perspective_mode)
    seen = set()
    for case_id in ids:
        if case_id in seen:
            raise ValueError(f"case {case_id} is named twice")
        seen.add(case_id)
    single_domain(case for _, case in loaded)
    names = _v30_targets.perspective_set("macro")
    if _V30_Path(playbook_path).is_file():
        book = _v30_playbook.load_playbook(playbook_path)
        if set(book.weights) != set(names):
            raise ValueError(f"the playbook {playbook_path} does not weigh the macro "
                             f"perspectives; it belongs to another domain")
    else:
        book = _v30_playbook.Playbook.initial(names)
    through = _v30_playbook.learned_through(book)
    problems, dates = [], {}
    for case_id, case in loaded:
        value = case.event_date
        when = _v30_parse_iso_date(value) if isinstance(value, str) else None
        if when is None:
            problems.append(f"case {case_id}: event_date {value!r} is not an ISO date")
            continue
        if through is not None and when <= through:
            problems.append(f"case {case_id}: event_date {when.isoformat()} is on or before "
                            f"{through.isoformat()}, the latest date the playbook "
                            f"{playbook_path} has learned from")
        dates[case_id] = when
    if problems:
        raise ValueError("train refused: " + "; ".join(problems))
    for case_id, case in loaded:
        require_after_cutoff(case_id, case, knowledge_cutoff)
    for case_id, case in loaded:
        manifest = _v30_cases.load_resolution(root, case_id)
        missing = (_v30_cases.target_keys(case) if manifest is None
                   else _v30_cases.unresolved_targets(case, manifest))
        if missing:
            raise ValueError(f"case {case_id}: not every target has resolved: "
                             f"{', '.join(missing)}")
    for case_id, _ in loaded:
        if (_V30_Path(root) / case_id / _v30_cases.PREDICTION_FILE).exists():
            raise ValueError(f"case {case_id}: its forecast is already sealed")

    sealed, outcomes, left, waiting, results = {}, {}, {}, [], []

    def learn(due) -> None:
        ready = sorted((w for w in waiting if due(w[0])), key=lambda w: w[0])
        waiting[:] = [w for w in waiting if not due(w[0])]
        for _, case_id, key, horizon in ready:
            case, analysis = sealed[case_id]
            if case_id not in outcomes:
                outcomes[case_id] = _v30_cases.load_outcome(root, case_id)
            outcome = outcomes[case_id]
            found = [r for r in _v30_targets.score_targets(analysis, outcome, case)
                     if r["key"] == key]
            if not found:
                raise ValueError(f"case {case_id}: {key} is marked resolved, but its outcome "
                                 f"has no TargetOutcome for it")
            record = found[0]
            _v30_playbook.update_weights_probs(book, record["prob_by_perspective"],
                                               record["actual_up"])
            entry = {"case_id": record["case_id"], "split": "train"}
            entry.update((k, v) for k, v in record.items() if k != "case_id")
            book.history.append(entry)
            results.append(entry)
            left[(case_id, horizon)] -= 1
            if left[(case_id, horizon)] == 0:
                keys = [_v30_cases.target_key(t.id, horizon) for t in case.targets
                        if horizon in t.horizons]
                lessons = _v30_targets.reflect_targets(case, analysis, outcome, roles.reflector,
                                                       keys=keys)
                _v30_playbook.add_lessons(book, lessons, _v30_cases.identity_terms(case))
            _v30_playbook.save_playbook(playbook_path, book)

    ordered = sorted(loaded, key=lambda item: dates[item[0]])
    for when, group in _v30_groupby(ordered, key=lambda item: dates[item[0]]):
        batch = list(group)
        learn(lambda r: r < when)
        for case_id, case in batch:
            analysis = _v30_targets.analyze_targets(
                _v30_masking.unmasked(case), case, book, roles.analyst, roles.judge,
                perspectives=_v30_targets.perspective_set("macro"), mode=perspective_mode)
            model = getattr(roles.analyst, "model", "")
            analysis.model = model if isinstance(model, str) else ""
            analysis.knowledge_cutoff = _v30_parse_iso_date(cutoff).isoformat()
            _v30_write_json(_V30_Path(root) / case_id / _v30_cases.PREDICTION_FILE,
                            _v30_asdict(analysis))
            sealed[case_id] = (case, analysis)
        for case_id, case in batch:
            for target in case.targets:
                for horizon in target.horizons:
                    waiting.append((_v30_horizon_end(when, horizon), case_id,
                                    _v30_cases.target_key(target.id, horizon), horizon))
                    left[(case_id, horizon)] = left.get((case_id, horizon), 0) + 1
        learn(lambda r: r <= when)
    learn(lambda r: True)
    return results
