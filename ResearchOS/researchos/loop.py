"""Training and evaluation loops: mask, analyze, seal, reveal, (reflect).

Forecast now, score later (v28): ``forecast`` seals forecasts and opens no outcome, and
``score_sealed`` scores what was sealed against what has resolved since, calling no model.
``evaluate`` opens a case's outcome as soon as it is sealed, so it refuses a macro case,
whose targets resolve weeks or months after its date.

Walk forward by resolution date (v30): ``train`` takes a macro run. Each snapshot is
forecast with a playbook that has learned from exactly the target keys resolved before its
date, and a key is learned from once its horizon has ended.
"""

from __future__ import annotations

import logging
import random
from dataclasses import asdict, dataclass
from datetime import date
from itertools import groupby
from pathlib import Path
from typing import Any

from tools.dates import horizon_end, parse_iso_date
from tools.stats import brier_score

from researchos.cases import (DOMAINS, OUTCOME_FILE, PREDICTION_FILE, RESOLUTION_FILE, Outcome,
                              TargetOutcome, identity_terms, load_case, load_outcome,
                              load_resolution, target_key, target_keys, unresolved_targets)
from researchos.jsonio import read_json, write_json
from researchos.masking import (MASKED_FILE, MaskConfig, guard_documents, load_masked,
                                mask_case, save_masked, unmasked)
from researchos.perspectives import Analysis, View, analyze, check_mode
from researchos.playbook import (Playbook, add_lessons, learned_through, lesson_is_clean,
                                 load_playbook, save_playbook, update_weights,
                                 update_weights_probs)
from researchos.reflect import reflect
from researchos.targets import (TargetAnalysis, analyze_targets, perspective_set, reflect_targets,
                                score_targets, target_analysis_from_dict)

log = logging.getLogger("researchos")


@dataclass
class Roles:
    masker: Any
    guesser: Any
    analyst: Any
    judge: Any
    reflector: Any
    abstractor: Any = None  # used only when MaskConfig.abstract is set


def _brier(p: float, y: int) -> float:
    return brier_score([p], [y])


def score(analysis: Analysis, outcome) -> dict:
    y = 1 if outcome.abnormal_return_1d > 0 else 0
    actual_up = y == 1
    return {
        "actual_up": actual_up,
        "brier_weighted": _brier(analysis.prob_weighted, y),
        "brier_judge": _brier(analysis.prob_judge, y),
        "brier_by_perspective": {name: _brier(v.prob_up, y) for name, v in analysis.views.items()},
        # Each perspective's own forecast, so other ways of combining them can be replayed
        # later (researchos.blend) without calling a model again.
        "prob_by_perspective": {name: v.prob_up for name, v in analysis.views.items()},
        "correct_weighted": (analysis.prob_weighted > 0.5) == actual_up,
        "correct_judge": (analysis.prob_judge > 0.5) == actual_up,
    }


def _get_masked(root, case, roles: Roles, rng: random.Random, mask_config: MaskConfig):
    if (Path(root) / case.case_id / MASKED_FILE).is_file():
        masked = load_masked(root, case.case_id)
        if masked.abstracted != mask_config.abstract:
            log.warning("%s: reusing the saved %s, masked %s abstraction although this run "
                        "masks %s it; delete the file to mask the case again", case.case_id,
                        MASKED_FILE, "with" if masked.abstracted else "without",
                        "with" if mask_config.abstract else "without")
    else:
        masked = mask_case(case, roles.masker, roles.guesser, rng, mask_config,
                           abstractor=getattr(roles, "abstractor", None))
        save_masked(root, masked)
    # Whatever is on disk, never show the analyst an identity term.
    masked.documents = guard_documents(masked.documents, identity_terms(case))
    return masked


def _analysis_playbook(playbook: Playbook, case) -> Playbook:
    """A view of the playbook without lessons that name the case being analyzed."""
    terms = identity_terms(case)
    return Playbook(weights=dict(playbook.weights),
                    lessons=[l for l in playbook.lessons if lesson_is_clean(l, terms)],
                    history=[])


def _seal(root, analysis: Analysis) -> None:
    path = Path(root) / analysis.case_id / PREDICTION_FILE
    write_json(path, asdict(analysis))


