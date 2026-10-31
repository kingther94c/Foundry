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

A macro record is not known on its event date: a 1-month view of a snapshot is
known a month later (SPEC.md v29). The state learns from a record only once it
has resolved, on its `resolution_date` (its event date when it has none):
before a batch, from what resolved before the batch's date; after it, from what
resolved on or before that date. When every record resolves on its event date,
this is the rule above exactly.

Each replayed case carries `brier_blend`, so two replays (or a replay and
anything else carrying that metric) compare with `compare --metric brier_blend`.

`agreement` (SPEC.md v19) measures whether the perspectives disagree at all:
no weighting can add what seven views that move together do not have.

Both read the perspectives they are given (SPEC.md v29): `PERSPECTIVES`, the
seven of an earnings report, by default; the ten macro perspectives of
`researchos.targets` for the records a macro snapshot is scored into.
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


def _perspective_names(perspectives) -> list[str]:
    """The perspectives to read (SPEC.md v29), checked before any record is read.

    None means PERSPECTIVES; anything else must be a non-empty list of distinct
    strings, or ValueError.
    """
    if perspectives is None:
        return list(PERSPECTIVES)
    if (not isinstance(perspectives, list) or not perspectives
            or not all(isinstance(name, str) for name in perspectives)
            or len(set(perspectives)) != len(perspectives)):
        raise ValueError(f"perspectives must be a non-empty list of distinct strings, "
                         f"got {perspectives!r}")
    return list(perspectives)


class _Scored(NamedTuple):
    case_id: str
    when: date
    actual_up: bool
    probs: dict[str, float]  # exactly the perspectives read, in their order, each in [0, 1]
    resolution: date | None  # the record's resolution_date; None when it has none

    @property
    def resolves(self) -> date:
        """The day the outcome was known: the resolution date, else the event date."""
        return self.when if self.resolution is None else self.resolution


def _event_date(record: dict, case_id: str) -> date:
    value = record.get("event_date")
    # Only a string can be an event date here (parse_iso_date would also pass a date object).
    when = parse_iso_date(value) if isinstance(value, str) else None
    if when is None:
        raise ValueError(f"case {case_id}: event_date {value!r} is not an ISO date (YYYY-MM-DD), "
                         f"so the case cannot be placed in time")
    return when


def _resolution_date(record: dict, case_id: str, when: date) -> date | None:
    """The record's resolution_date (SPEC.md v29); None when the record has no such key."""
    if "resolution_date" not in record:
        return None
    value = record["resolution_date"]
    # As for event_date, only a string can be a resolution date here.
    resolved = parse_iso_date(value) if isinstance(value, str) else None
    if resolved is None:
        raise ValueError(f"case {case_id}: resolution_date {value!r} is not an ISO date "
                         f"(YYYY-MM-DD)")
    if resolved < when:
        raise ValueError(f"case {case_id}: resolution_date {resolved.isoformat()} is before "
                         f"its event_date {when.isoformat()}")
    return resolved


def _scored_cases(records, names: list[str]) -> list[_Scored]:
    """The scored records (those with brier_by_perspective), checked, in the order given.

    Each keeps the probabilities of `names` only, in that order; any other
    probability in the record is ignored.
    """
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
        resolution = _resolution_date(record, case_id, when)
        up = record.get("actual_up")
        if not isinstance(up, bool):
            raise ValueError(f"case {case_id}: actual_up {up!r} is not true or false")
        probs = perspective_probs(record)
        missing = [name for name in names
                   if not (is_number(probs.get(name)) and 0 <= probs[name] <= 1)]
        if missing:
            raise ValueError(f"case {case_id}: no probability for perspective(s) "
                             f"{', '.join(missing)}")
        scored.append(_Scored(case_id, when, up, {name: float(probs[name]) for name in names},
                              resolution))
    return scored


def _forecast(rule: str, probs: dict[str, float], names: list[str],
              weights: dict[str, float], brier_sums: dict[str, float], n_seen: int) -> float:
    """One case's blended probability over `names`, from the state learned so far."""
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
    # in the order of `names` among equals.
    if n_seen == 0:
        return uniform
    means = {name: brier_sums[name] / n_seen for name in names}
    leader = min(names, key=lambda name: means[name])
    return probs[leader]


