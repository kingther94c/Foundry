"""Training and evaluation loops: mask, analyze, seal, reveal, (reflect)."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tools.stats import brier_score

from researchos.cases import identity_terms, load_case, load_outcome
from researchos.masking import (MASKED_FILE, MaskConfig, guard_documents, load_masked,
                                mask_case, save_masked)
from researchos.perspectives import Analysis, analyze
from researchos.playbook import (Playbook, add_lessons, lesson_is_clean, load_playbook,
                                 save_playbook, update_weights)
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
        "correct_weighted": (analysis.prob_weighted > 0.5) == actual_up,
        "correct_judge": (analysis.prob_judge > 0.5) == actual_up,
    }


def _get_masked(root, case, roles: Roles, rng: random.Random, mask_config: MaskConfig):
    if (Path(root) / case.case_id / MASKED_FILE).is_file():
        masked = load_masked(root, case.case_id)
    else:
        masked = mask_case(case, roles.masker, roles.guesser, rng, mask_config)
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
    path.write_text(json.dumps(asdict(analysis), indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _predict(root, case_id, playbook, roles, seed, mask_config, split):
    """Mask, analyze and seal one case. Returns (case, masked, analysis); analysis None if skipped."""
    case = load_case(root, case_id)
    rng = random.Random(f"{seed}:{case_id}")
    masked = _get_masked(root, case, roles, rng, mask_config)
    if masked.status == "identifiable":
        log.warning("%s: %s skipped, masking left it identifiable", split, case_id)
        return case, masked, None
    analysis = analyze(masked, _analysis_playbook(playbook, case), roles.analyst, roles.judge)
    _seal(root, analysis)
    return case, masked, analysis


def _open_playbook(playbook_path) -> Playbook:
    if Path(playbook_path).is_file():
        return load_playbook(playbook_path)
    return Playbook.initial()


def train(root, case_ids, playbook_path, roles: Roles, seed=0,
          mask_config: MaskConfig = MaskConfig()) -> list[dict]:
    playbook = _open_playbook(playbook_path)
    results: list[dict] = []
    for case_id in case_ids:
        case, masked, analysis = _predict(root, case_id, playbook, roles, seed, mask_config,
                                          "train")
        if analysis is None:
            playbook.history.append(
                {"case_id": case_id, "split": "train", "skipped": "identifiable"})
            save_playbook(playbook_path, playbook)
            continue
        outcome = load_outcome(root, case_id)  # only after the prediction is sealed
        record = score(analysis, outcome)
        update_weights(playbook, analysis.views, record["actual_up"])
        lessons = reflect(case, masked, analysis, outcome, roles.reflector)
        rejected = add_lessons(playbook, lessons, identity_terms(case))
        record = {"case_id": case_id, "split": "train", **record, "lessons_rejected": rejected}
        playbook.history.append(record)
        save_playbook(playbook_path, playbook)
        results.append(record)
    return results


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def evaluate(root, case_ids, playbook_path, roles: Roles, seed=0,
             mask_config: MaskConfig = MaskConfig()) -> dict:
    if Path(playbook_path).is_file():
        playbook = load_playbook(playbook_path)
    else:
        log.warning("evaluate: no playbook at %s, using the initial playbook", playbook_path)
        playbook = Playbook.initial()
    records: list[dict] = []
    for case_id in case_ids:
        _, _, analysis = _predict(root, case_id, playbook, roles, seed, mask_config, "eval")
        if analysis is None:
            continue
        outcome = load_outcome(root, case_id)  # only after the prediction is sealed
        record = score(analysis, outcome)
        y = 1 if record["actual_up"] else 0
        probs = [v.prob_up for v in analysis.views.values()]
        uniform = sum(probs) / len(probs)
        records.append({"case_id": case_id, "split": "eval", **record,
                        "brier_uniform": _brier(uniform, y)})
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
    }