def parse_cutoff(knowledge_cutoff) -> date | None:
    """The knowledge cutoff as a date (None stays None); ValueError if not an ISO date."""
    if knowledge_cutoff is None:
        return None
    if isinstance(knowledge_cutoff, date):
        return knowledge_cutoff
    if not isinstance(knowledge_cutoff, str):
        raise ValueError(f"knowledge_cutoff must be an ISO date string, got {knowledge_cutoff!r}")
    cutoff = parse_iso_date(knowledge_cutoff)
    if cutoff is None:
        raise ValueError(f"knowledge_cutoff is not an ISO date: {knowledge_cutoff!r}")
    return cutoff


def _after_cutoff(case, cutoff: date | None) -> bool:
    """True only if the case's event date is known and strictly after the cutoff."""
    if cutoff is None:
        return False
    # Parsed from its text, as always: any value whose str() is an ISO date counts.
    event = parse_iso_date(str(case.event_date))
    if event is None:
        log.warning("%s: event_date %r is not an ISO date; treated as not after the "
                    "knowledge cutoff", case.case_id, case.event_date)
        return False
    return event > cutoff


def _predict(root, case_id, case, playbook, roles, seed, mask_config, split, cutoff=None,
             perspective_mode="holistic"):
    """Mask, analyze and seal one case. Returns (masked, analysis); analysis None if skipped."""
    rng = random.Random(f"{seed}:{case_id}")
    masked = _get_masked(root, case, roles, rng, mask_config)
    if masked.status == "identifiable":
        if not _after_cutoff(case, cutoff):
            log.warning("%s: %s skipped, masking left it identifiable", split, case_id)
            return masked, None
        log.warning("%s: %s is identifiable but its event date %s is after the knowledge "
                    "cutoff %s; analyzing it", split, case_id, case.event_date, cutoff)
    analysis = analyze(masked, _analysis_playbook(playbook, case), roles.analyst, roles.judge,
                       mode=perspective_mode)
    _seal(root, analysis)
    return masked, analysis


def _open_playbook(playbook_path) -> Playbook:
    if Path(playbook_path).is_file():
        return load_playbook(playbook_path)
    return Playbook.initial()


def _event_date(case_id, case) -> date:
    """The case's event date; ValueError naming the case if it is not an ISO date."""
    value = case.event_date
    # Only a string can be an event date here (parse_iso_date would also pass a date object).
    when = parse_iso_date(value) if isinstance(value, str) else None
    if when is None:
        raise ValueError(f"case {case_id}: event_date {value!r} is not an ISO date (YYYY-MM-DD), "
                         f"so the case cannot be placed in time")
    return when


def require_after_cutoff(case_id, case, knowledge_cutoff) -> None:
    """Refuse a case whose date is not after the model's knowledge cutoff (v21).

    Nothing in a macro case's documents stops a model from remembering what followed their
    date, so it may only be forecast when that date is after the cutoff. Raises ValueError
    naming case_id when knowledge_cutoff is None, when the case's event_date is not a str
    that parse_iso_date reads, or when that date is on or before the cutoff. knowledge_cutoff
    is anything parse_cutoff accepts; parse_cutoff's ValueError passes through.
    """
    cutoff = parse_cutoff(knowledge_cutoff)
    if cutoff is None:
        raise ValueError(f"case {case_id}: no knowledge cutoff was given, so nothing shows that "
                         f"the model cannot remember what followed the case's date")
    # parse_cutoff passes a datetime through as it is: compare its date.
    cutoff = parse_iso_date(cutoff)
    when = _event_date(case_id, case)
    if when <= cutoff:
        raise ValueError(f"case {case_id}: event_date {when.isoformat()} is on or before the "
                         f"knowledge cutoff {cutoff.isoformat()}, so the model may remember "
                         f"what followed it")


