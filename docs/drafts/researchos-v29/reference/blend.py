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

`agreement` (SPEC.md v19) measures whether the perspectives disagree at all:
no weighting can add what seven views that move together do not have.
"""

from __future__ import annotations

import math
import statistics
from datetime import date
from itertools import combinations, groupby
from typing import NamedTuple

from tools.dates import parse_iso_date
from tools.stats import combine_probabilities, hedge_update, is_number, pool_log_odds

from researchos.perspectives import PERSPECTIVES, PROB_MAX, PROB_MIN

RULES = ("uniform", "median", "hedge", "leader", "pool")


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
        if is_number(b) and 0 <= b <= 1:
            distance = math.sqrt(b)  # |p - y|
            derived[name] = 1.0 - distance if up else distance
    return derived


class _Scored(NamedTuple):
    case_id: str
    when: date
    actual_up: bool
    probs: dict[str, float]  # exactly the perspectives read, each a number in [0, 1]
    resolved: date  # the resolution date (v29): the event date unless the record has one
    has_resolution: bool


def _names(perspectives) -> list[str]:
    """The perspectives to read: PERSPECTIVES for None, else a non-empty list of distinct str."""
    if perspectives is None:
        return list(PERSPECTIVES)
    if (not isinstance(perspectives, list) or not perspectives
            or not all(isinstance(name, str) for name in perspectives)
            or len(set(perspectives)) != len(perspectives)):
        raise ValueError(f"perspectives must be a non-empty list of distinct strings, got "
                         f"{perspectives!r}")
    return list(perspectives)


def _event_date(record: dict, case_id: str) -> date:
    value = record.get("event_date")
    # Only a string can be an event date here (parse_iso_date would also pass a date object).
    when = parse_iso_date(value) if isinstance(value, str) else None
    if when is None:
        raise ValueError(f"case {case_id}: event_date {value!r} is not an ISO date (YYYY-MM-DD), "
                         f"so the case cannot be placed in time")
    return when


def _scored_cases(records, names) -> list[_Scored]:
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
        missing = [name for name in names
                   if not (is_number(probs.get(name)) and 0 <= probs[name] <= 1)]
        if missing:
            raise ValueError(f"case {case_id}: no probability for perspective(s) "
                             f"{', '.join(missing)}")
        has_resolution = "resolution_date" in record
        resolved = when
        if has_resolution:
            value = record["resolution_date"]
            resolved = parse_iso_date(value) if isinstance(value, str) else None
            if resolved is None:
                raise ValueError(f"case {case_id}: resolution_date {value!r} is not an ISO "
                                 f"date (YYYY-MM-DD)")
            if resolved < when:
                raise ValueError(f"case {case_id}: resolution_date {resolved.isoformat()} is "
                                 f"before its event date {when.isoformat()}")
        scored.append(_Scored(case_id, when, up, {name: float(probs[name]) for name in names},
                              resolved, has_resolution))
    return scored


def _forecast(rule: str, probs: dict[str, float], weights: dict[str, float],
              brier_sums: dict[str, float], n_seen: int, names: list[str]) -> float:
    """One case's blended probability, from the state left by earlier batches."""
    values = [probs[name] for name in names]
    uniform = sum(values) / len(values)
    if rule == "uniform":
        return uniform
    if rule == "median":
        return float(statistics.median(values))
    if rule == "hedge":
        combined = combine_probabilities(probs, weights)
        return uniform if combined is None else combined
    if rule == "pool":
        # Evidence added in log-odds (SPEC.md v20); learns nothing from earlier cases.
        pooled = pool_log_odds({name: probs[name] for name in names})
        return min(max(float(pooled), PROB_MIN), PROB_MAX)
    # leader: the perspective with the lowest mean Brier so far; min keeps the first
    # in PERSPECTIVES order among equals.
    if n_seen == 0:
        return uniform
    means = {name: brier_sums[name] / n_seen for name in names}
    leader = min(names, key=lambda name: means[name])
    return probs[leader]


