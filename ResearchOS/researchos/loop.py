"""Training and evaluation loops: mask, analyze, seal, reveal, (reflect)."""

from __future__ import annotations

import logging
import random
from dataclasses import asdict, dataclass
from datetime import date
from itertools import groupby
from pathlib import Path
from typing import Any

from tools.dates import parse_iso_date
from tools.stats import brier_score

from researchos.cases import identity_terms, load_case, load_outcome
from researchos.jsonio import write_json
from researchos.masking import (MASKED_FILE, MaskConfig, guard_documents, load_masked,
                                mask_case, save_masked)
from researchos.perspectives import Analysis, analyze
from researchos.playbook import (Playbook, add_lessons, learned_through, lesson_is_clean,
                                 load_playbook, save_playbook, update_weights)
from researchos.reflect import reflect

log = logging.getLogger("researchos")

PREDICTION_FILE = "prediction.json"


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


def _predict(root, case_id, case, playbook, roles, seed, mask_config, split, cutoff=None):
    """Mask, analyze and seal one case. Returns (masked, analysis); analysis None if skipped."""
    rng = random.Random(f"{seed}:{case_id}")
    masked = _get_masked(root, case, roles, rng, mask_config)
    if masked.status == "identifiable":
        if not _after_cutoff(case, cutoff):
            log.warning("%s: %s skipped, masking left it identifiable", split, case_id)
            return masked, None
        log.warning("%s: %s is identifiable but its event date %s is after the knowledge "
                    "cutoff %s; analyzing it", split, case_id, case.event_date, cutoff)
    analysis = analyze(masked, _analysis_playbook(playbook, case), roles.analyst, roles.judge)
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


def _requested_cases(root, case_ids, playbook: Playbook, playbook_path, command: str):
    """Load every requested case and check it can be forecast without look-ahead.

    Returns [(case_id, case, event_date)] in the order given. Raises ValueError naming
    every case whose event_date is not an ISO date, or is on or before the latest event
    date the playbook has learned from (its forecast would use reactions it could not
    have known). Nothing is called or written here, so a refusal leaves no trace.
    """
    through = learned_through(playbook)
    requested, problems = [], []
    for case_id in case_ids:
        case = load_case(root, case_id)
        try:
            when = _event_date(case_id, case)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if through is not None and when <= through:
            problems.append(
                f"case {case_id}: event_date {when.isoformat()} is on or before "
                f"{through.isoformat()}, the latest event date the playbook {playbook_path} "
                f"has learned from, so its forecast would use reactions it could not have known")
        requested.append((case_id, case, when))
    if problems:
        raise ValueError(f"{command} refused: " + "; ".join(problems))
    return requested


def train(root, case_ids, playbook_path, roles: Roles, seed=0,
          mask_config: MaskConfig = MaskConfig(), knowledge_cutoff=None) -> list[dict]:
    cutoff = parse_cutoff(knowledge_cutoff)
    playbook = _open_playbook(playbook_path)
    requested = _requested_cases(root, case_ids, playbook, playbook_path, "train")
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
                                        "train", cutoff)
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
             mask_config: MaskConfig = MaskConfig(), knowledge_cutoff=None) -> dict:
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
                                    "eval", cutoff)
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
    }