def single_domain(cases) -> str:
    """The domain every case in `cases` shares; "earnings" when there are none (v21).

    Raises ValueError naming the domain when a case's domain is not in DOMAINS, and naming
    both when the cases have more than one: forecasts, playbooks and perspectives differ by
    domain, so a run is one domain.
    """
    domains: list[str] = []
    for case in cases:
        domain = case.domain
        if domain not in DOMAINS:
            raise ValueError(f"case {case.case_id}: domain {domain!r} is not one of "
                             f"{', '.join(DOMAINS)}")
        if domain not in domains:
            domains.append(domain)
    if len(domains) > 1:
        raise ValueError(f"the cases have more than one domain, {domains[0]!r} and "
                         f"{domains[1]!r}: forecasts, playbooks and perspectives differ by "
                         f"domain, so a run is one domain")
    return domains[0] if domains else "earnings"


def _load_cases(root, case_ids) -> list:
    """[(case_id, load_case(root, case_id))] for each id, in the order given."""
    return [(case_id, load_case(root, case_id)) for case_id in case_ids]


def _refuse_macro(loaded, command: str) -> None:
    """ValueError naming every macro case among the loaded ones (v28).

    evaluate opens a case's outcome as soon as its forecast is sealed, and a macro case's
    outcome is not known on its date: it is forecast with forecast and scored with
    score_sealed. (train walks a macro run forward by resolution date instead, v30.)
    """
    problems = [f"case {case_id} is a macro case: its outcome is not known on its date, so "
                f"it is forecast with forecast and scored with score_sealed"
                for case_id, case in loaded if case.domain == "macro"]
    if problems:
        raise ValueError(f"{command} refused: " + "; ".join(problems))


def _requested_cases(root, case_ids, playbook: Playbook, playbook_path, command: str):
    """Load every requested case and check it can be forecast without look-ahead.

    Returns [(case_id, case, event_date)] in the order given. Raises ValueError naming
    every macro case (v28); then naming every case whose event_date is not an ISO date,
    or is on or before the latest event date the playbook has learned from (its forecast
    would use reactions it could not have known). Nothing is called or written here, so a
    refusal leaves no trace.
    """
    loaded = _load_cases(root, case_ids)
    _refuse_macro(loaded, command)
    return _dated_cases(loaded, playbook, playbook_path, command)


def _dated_cases(loaded, playbook: Playbook, playbook_path, command: str, *,
                 learned: str = "event date", used: str = "reactions"):
    """[(case_id, case, event_date)] for the loaded cases, once none of them is a look-ahead.

    Raises ValueError, its message beginning f"{command} refused: ", naming every case
    whose event_date is not an ISO date, or is on or before learned_through(playbook).
    `learned` and `used` only word the message: a macro run learns by resolution date.
    """
    through = learned_through(playbook)
    requested, problems = [], []
    for case_id, case in loaded:
        try:
            when = _event_date(case_id, case)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if through is not None and when <= through:
            problems.append(
                f"case {case_id}: event_date {when.isoformat()} is on or before "
                f"{through.isoformat()}, the latest {learned} the playbook {playbook_path} "
                f"has learned from, so its forecast would use {used} it could not have known")
        requested.append((case_id, case, when))
    if problems:
        raise ValueError(f"{command} refused: " + "; ".join(problems))
    return requested