def replay(records, rule="hedge", *, eta=0.5, perspectives=None) -> dict:
    """Replay a combination rule walk-forward over scored records.

    `perspectives` are the perspectives read: None for PERSPECTIVES, or a
    non-empty list of distinct strings; every rule is taken over them, in their
    order (SPEC.md v29). Returns {"rule", "eta", "n", "brier_blend", "cases":
    [{"case_id", "event_date", "actual_up", "prob", "brier_blend"}, ...]} with
    the cases in replay order, a case whose record has a resolution_date
    carrying it as well (YYYY-MM-DD, right after event_date); `eta` is None
    unless the rule is "hedge"; `brier_blend` is the mean of the cases' (None
    when there are none). Raises ValueError for an unknown rule, an eta that is
    not a positive number, or perspectives that are not such a list (before any
    record is read); for a scored record without an ISO event_date, an
    actual_up, or a probability for one of the perspectives, or whose
    resolution_date is not an ISO date string or is before its event_date
    (naming the case); or for a case id scored twice -- all checked before
    anything is replayed.
    """
    if rule not in RULES:
        raise ValueError(f"unknown rule {rule!r}: expected one of {', '.join(RULES)}")
    if not is_number(eta) or eta <= 0:
        raise ValueError(f"eta must be a positive number, got {eta!r}")
    names = _perspective_names(perspectives)
    # Walk forward in event-date order; sorted() is stable, so ties keep the order given.
    ordered = sorted(_scored_cases(records, names), key=lambda case: case.when)

    weights = {name: 1.0 / len(names) for name in names}  # hedge
    brier_sums = {name: 0.0 for name in names}  # leader
    n_seen = 0

    def learn(due: list[_Scored]) -> None:
        """Learn from the records of `due`, one at a time in resolution-date order.

        sorted() is stable, so records that resolve on the same day keep their
        replay order.
        """
        nonlocal weights, n_seen
        for case in sorted(due, key=lambda case: case.resolves):
            y = 1.0 if case.actual_up else 0.0
            losses = {name: (case.probs[name] - y) ** 2 for name in names}
            if rule == "hedge":
                weights = hedge_update(weights, losses, eta)
            for name in names:
                brier_sums[name] += losses[name]
            n_seen += 1

    waiting: list[_Scored] = []  # forecast but not yet learned from, in replay order
    cases: list[dict] = []
    for when, group in groupby(ordered, key=lambda case: case.when):
        batch = list(group)
        # Before the batch, the state learns from every waiting record that resolved
        # before the batch's date...
        learn([case for case in waiting if case.resolves < when])
        waiting = [case for case in waiting if case.resolves >= when]
        # ...every case of the batch is forecast with that state...
        for case in batch:
            y = 1.0 if case.actual_up else 0.0
            prob = _forecast(rule, case.probs, names, weights, brier_sums, n_seen)
            entry = {"case_id": case.case_id, "event_date": case.when.isoformat()}
            if case.resolution is not None:
                entry["resolution_date"] = case.resolution.isoformat()
            entry.update({"actual_up": case.actual_up, "prob": prob,
                          "brier_blend": (prob - y) ** 2})
            cases.append(entry)
        # ...and only then do its records join those waiting, and the state learns from
        # every waiting record that resolved on or before the batch's date. When every
        # record resolves on its event date, that is the batch itself, in batch order
        # (SPEC.md v11). A record resolving after the last batch is never learned from.
        waiting.extend(batch)
        learn([case for case in waiting if case.resolves <= when])
        waiting = [case for case in waiting if case.resolves > when]

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
    ValueErrors, `perspectives` included), in the order given; their dates are
    checked but take no part. Returns {"n", "perspectives": {name: {"mean_prob",
    "sd_prob", "brier", "calls", "hits"}}, "pairs": [{"a", "b", "correlation"},
    ...], "mean_correlation", "min_correlation"} with one entry per perspective
    read (PERSPECTIVES when `perspectives` is None), in their order, and the
    n (n - 1) / 2 pairs a-before-b in that order.
    """
    names = _perspective_names(perspectives)
    scored = _scored_cases(records, names)
    n = len(scored)
    ys = [1.0 if case.actual_up else 0.0 for case in scored]
    series = {name: [case.probs[name] for case in scored] for name in names}

    entries: dict[str, dict] = {}
    for name in names:
        probs = series[name]
        calls = hits = 0
        for p, case in zip(probs, scored):
            if p != 0.5:
                calls += 1
                if (p > 0.5) == case.actual_up:
                    hits += 1
        entries[name] = {
            "mean_prob": statistics.fmean(probs) if n else None,
            "sd_prob": statistics.pstdev(probs) if n else None,
            "brier": statistics.fmean((p - y) ** 2 for p, y in zip(probs, ys)) if n else None,
            "calls": calls,
            "hits": hits,
        }

    pairs = [{"a": a, "b": b,
              "correlation": _correlation(series[a], series[b],
                                          entries[a]["sd_prob"],
                                          entries[b]["sd_prob"])}
             for a, b in combinations(names, 2)]
    found = [pair["correlation"] for pair in pairs if pair["correlation"] is not None]
    return {
        "n": n,
        "perspectives": entries,
        "pairs": pairs,
        "mean_correlation": statistics.fmean(found) if found else None,
        "min_correlation": min(found) if found else None,
    }
