"""How should the perspectives be combined? (SPEC.md "researchos.blend (v11)")

The playbook combines the perspectives with Hedge weights learned case by case.
Whether another rule would have explained the reactions better can be answered
from records already scored, without calling a model again: `replay` runs a
rule walk-forward over them, every forecast using only what earlier event dates
taught.

Records are taken in event-date order (ties keep the order given), in batches
of one event date. Every case of a batch is forecast with the state from before
the batch, and the state is updated only after the whole batch: cases that
reacted on the same day could not have taught one another anything.

Each replayed case carries `brier_blend`, so two replays (or a replay and
anything else carrying that metric) compare with `compare --metric brier_blend`.
"""

from __future__ import annotations

import math
import statistics
from datetime import date
from itertools import groupby
from typing import NamedTuple

from tools.stats import combine_probabilities, hedge_update

from researchos.perspectives import PERSPECTIVES

RULES = ("uniform", "median", "hedge", "leader")


def _is_number(value) -> bool:
    """A finite int or float; a bool is not a number here."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def perspective_probs(record) -> dict[str, float]:
    """Each perspective's prob_up in one scored record.

    `record["prob_by_perspective"]` when present (records scored since v11).
    Older records only carry `brier_by_perspective` and `actual_up`: a Brier
    score is (p - y) ** 2 with p in [0, 1], so p = 1 - sqrt(b) when the stock
    went up and p = sqrt(b) when it fell. A perspective whose probability
    cannot be recovered (its Brier score is not a number in [0, 1], or
    `actual_up` is not a bool) is left out; `replay` names the case.
    """
    probs = record.get("prob_by_perspective")
    if probs is not None:
        return dict(probs) if isinstance(probs, dict) else {}
    briers = record.get("brier_by_perspective")
    up = record.get("actual_up")
    if not isinstance(briers, dict) or not isinstance(up, bool):
        return {}
    derived: dict[str, float] = {}
    for name, b in briers.items():
        if _is_number(b) and 0 <= b <= 1:
            distance = math.sqrt(b)  # |p - y|
            derived[name] = 1.0 - distance if up else distance
    return derived


class _Scored(NamedTuple):
    case_id: str
    when: date
    actual_up: bool
    probs: dict[str, float]  # exactly PERSPECTIVES, each a number in [0, 1]


def _event_date(record: dict, case_id: str) -> date:
    value = record.get("event_date")
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise ValueError(f"case {case_id}: event_date {value!r} is not an ISO date (YYYY-MM-DD), "
                     f"so the case cannot be placed in time")


def _scored_cases(records) -> list[_Scored]:
    """The scored records (those with brier_by_perspective), checked, in the order given."""
    scored: list[_Scored] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("brier_by_perspective"),
                                                          dict):
            continue  # not scored, e.g. skipped as identifiable
        case_id = record.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"a scored record has no case_id: {case_id!r}")
        if case_id in seen:
            raise ValueError(f"case {case_id} is scored twice")
        seen.add(case_id)
        when = _event_date(record, case_id)
        up = record.get("actual_up")
        if not isinstance(up, bool):
            raise ValueError(f"case {case_id}: actual_up {up!r} is not true or false")
        probs = perspective_probs(record)
        missing = [name for name in PERSPECTIVES
                   if not (_is_number(probs.get(name)) and 0 <= probs[name] <= 1)]
        if missing:
            raise ValueError(f"case {case_id}: no probability for perspective(s) "
                             f"{', '.join(missing)}")
        scored.append(_Scored(case_id, when, up, {name: float(probs[name])
                                                  for name in PERSPECTIVES}))
    return scored


def _forecast(rule: str, probs: dict[str, float], weights: dict[str, float],
              brier_sums: dict[str, float], n_seen: int) -> float:
    """One case's blended probability, from the state left by earlier batches."""
    values = [probs[name] for name in PERSPECTIVES]
    uniform = sum(values) / len(values)
    if rule == "uniform":
        return uniform
    if rule == "median":
        return float(statistics.median(values))
    if rule == "hedge":
        combined = combine_probabilities(probs, weights)
        return uniform if combined is None else combined
    # leader: the perspective with the lowest mean Brier so far; min keeps the first
    # in PERSPECTIVES order among equals.
    if n_seen == 0:
        return uniform
    means = {name: brier_sums[name] / n_seen for name in PERSPECTIVES}
    leader = min(PERSPECTIVES, key=lambda name: means[name])
    return probs[leader]


def replay(records, rule="hedge", *, eta=0.5) -> dict:
    """Replay a combination rule walk-forward over scored records.

    Returns {"rule", "eta", "n", "brier_blend", "cases": [{"case_id",
    "event_date", "actual_up", "prob", "brier_blend"}, ...]} with the cases in
    replay order; `eta` is None unless the rule is "hedge"; `brier_blend` is
    the mean of the cases' (None when there are none). Raises ValueError for
    an unknown rule, an eta that is not a positive number, or a scored record
    without an ISO event_date, an actual_up, or a probability for one of
    PERSPECTIVES (naming the case), or a case id scored twice -- all checked
    before anything is replayed.
    """
    if rule not in RULES:
        raise ValueError(f"unknown rule {rule!r}: expected one of {', '.join(RULES)}")
    if not _is_number(eta) or eta <= 0:
        raise ValueError(f"eta must be a positive number, got {eta!r}")
    # Walk forward in event-date order; sorted() is stable, so ties keep the order given.
    ordered = sorted(_scored_cases(records), key=lambda case: case.when)

    weights = {name: 1.0 / len(PERSPECTIVES) for name in PERSPECTIVES}  # hedge
    brier_sums = {name: 0.0 for name in PERSPECTIVES}                    # leader
    n_seen = 0
    cases: list[dict] = []
    for _, group in groupby(ordered, key=lambda case: case.when):
        batch = list(group)
        # Every case of the batch is forecast with the state from before the batch...
        for case in batch:
            y = 1.0 if case.actual_up else 0.0
            prob = _forecast(rule, case.probs, weights, brier_sums, n_seen)
            cases.append({"case_id": case.case_id, "event_date": case.when.isoformat(),
                          "actual_up": case.actual_up, "prob": prob,
                          "brier_blend": (prob - y) ** 2})
        # ...and only then does the state learn from it, one case at a time in batch order.
        for case in batch:
            y = 1.0 if case.actual_up else 0.0
            losses = {name: (case.probs[name] - y) ** 2 for name in PERSPECTIVES}
            if rule == "hedge":
                weights = hedge_update(weights, losses, eta)
            for name in PERSPECTIVES:
                brier_sums[name] += losses[name]
            n_seen += 1

    n = len(cases)
    return {
        "rule": rule,
        "eta": eta if rule == "hedge" else None,
        "n": n,
        "brier_blend": sum(c["brier_blend"] for c in cases) / n if n else None,
        "cases": cases,
    }