def train(root, case_ids, playbook_path, roles: Roles, seed=0,
          mask_config: MaskConfig = MaskConfig(), knowledge_cutoff=None,
          perspective_mode="holistic") -> list[dict]:
    """Learn from the cases, walking forward in time.

    check_mode and parse_cutoff come first, then load_case of every id. When none of the
    cases is a macro case, the run is an earnings run, as before v30: batches of one event
    date, each case masked, analyzed and sealed with the playbook from before its batch,
    then its outcome opened, scored and reflected on; it returns the scored records.
    Otherwise it is a macro run (_train_macro): each target key is learned from once its
    resolution date has passed, and it returns the history records it appended.
    """
    check_mode(perspective_mode)  # before anything is masked, called or written
    cutoff = parse_cutoff(knowledge_cutoff)
    ids = list(case_ids)
    loaded = _load_cases(root, ids)
    if any(case.domain == "macro" for _, case in loaded):
        return _train_macro(root, ids, loaded, playbook_path, roles, knowledge_cutoff, cutoff,
                            perspective_mode)
    # An earnings run, as before v30.
    playbook = _open_playbook(playbook_path)
    requested = _dated_cases(loaded, playbook, playbook_path, "train")
    # Walk forward in event-date order; sorted() is stable, so ties keep the order given.
    ordered = sorted(requested, key=lambda item: item[2])
    results: list[dict] = []
    for _, group in groupby(ordered, key=lambda item: item[2]):
        batch = list(group)
        # Cases sharing an event date reacted together: each is forecast (masked, analyzed,
        # sealed) with the playbook as it stood before the batch, before any of their
        # outcomes is opened. _predict never changes the playbook.
        forecasts = []
        for case_id, case, _ in batch:
            masked, analysis = _predict(root, case_id, case, playbook, roles, seed, mask_config,
                                        "train", cutoff, perspective_mode)
            forecasts.append((case_id, case, masked, analysis))
        # Only then are the outcomes opened, one case at a time in batch order.
        for case_id, case, masked, analysis in forecasts:
            if analysis is None:
                playbook.history.append({"case_id": case_id, "split": "train",
                                         "event_date": case.event_date,
                                         "skipped": "identifiable"})
                save_playbook(playbook_path, playbook)
                continue
            outcome = load_outcome(root, case_id)  # only after the whole batch is sealed
            record = score(analysis, outcome)
            update_weights(playbook, analysis.views, record["actual_up"])
            lessons = reflect(case, masked, analysis, outcome, roles.reflector)
            rejected = add_lessons(playbook, lessons, identity_terms(case))
            record = {"case_id": case_id, "split": "train", "event_date": case.event_date,
                      **record,
                      "identifiable": masked.status == "identifiable",
                      "lessons_rejected": rejected}
            playbook.history.append(record)
            save_playbook(playbook_path, playbook)
            results.append(record)
    return results


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def evaluate(root, case_ids, playbook_path, roles: Roles, seed=0,
             mask_config: MaskConfig = MaskConfig(), knowledge_cutoff=None,
             perspective_mode="holistic") -> dict:
    check_mode(perspective_mode)  # before anything is masked, called or written
    cutoff = parse_cutoff(knowledge_cutoff)
    if Path(playbook_path).is_file():
        playbook = load_playbook(playbook_path)
    else:
        log.warning("evaluate: no playbook at %s, using the initial playbook", playbook_path)
        playbook = Playbook.initial()
    # A held-out case must come after everything the playbook learned from.
    requested = _requested_cases(root, case_ids, playbook, playbook_path, "evaluate")
    records: list[dict] = []
    for case_id, case, _ in requested:
        masked, analysis = _predict(root, case_id, case, playbook, roles, seed, mask_config,
                                    "eval", cutoff, perspective_mode)
        if analysis is None:
            continue
        outcome = load_outcome(root, case_id)  # only after the prediction is sealed
        record = score(analysis, outcome)
        y = 1 if record["actual_up"] else 0
        probs = [v.prob_up for v in analysis.views.values()]
        uniform = sum(probs) / len(probs)
        records.append({"case_id": case_id, "split": "eval", "event_date": case.event_date,
                        **record,
                        "brier_uniform": _brier(uniform, y),
                        "identifiable": masked.status == "identifiable"})
    n = len(records)
    return {
        "cases": records,
        "n": n,
        "brier_weighted": _mean([r["brier_weighted"] for r in records]),
        "brier_judge": _mean([r["brier_judge"] for r in records]),
        "brier_uniform": _mean([r["brier_uniform"] for r in records]),
        "brier_coin": _mean([0.25 for _ in records]),
        "accuracy_weighted": _mean([1.0 if r["correct_weighted"] else 0.0 for r in records]),
        "accuracy_judge": _mean([1.0 if r["correct_judge"] else 0.0 for r in records]),
        "n_identifiable": sum(1 for r in records if r["identifiable"]),
        "perspective_mode": perspective_mode,
    }


# ---- forecast now, score later (v28) ----------------------------------------------------

def _refuse_repeated(case_ids, command: str) -> None:
    """ValueError naming every id given more than once."""
    seen: set = set()
    repeated: list = []
    for case_id in case_ids:
        if case_id in seen and case_id not in repeated:
            repeated.append(case_id)
        seen.add(case_id)
    if repeated:
        raise ValueError(f"{command} refused: " + "; ".join(
            f"case {case_id} is given more than once" for case_id in repeated))


