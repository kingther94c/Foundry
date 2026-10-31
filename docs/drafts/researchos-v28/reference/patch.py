"""The v28 reference, as edits to a copy of the researchos package (check.py applies them).

Each edit replaces one anchor that must occur exactly once, so a changed module fails loudly.
"""

from __future__ import annotations

from pathlib import Path

NL = chr(10)


def _edit(path: Path, pairs) -> None:
    text = path.read_text(encoding="utf-8")
    for old, new in pairs:
        if text.count(old) != 1:
            raise AssertionError(f"{path.name}: anchor found {text.count(old)} times: {old[:60]!r}")
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8", newline=NL)


CASES = [(
    'RESOLUTION_FILE = "resolution.json"\n',
    'RESOLUTION_FILE = "resolution.json"\nPREDICTION_FILE = "prediction.json"\n',
)]

SNAPSHOT = [(
    "from researchos.loop import PREDICTION_FILE\n",
    "from researchos.cases import PREDICTION_FILE\n",
)]

PLAYBOOK = [(
    '''    @classmethod
    def initial(cls) -> "Playbook":
        return cls(weights={p: 1.0 / len(PERSPECTIVES) for p in PERSPECTIVES},
                   lessons=[], history=[])
''',
    '''    @classmethod
    def initial(cls, perspectives=None) -> "Playbook":
        names = list(PERSPECTIVES if perspectives is None else perspectives)
        return cls(weights={p: 1.0 / len(names) for p in names}, lessons=[], history=[])
''',
)]

LOOP_HEAD = [
    ("from researchos.cases import DOMAINS, identity_terms, load_case, load_outcome\n",
     "from researchos.cases import (DOMAINS, OUTCOME_FILE, PREDICTION_FILE, Outcome,\n"
     "                              identity_terms, load_case, load_outcome)\n"),
    ("from researchos.jsonio import write_json\n",
     "from researchos.jsonio import read_json, write_json\n"),
    ("                                mask_case, save_masked)\n",
     "                                mask_case, save_masked, unmasked)\n"),
    ("from researchos.perspectives import Analysis, analyze, check_mode\n",
     "from researchos.perspectives import Analysis, View, analyze, check_mode\n"),
    ("from researchos.reflect import reflect\n",
     "from researchos.reflect import reflect\n"
     "from researchos.targets import (TargetAnalysis, analyze_targets, perspective_set,\n"
     "                                score_targets, target_analysis_from_dict)\n"),
    ('\nPREDICTION_FILE = "prediction.json"\n', "\n"),
    # train and evaluate refuse a macro case (with their other refusals)
    ("        case = load_case(root, case_id)\n"
     "        try:\n"
     "            when = _event_date(case_id, case)\n",
     "        case = load_case(root, case_id)\n"
     "        if case.domain == \"macro\":\n"
     "            problems.append(f\"case {case_id}: a macro case; its outcome is not known on \"\n"
     "                            f\"its date, so it is forecast with forecast and scored with \"\n"
     "                            f\"score_sealed\")\n"
     "            continue\n"
     "        try:\n"
     "            when = _event_date(case_id, case)\n"),
]

