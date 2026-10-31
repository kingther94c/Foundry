

# ---- scoring and reflecting on many targets (v27 reference) -----------------------------

from tools.dates import horizon_end as _v27_horizon_end
from tools.dates import parse_iso_date as _v27_parse_iso_date
from tools.stats import brier_score as _v27_brier_score

from researchos import cases as _v27_cases
from researchos.playbook import Lesson as _V27_Lesson
from researchos.reflect import REFLECT_SCHEMA as _V27_REFLECT_SCHEMA

MACRO_REFLECTOR_SYSTEM = (
    "You are the reflector in a research pipeline that trains macro strategists to forecast "
    "where market levels go in the weeks and months after a snapshot of the markets and the "
    "economy. The forecasts were sealed before the outcomes were known; now you see both. "
    "Explain why each target moved the way it did, where the forecasts went wrong or right, "
    "and write lessons that would hold on other dates. A lesson must not make a rule of a "
    "particular date, level or event: it is shown to the strategists on every later "
    "snapshot. Each lesson says when it applies and which perspective it belongs to.")


def _v27_check(analysis, case):
    if analysis.case_id != case.case_id:
        raise ValueError(f"the analysis is of case {analysis.case_id!r}, not {case.case_id!r}")
    keys = _v27_cases.target_keys(case)
    if list(analysis.keys) != keys:
        raise ValueError(f"case {case.case_id}: the analysis has the keys {analysis.keys!r}, "
                         f"the case {keys!r}")


def _v27_parts(case):
    found = {}
    for t in case.targets:
        for h in t.horizons:
            found[_v27_cases.target_key(t.id, h)] = (t, h)
    return found


def score_targets(analysis, outcome, case) -> list:
    _v27_check(analysis, case)
    parts = _v27_parts(case)
    as_of = _v27_parse_iso_date(case.event_date)
    records = []
    for key in analysis.keys:
        o = outcome.targets.get(key)
        if o is None:
            continue
        t, h = parts[key]
        y = 1 if o.up else 0

        def brier(p):
            return _v27_brier_score([p], [y])

        probs = {name: analysis.views[name].probs[key] for name in analysis.perspectives}
        uniform = sum(probs.values()) / len(probs)
        pw, pj = analysis.prob_weighted[key], analysis.prob_judge[key]
        records.append({
            "case_id": f"{case.case_id}/{key}", "case": case.case_id, "key": key,
            "target": t.id, "horizon": h, "kind": t.kind, "event_date": case.event_date,
            "resolution_date": _v27_horizon_end(as_of, h).isoformat(),
            "start_date": o.start_date, "start": o.start, "end_date": o.end_date, "end": o.end,
            "change": o.change, "trailing_change": t.trailing.get(h), "actual_up": o.up,
            "prob_weighted": pw, "prob_judge": pj, "prob_uniform": uniform,
            "prob_by_perspective": probs,
            "brier_weighted": brier(pw), "brier_judge": brier(pj), "brier_uniform": brier(uniform),
            "brier_coin": 0.25,
            "brier_by_perspective": {name: brier(p) for name, p in probs.items()},
            "correct_weighted": (pw > 0.5) == o.up, "correct_judge": (pj > 0.5) == o.up,
        })
    return records


def reflect_targets(case, analysis, outcome, reflector, *, keys=None) -> list:
    from researchos.snapshot import format_change, format_level

    _v27_check(analysis, case)
    if keys is None:
        chosen = [k for k in analysis.keys if k in outcome.targets]
    else:
        wanted = list(keys)
        for k in wanted:
            if k not in analysis.keys:
                raise ValueError(f"case {case.case_id}: {k!r} is not one of its keys")
            if k not in outcome.targets:
                raise ValueError(f"case {case.case_id}: {k} has not resolved")
        chosen = [k for k in analysis.keys if k in wanted]
    if not chosen:
        return []
    parts_of = _v27_parts(case)
    parts = [f"Snapshot as of {case.event_date}: the sealed forecasts and what happened."]
    for key in chosen:
        t, h = parts_of[key]
        o = outcome.targets[key]
        parts.append("\n".join([
            f"### {key} ({t.name}, {h})",
            "Perspectives: " + ", ".join(f"{name} {analysis.views[name].probs[key]:.2f}"
                                         for name in analysis.perspectives),
            f"Combined: {analysis.prob_weighted[key]:.3f}; judge: {analysis.prob_judge[key]:.3f}",
            f"Outcome: from {format_level(o.start)} on {o.start_date} to {format_level(o.end)} "
            f"on {o.end_date}, {format_change(o.change, o.kind)}: "
            f"{'up' if o.up else 'not up'}",
        ]))
    parts.append(f"Judge's thesis: {analysis.thesis}")
    parts.append("Documents\n\n" + _v27_cases.render_documents(case.documents))
    parts.append("Why did these targets move the way they did? Where were the forecasts wrong, "
                 "and where right? Write lessons that would hold on other dates; never make a "
                 "rule of a particular date, level or event.")
    parts.append("Each lesson's perspective is one of: " + ", ".join(analysis.perspectives)
                 + ", or \"general\".")
    output = reflector.json(MACRO_REFLECTOR_SYSTEM, "\n\n".join(parts), _V27_REFLECT_SCHEMA)
    items = output.get("lessons") if isinstance(output, dict) else None
    lessons = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        text = item.get("lesson")
        if not isinstance(text, str) or not text.strip():
            continue
        applies = item.get("applies_when")
        perspective = item.get("perspective")
        perspective = perspective.strip().lower() if isinstance(perspective, str) else ""
        if perspective not in analysis.perspectives:
            perspective = "general"
        lessons.append(_V27_Lesson(text=text.strip(),
                                   applies_when=applies.strip() if isinstance(applies, str) else "",
                                   perspective=perspective, case_id=case.case_id))
    return lessons