def _domain_playbook(playbook_path, domain: str, command: str = "forecast") -> Playbook:
    """The playbook at playbook_path, else the initial playbook of the domain's perspectives.

    A loaded playbook whose weights do not name exactly the domain's perspectives (as a set)
    belongs to another domain: ValueError naming the file and the domain, its message
    beginning f"{command} refused: ".
    """
    names = perspective_set(domain)
    if not Path(playbook_path).is_file():
        return Playbook.initial(names)
    playbook = load_playbook(playbook_path)
    if set(playbook.weights) != set(names):
        raise ValueError(f"{command} refused: the playbook {playbook_path} weighs the "
                         f"perspectives {', '.join(playbook.weights) or '(none)'}, not those "
                         f"of the {domain} domain ({', '.join(names)}): the playbook belongs "
                         f"to another domain")
    return playbook


def _iso_cutoff(cutoff) -> str:
    """The knowledge cutoff (what parse_cutoff returned) as YYYY-MM-DD."""
    return parse_iso_date(cutoff).isoformat()


def _model_of(agent) -> str:
    """agent.model when the agent has a `model` attribute that is a str, else ""."""
    model = getattr(agent, "model", None)
    return model if isinstance(model, str) else ""


def _forecast_earnings(root, case_id, case, playbook, roles, seed, mask_config, cutoff,
                       perspective_mode) -> dict:
    """Mask, skip or analyze, and seal one earnings case as evaluate does; its entry."""
    masked, analysis = _predict(root, case_id, case, playbook, roles, seed, mask_config,
                                "forecast", cutoff, perspective_mode)
    entry = {"case_id": case_id, "event_date": case.event_date, "domain": "earnings"}
    if analysis is None:
        return {**entry, "sealed": False, "skipped": "identifiable"}
    return {**entry, "sealed": True,
            "prob_weighted": analysis.prob_weighted,
            "prob_judge": analysis.prob_judge,
            "prob_by_perspective": {name: view.prob_up for name, view in analysis.views.items()},
            "identifiable": masked.status == "identifiable"}


def _seal_macro(root, case_id, case, playbook, roles, cutoff, perspective_mode) -> TargetAnalysis:
    """Analyze one macro case on its documents as they are and seal it; the analysis.

    A macro case has no company to hide: no masked.json is read or written. The analysis
    records the analyst's model and the knowledge cutoff as YYYY-MM-DD.
    """
    analysis = analyze_targets(unmasked(case), case, playbook, roles.analyst, roles.judge,
                               perspectives=perspective_set("macro"), mode=perspective_mode)
    analysis.model = _model_of(roles.analyst)
    analysis.knowledge_cutoff = _iso_cutoff(cutoff)
    write_json(Path(root) / case_id / PREDICTION_FILE, asdict(analysis))
    return analysis


def _forecast_macro(root, case_id, case, playbook, roles, cutoff, perspective_mode) -> dict:
    """Analyze one macro case on its documents as they are and seal it; its entry."""
    analysis = _seal_macro(root, case_id, case, playbook, roles, cutoff, perspective_mode)
    return {"case_id": case_id, "event_date": case.event_date, "domain": "macro",
            "sealed": True, "mode": perspective_mode, "model": analysis.model,
            "keys": list(analysis.keys),
            "prob_weighted": dict(analysis.prob_weighted),
            "prob_judge": dict(analysis.prob_judge),
            "prob_by_perspective": {name: dict(view.probs)
                                    for name, view in analysis.views.items()}}