LOOP_TAIL = '''

# ---- forecast now, score later (v28 reference) ----------------------------------------

def _no_repeats(case_ids) -> None:
    seen = set()
    for case_id in case_ids:
        if case_id in seen:
            raise ValueError(f"case {case_id} is named twice")
        seen.add(case_id)


def forecast(root, case_ids, playbook_path, roles: Roles, seed=0,
             mask_config: MaskConfig = MaskConfig(), knowledge_cutoff=None,
             perspective_mode="holistic") -> list[dict]:
    check_mode(perspective_mode)
    cutoff = parse_cutoff(knowledge_cutoff)
    ids = list(case_ids)
    if not ids:
        return []
    _no_repeats(ids)
    loaded = [(case_id, load_case(root, case_id)) for case_id in ids]
    domain = single_domain(case for _, case in loaded)
    names = perspective_set(domain)
    if Path(playbook_path).is_file():
        playbook = load_playbook(playbook_path)
        if set(playbook.weights) != set(names):
            raise ValueError(f"the playbook {playbook_path} does not weigh the {domain} "
                             f"perspectives; it belongs to another domain")
    else:
        playbook = Playbook.initial(names)
    through = learned_through(playbook)
    problems = []
    for case_id, case in loaded:
        try:
            when = _event_date(case_id, case)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if through is not None and when <= through:
            problems.append(f"case {case_id}: event_date {when.isoformat()} is on or before "
                            f"{through.isoformat()}, the latest event date the playbook "
                            f"{playbook_path} has learned from")
    if problems:
        raise ValueError("forecast refused: " + "; ".join(problems))
    if domain == "macro":
        for case_id, case in loaded:
            require_after_cutoff(case_id, case, knowledge_cutoff)
    for case_id, _ in loaded:
        if (Path(root) / case_id / PREDICTION_FILE).exists():
            raise ValueError(f"case {case_id}: its forecast is already sealed")
    entries = []
    for case_id, case in loaded:
        if domain == "macro":
            analysis = analyze_targets(unmasked(case), case, playbook, roles.analyst,
                                       roles.judge, perspectives=perspective_set("macro"),
                                       mode=perspective_mode)
            model = getattr(roles.analyst, "model", "")
            analysis.model = model if isinstance(model, str) else ""
            analysis.knowledge_cutoff = parse_iso_date(cutoff).isoformat()
            _seal(root, analysis)
            entries.append({"case_id": case_id, "event_date": case.event_date,
                            "domain": "macro", "sealed": True, "mode": perspective_mode,
                            "model": analysis.model, "keys": analysis.keys,
                            "prob_weighted": analysis.prob_weighted,
                            "prob_judge": analysis.prob_judge,
                            "prob_by_perspective": {name: view.probs
                                                    for name, view in analysis.views.items()}})
            continue
        masked, analysis = _predict(root, case_id, case, playbook, roles, seed, mask_config,
                                    "forecast", cutoff, perspective_mode)
        if analysis is None:
            entries.append({"case_id": case_id, "event_date": case.event_date,
                            "domain": "earnings", "sealed": False, "skipped": "identifiable"})
            continue
        entries.append({"case_id": case_id, "event_date": case.event_date, "domain": "earnings",
                        "sealed": True, "prob_weighted": analysis.prob_weighted,
                        "prob_judge": analysis.prob_judge,
                        "prob_by_perspective": {name: view.prob_up
                                                for name, view in analysis.views.items()},
                        "identifiable": masked.status == "identifiable"})
    return entries


def load_prediction(root, case_id):
    data = read_json(Path(root) / case_id / PREDICTION_FILE)
    if not isinstance(data, dict):
        raise ValueError(f"case {case_id}: {PREDICTION_FILE} does not hold an object")
    if "keys" in data:
        return target_analysis_from_dict(data)
    views = {name: View(stance=v["stance"], prob_up=v["prob_up"], conviction=v["conviction"],
                        key_points=list(v["key_points"]))
             for name, v in data["views"].items()}
    return Analysis(case_id=data["case_id"], views=views, prob_weighted=data["prob_weighted"],
                    prob_judge=data["prob_judge"], thesis=data["thesis"])


def score_sealed(root, case_ids) -> dict:
    ids = list(case_ids)
    _no_repeats(ids)
    loaded = [(case_id, load_case(root, case_id)) for case_id in ids]
    single_domain(case for _, case in loaded)
    for case_id, _ in loaded:
        if not (Path(root) / case_id / PREDICTION_FILE).is_file():
            raise ValueError(f"case {case_id}: no {PREDICTION_FILE}; it was never forecast")
    forecasts = []
    for case_id, case in loaded:
        analysis = load_prediction(root, case_id)
        if analysis.case_id != case_id:
            raise ValueError(f"case {case_id}: its {PREDICTION_FILE} is the forecast of case "
                             f"{analysis.case_id!r}")
        if isinstance(analysis, TargetAnalysis) != (case.domain == "macro"):
            raise ValueError(f"case {case_id}: its {PREDICTION_FILE} is not a forecast of a "
                             f"{case.domain} case")
        forecasts.append((case_id, case, analysis))
    records, pending = [], []
    for case_id, case, analysis in forecasts:
        has_outcome = (Path(root) / case_id / OUTCOME_FILE).is_file()
        if case.domain == "macro":
            outcome = load_outcome(root, case_id) if has_outcome else Outcome()
            records.extend(score_targets(analysis, outcome, case))
            pending.extend(f"{case_id}/{key}" for key in analysis.keys
                           if key not in outcome.targets)
            continue
        if not has_outcome:
            pending.append(case_id)
            continue
        outcome = load_outcome(root, case_id)
        record = score(analysis, outcome)
        probs = [view.prob_up for view in analysis.views.values()]
        y = 1 if record["actual_up"] else 0
        records.append({"case_id": case_id, "event_date": case.event_date, **record,
                        "brier_uniform": _brier(sum(probs) / len(probs), y)})
    return {
        "cases": records,
        "n": len(records),
        "pending": pending,
        "brier_weighted": _mean([r["brier_weighted"] for r in records]),
        "brier_judge": _mean([r["brier_judge"] for r in records]),
        "brier_uniform": _mean([r["brier_uniform"] for r in records]),
        "brier_coin": _mean([0.25 for _ in records]),
        "accuracy_weighted": _mean([1.0 if r["correct_weighted"] else 0.0 for r in records]),
        "accuracy_judge": _mean([1.0 if r["correct_judge"] else 0.0 for r in records]),
    }
'''