def replay(records, rule="hedge", *, eta=0.5, perspectives=None) -> dict:
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
    if not is_number(eta) or eta <= 0:
        raise ValueError(f"eta must be a positive number, got {eta!r}")
    names = _names(perspectives)
    # Walk forward in event-date order; sorted() is stable, so ties keep the order given.
    ordered = sorted(_scored_cases(records, names), key=lambda case: case.when)

    state = {"weights": {name: 1.0 / len(names) for name in names},  # hedge
             "brier_sums": {name: 0.0 for name in names},             # leader
             "n_seen": 0}
    waiting: list[_Scored] = []

    def learn(ready) -> None:
        for case in ready:
            y = 1.0 if case.actual_up else 0.0
            losses = {name: (case.probs[name] - y) ** 2 for name in names}
            if rule == "hedge":
                state["weights"] = hedge_update(state["weights"], losses, eta)
            for name in names:
                state["brier_sums"][name] += losses[name]
            state["n_seen"] += 1

    def take(due) -> list[_Scored]:
        ready = sorted((case for case in waiting if due(case.resolved)),
                       key=lambda case: case.resolved)
        waiting[:] = [case for case in waiting if not due(case.resolved)]
        return ready

    cases: list[dict] = []
    for when, group in groupby(ordered, key=lambda case: case.when):
        batch = list(group)
        learn(take(lambda r: r < when))
        # Every case of the batch is forecast with the state from before the batch...
        for case in batch:
            y = 1.0 if case.actual_up else 0.0
            prob = _forecast(rule, case.probs, state["weights"], state["brier_sums"],
                             state["n_seen"], names)
            entry = {"case_id": case.case_id, "event_date": case.when.isoformat()}
            if case.has_resolution:
                entry["resolution_date"] = case.resolved.isoformat()
            entry.update({"actual_up": case.actual_up, "prob": prob,
                          "brier_blend": (prob - y) ** 2})
            cases.append(entry)
        # ...and the state learns from a record only once it has resolved.
        waiting.extend(batch)
        learn(take(lambda r: r <= when))

    n = len(cases)
    return {
        "rule": rule,
        "eta": eta if rule == "hedge" else None,
        "n": n,
        "brier_blend": sum(c["brier_blend"] for c in cases) / n if n else None,
        "cases": cases,
    }


def _correlation(xs: list[float], ys: list[float], sd_x, sd_y) -> float | None:
    """Pearson correlation; None for fewer than two values or a population sd of 0."""
    if len(xs) < 2 or not sd_x or not sd_y:
        return None
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:  # deviations so small their product underflows
        return None


def agreement(records, *, perspectives=None) -> dict:
    """Do the perspectives disagree? Their spread, accuracy and pairwise correlations.

    Over the scored records `replay` accepts (checked the same way, so the same
    ValueErrors), in the order given. Returns {"n", "perspectives": {name:
    {"mean_prob", "sd_prob", "brier", "calls", "hits"}}, "pairs": [{"a", "b",
    "correlation"}, ...], "mean_correlation", "min_correlation"} with the
    perspectives in PERSPECTIVES order and the 21 pairs a-before-b in that order.
    """
    names = _names(perspectives)
    scored = _scored_cases(records, names)
    n = len(scored)
    ys = [1.0 if case.actual_up else 0.0 for case in scored]
    series = {name: [case.probs[name] for case in scored] for name in names}

    perspectives: dict[str, dict] = {}
    for name in names:
        probs = series[name]
        calls = hits = 0
        for p, case in zip(probs, scored):
            if p != 0.5:
                calls += 1
                if (p > 0.5) == case.actual_up:
                    hits += 1
        perspectives[name] = {
            "mean_prob": statistics.fmean(probs) if n else None,
            "sd_prob": statistics.pstdev(probs) if n else None,
            "brier": statistics.fmean((p - y) ** 2 for p, y in zip(probs, ys)) if n else None,
            "calls": calls,
            "hits": hits,
        }

    pairs = [{"a": a, "b": b,
              "correlation": _correlation(series[a], series[b],
                                          perspectives[a]["sd_prob"],
                                          perspectives[b]["sd_prob"])}
             for a, b in combinations(names, 2)]
    found = [pair["correlation"] for pair in pairs if pair["correlation"] is not None]
    return {
        "n": n,
        "perspectives": perspectives,
        "pairs": pairs,
        "mean_correlation": statistics.fmean(found) if found else None,
        "min_correlation": min(found) if found else None,
    }