def forecast(root, case_ids, playbook_path, roles: Roles, seed=0,
             mask_config: MaskConfig = MaskConfig(), knowledge_cutoff=None,
             perspective_mode="holistic") -> list[dict]:
    """Seal a forecast for each case and return what it forecast (v28).

    No outcome.json and no resolution.json is opened; the playbook is read but never
    written or changed. These checks run in order, each for every case before the next,
    before anything is masked, called or written; the first that fails raises:

    1. check_mode(perspective_mode) and parse_cutoff(knowledge_cutoff); with no case id
       the result is [] here, nothing read;
    2. an id given more than once;
    3. load_case of each id (its OSError passes through);
    4. single_domain of the cases: the run's domain;
    5. the playbook: load_playbook(playbook_path) when the file exists, else
       Playbook.initial(perspective_set(domain)); a loaded one must weigh exactly the
       domain's perspectives;
    6. the refusals train makes for each case's date ("forecast refused: ...");
    7. in a macro run, require_after_cutoff for each case;
    8. a case whose prediction.json exists: a forecast is sealed once and never replaced.

    Then each case is forecast and sealed in the order given: an earnings case masked,
    skipped or analyzed as evaluate does it, a macro case through targets.analyze_targets
    on its documents as they are. A failure part way leaves the forecasts sealed before it.
    """
    check_mode(perspective_mode)
    cutoff = parse_cutoff(knowledge_cutoff)
    ids = list(case_ids)
    if not ids:
        return []
    _refuse_repeated(ids, "forecast")
    loaded = _load_cases(root, ids)
    domain = single_domain(case for _, case in loaded)
    playbook = _domain_playbook(playbook_path, domain)
    _dated_cases(loaded, playbook, playbook_path, "forecast")
    if domain == "macro":
        for case_id, case in loaded:
            require_after_cutoff(case_id, case, knowledge_cutoff)
    _refuse_sealed(root, loaded, "forecast")
    entries: list[dict] = []
    for case_id, case in loaded:
        if domain == "macro":
            entries.append(_forecast_macro(root, case_id, case, playbook, roles, cutoff,
                                           perspective_mode))
        else:
            entries.append(_forecast_earnings(root, case_id, case, playbook, roles, seed,
                                              mask_config, cutoff, perspective_mode))
    return entries


def load_prediction(root, case_id) -> Analysis | TargetAnalysis:
    """The forecast sealed in <root>/<case_id>/prediction.json (v28).

    A dict with the key "keys" is a macro forecast (targets.target_analysis_from_dict);
    any other dict is an earnings Analysis, every field from the file (KeyError when one
    is missing) and its views in the file's order. A value that is not a dict raises
    ValueError; a missing file's OSError passes through.
    """
    path = Path(root) / case_id / PREDICTION_FILE
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: a sealed forecast is a JSON object, got "
                         f"{type(data).__name__}")
    if "keys" in data:
        return target_analysis_from_dict(data)
    return Analysis(
        case_id=data["case_id"],
        views={name: View(stance=view["stance"], prob_up=view["prob_up"],
                          conviction=view["conviction"], key_points=list(view["key_points"]))
               for name, view in data["views"].items()},
        prob_weighted=data["prob_weighted"],
        prob_judge=data["prob_judge"],
        thesis=data["thesis"],
    )


def score_sealed(root, case_ids) -> dict:
    """Score the sealed forecasts against whatever has resolved since (v28).

    Calls no model and writes nothing. First, each check for every case before the next
    (the first that fails raises): an id given more than once (ValueError); load_case of
    each id (OSError); single_domain; a case without prediction.json (ValueError: it was
    never forecast); load_prediction, a forecast of another case and a forecast of the
    other kind (ValueError). Then, case by case: a macro case's records are
    targets.score_targets against its outcome (Outcome() when it has no outcome.json),
    and each key without a TargetOutcome is pending as "<case_id>/<key>"; an earnings
    case without outcome.json is pending as its id, otherwise scored as evaluate scores
    it. The means are evaluate's: over the records, None when there is none.
    """
    ids = list(case_ids)
    _refuse_repeated(ids, "score")
    loaded = _load_cases(root, ids)
    single_domain(case for _, case in loaded)
    unsealed = [case_id for case_id, _ in loaded
                if not (Path(root) / case_id / PREDICTION_FILE).is_file()]
    if unsealed:
        raise ValueError("score refused: " + "; ".join(
            f"case {case_id} has no {PREDICTION_FILE}: it was never forecast"
            for case_id in unsealed))
    analyses = [load_prediction(root, case_id) for case_id, _ in loaded]
    for (case_id, case), analysis in zip(loaded, analyses):
        if analysis.case_id != case.case_id:
            raise ValueError(f"score refused: the forecast sealed for case {case_id} is of "
                             f"case {analysis.case_id!r}, not of case {case.case_id!r}")
    for (case_id, case), analysis in zip(loaded, analyses):
        wanted, kind = ((TargetAnalysis, "a macro") if case.domain == "macro"
                        else (Analysis, "an earnings"))
        if not isinstance(analysis, wanted):
            raise ValueError(f"score refused: case {case_id} is {kind} case, but its sealed "
                             f"forecast is of the other kind ({wanted.__name__} needed, "
                             f"{type(analysis).__name__} found)")

    records: list[dict] = []
    pending: list[str] = []
    for (case_id, case), analysis in zip(loaded, analyses):
        has_outcome = (Path(root) / case_id / OUTCOME_FILE).is_file()
        if case.domain == "macro":
            outcome = load_outcome(root, case_id) if has_outcome else Outcome()
            records.extend(score_targets(analysis, outcome, case))
            pending.extend(f"{case_id}/{key}" for key in analysis.keys
                           if not isinstance(outcome.targets.get(key), TargetOutcome))
            continue
        if not has_outcome:
            pending.append(case_id)
            continue
        outcome = load_outcome(root, case_id)
        record = score(analysis, outcome)
        y = 1 if record["actual_up"] else 0
        probs = [view.prob_up for view in analysis.views.values()]
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