MAIN = [
    ("from researchos.loop import Roles, evaluate, parse_cutoff, train\n",
     "from researchos.loop import Roles, evaluate, forecast, parse_cutoff, score_sealed, train\n"),
    ('    for name in ("train", "evaluate"):\n', '    for name in ("train", "evaluate", "forecast"):\n'),
    ("    return parser\n",
     '    p = sub.add_parser("score", help="score the sealed forecasts against what has resolved")\n'
     '    p.add_argument("--root", required=True)\n'
     '    p.add_argument("--ids", required=True, help="comma-separated case ids")\n'
     "    _add_out(p)\n"
     "    return parser\n"),
    ('    if args.command == "resolve":\n        return _resolve(args)\n',
     '    if args.command == "resolve":\n        return _resolve(args)\n'
     '    if args.command == "score":\n        return _score(args)\n'),
    ('        run = train if args.command == "train" else evaluate\n',
     '        run = {"train": train, "evaluate": evaluate, "forecast": forecast}[args.command]\n'),
    ("    return _emit(result, args.out)\n\n\ndef _case_ids",
     '    if args.command == "forecast":\n'
     '        result = {"cases": result}\n'
     "    return _emit(result, args.out)\n\n\n"
     "def _score(args) -> int:\n"
     "    ids = _case_ids(args.ids)\n"
     "    if not ids:\n"
     '        print("error: --ids needs at least one case id", file=sys.stderr)\n'
     "        return 2\n"
     "    try:\n"
     "        result = score_sealed(args.root, ids)\n"
     "    except (OSError, ValueError, KeyError) as exc:\n"
     '        print(f"error: {exc}", file=sys.stderr)\n'
     "        return 1\n"
     "    return _emit(result, args.out)\n\n\n"
     "def _case_ids"),
]


def apply(pkg) -> None:
    pkg = Path(pkg)
    _edit(pkg / "cases.py", CASES)
    _edit(pkg / "snapshot.py", SNAPSHOT)
    _edit(pkg / "playbook.py", PLAYBOOK)
    _edit(pkg / "loop.py", LOOP_HEAD)
    loop = pkg / "loop.py"
    loop.write_text(loop.read_text(encoding="utf-8") + LOOP_TAIL, encoding="utf-8", newline=NL)
    _edit(pkg / "__main__.py", MAIN)