# ---- walk forward by resolution date (v30) ----------------------------------------------

def _refuse_sealed(root, loaded, command: str) -> None:
    """ValueError naming every case whose prediction.json exists: a forecast is sealed once."""
    sealed = [(case_id, Path(root) / case_id / PREDICTION_FILE) for case_id, _ in loaded]
    sealed = [(case_id, path) for case_id, path in sealed if path.exists()]
    if sealed:
        raise ValueError(f"{command} refused: " + "; ".join(
            f"case {case_id}: {path} exists, so its forecast is already sealed; a forecast "
            f"is sealed once and never replaced" for case_id, path in sealed))


def _refuse_unresolved(root, loaded, command: str) -> None:
    """ValueError naming every case with a target key not resolved yet, and those keys.

    Every key of a case has resolved when load_resolution gives a manifest (a dict) of which
    unresolved_targets is empty. Only resolution.json is read: no outcome.json is opened.
    """
    problems: list[str] = []
    for case_id, case in loaded:
        manifest = load_resolution(root, case_id)
        unresolved = unresolved_targets(case, manifest)
        keys = ", ".join(unresolved) or "no key"
        if manifest is None:
            problems.append(f"case {case_id} has no {RESOLUTION_FILE}, so none of its keys is "
                            f"known to have resolved ({keys})")
        elif not isinstance(manifest, dict):
            problems.append(f"case {case_id}: its {RESOLUTION_FILE} is not a manifest, so none "
                            f"of its keys is known to have resolved ({keys})")
        elif unresolved:
            problems.append(f"case {case_id}: its keys {keys} have not resolved yet, so it "
                            f"cannot be learned from")
    if problems:
        raise ValueError(f"{command} refused: " + "; ".join(problems))


@dataclass
class _Walked:
    """A macro case on the walk, and what has been opened and learned from it so far."""
    case_id: str
    case: Any
    when: date                       # its event date
    keys: list                       # (key, horizon, resolution date), in target_keys order
    by_horizon: dict                 # horizon -> its keys, in target_keys order
    left: dict                       # horizon -> how many of those are not learned from yet
    analysis: TargetAnalysis | None = None
    outcome: Outcome | None = None
    records: dict | None = None      # key -> its score_targets record, once the outcome is open


def _walked(case_id, case, when: date) -> _Walked:
    """The case with each key's resolution date, horizon_end(<event date>, <horizon>)."""
    keys = target_keys(case)
    horizon_of = {target_key(target.id, horizon): horizon
                  for target in case.targets for horizon in target.horizons}
    dated = [(key, horizon_of[key], horizon_end(when, horizon_of[key])) for key in keys]
    by_horizon: dict[str, list[str]] = {}
    for key, horizon, _ in dated:
        by_horizon.setdefault(horizon, []).append(key)
    return _Walked(case_id=case_id, case=case, when=when, keys=dated, by_horizon=by_horizon,
                   left={horizon: len(group) for horizon, group in by_horizon.items()})


def _learn(root, walked: _Walked, key: str, horizon: str, playbook: Playbook, playbook_path,
           roles: Roles) -> dict:
    """Learn from one resolved key of a sealed macro case; the history record appended."""
    if walked.records is None:
        # The first key of the case learned from: only now, after the case was sealed, is
        # its outcome opened.
        walked.outcome = load_outcome(root, walked.case_id)
        walked.records = {record["key"]: record for record in
                          score_targets(walked.analysis, walked.outcome, walked.case)}
    record = walked.records.get(key)
    if record is None:
        raise ValueError(f"train: case {walked.case_id}: its {OUTCOME_FILE} has no outcome for "
                         f"the key {key}, which its {RESOLUTION_FILE} marks resolved")
    update_weights_probs(playbook, record["prob_by_perspective"], record["actual_up"])
    entry = {"case_id": record["case_id"], "split": "train",
             **{name: value for name, value in record.items() if name != "case_id"}}
    playbook.history.append(entry)
    walked.left[horizon] -= 1
    if walked.left[horizon] == 0:
        # The last key of this horizon: one reflection on the horizon's keys.
        lessons = reflect_targets(walked.case, walked.analysis, walked.outcome, roles.reflector,
                                  keys=list(walked.by_horizon[horizon]))
        add_lessons(playbook, lessons, identity_terms(walked.case))
    save_playbook(playbook_path, playbook)
    return entry


def _train_macro(root, ids, loaded, playbook_path, roles: Roles, knowledge_cutoff, cutoff,
                 perspective_mode) -> list[dict]:
    """The macro run of train (v30), once check_mode, parse_cutoff and load_case are done.

    The remaining checks, in order, each for every case before the next, before anything
    is called or written: an id given more than once; single_domain; the playbook (it must
    weigh the macro perspectives); the dates train refuses; require_after_cutoff; every key
    resolved; no prediction.json. Then the snapshots are walked in event-date order (ties
    keep the order given), in batches of one event date b: the playbook learns from every
    waiting key resolved before b; each case of the batch is forecast and sealed as
    forecast does it; the batch's keys join the waiting ones, and the playbook learns from
    those resolved on or before b. After the last batch it learns from every key still
    waiting. Keys are learned from one at a time by resolution date, ties in the order they
    joined (_learn). Returns the history records appended, in that order. Nothing is
    masked, so seed and mask_config play no part.
    """
    _refuse_repeated(ids, "train")
    single_domain(case for _, case in loaded)
    playbook = _domain_playbook(playbook_path, "macro", "train")
    requested = _dated_cases(loaded, playbook, playbook_path, "train",
                             learned="resolution date", used="outcomes")
    for case_id, case in loaded:
        require_after_cutoff(case_id, case, knowledge_cutoff)
    _refuse_unresolved(root, loaded, "train")
    _refuse_sealed(root, loaded, "train")
    # Each key's resolution date, still before anything is called or written; sort() is
    # stable, so snapshots of one date keep the order given.
    walk = [_walked(case_id, case, when) for case_id, case, when in requested]
    walk.sort(key=lambda walked: walked.when)

    results: list[dict] = []
    waiting: list[tuple] = []  # (resolution date, _Walked, key, horizon), in joining order

    def learn(due) -> None:
        """Learn from every waiting key whose resolution date is due, by resolution date."""
        # sorted() is stable: keys that resolve on the same day keep the order they joined.
        ready = sorted((item for item in waiting if due(item[0])), key=lambda item: item[0])
        waiting[:] = [item for item in waiting if not due(item[0])]
        for _, walked, key, horizon in ready:
            results.append(_learn(root, walked, key, horizon, playbook, playbook_path, roles))

    for b, group in groupby(walk, key=lambda walked: walked.when):
        batch = list(group)
        learn(lambda resolved, b=b: resolved < b)  # known before the batch's date
        for walked in batch:  # with the playbook as it stands: no outcome of b is known
            walked.analysis = _seal_macro(root, walked.case_id, walked.case, playbook, roles,
                                          cutoff, perspective_mode)
        for walked in batch:
            waiting.extend((resolved, walked, key, horizon)
                           for key, horizon, resolved in walked.keys)
        learn(lambda resolved, b=b: resolved <= b)  # resolved on the batch's own date
    learn(lambda resolved: True)  # every key still waiting, however late it resolves
    return results
