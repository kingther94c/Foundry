"""Many targets, one analysis (SPEC.md v26): a macro snapshot through the perspectives.

A macro case is analyzed the way an earnings report is (v20): one analyst call per
perspective, then a judge. Each call covers every target and horizon of the case at once:
a reply holds one probability per target key (``cases.target_key``), the probability that
the target's level is higher at the end of the horizon than the level the case starts
from. The views are combined key by key -- averaged with the playbook's weights in the
"holistic" mode, their evidence added in log-odds in the "isolated" mode. The prompts
never show the case id, an outcome or the manifest, and no file is read or written.

Scoring and reflecting on many targets (SPEC.md v27): once some of a snapshot's targets
have resolved, ``score_targets`` gives one record per resolved target key (its case_id is
``<case id>/<key>``, with the keys an earnings record has), and ``reflect_targets`` asks
the reflector once for lessons about the resolved keys. Neither reads or writes a file.
"""

from __future__ import annotations

from dataclasses import dataclass

from tools.dates import horizon_end, parse_iso_date
from tools.stats import brier_score, combine_probabilities, pool_log_odds

from researchos.cases import (TargetOutcome, check_targets, render_documents, target_key,
                              target_keys)
from researchos.llm import LLMError
from researchos.perspectives import PERSPECTIVES, PROB_MAX, PROB_MIN, check_mode, clamp_prob
from researchos.playbook import Lesson, render_for_prompt
from researchos.reflect import REFLECT_SCHEMA
from researchos.snapshot import format_change, format_level

MACRO_PERSPECTIVES = ["growth", "inflation", "labor", "monetary_policy", "fiscal_supply",
                      "liquidity_conditions", "positioning_sentiment", "global_fx",
                      "commodities_energy", "politics_geopolitics"]
PERSPECTIVE_SETS = {"earnings": PERSPECTIVES, "macro": MACRO_PERSPECTIVES}

MACRO_PERSPECTIVE_FOCUS = {
    "growth": ("real activity: GDP and its nowcasts, industrial production, retail sales, "
               "housing, surveys of businesses and consumers, and whether growth is speeding "
               "up or slowing down."),
    "inflation": ("prices: consumer, producer and import prices, their core and services "
                  "parts, inflation expectations and breakevens, and wages as a source of "
                  "inflation."),
    "labor": ("the labor market: payrolls, unemployment, jobless claims, job openings, "
              "participation and wage growth, and whether it is tightening or loosening."),
    "monetary_policy": ("the Federal Reserve and other central banks: their statements, "
                        "projections and speeches, how they react to the data, and what the "
                        "market prices for the path of policy rates against what they are "
                        "likely to do."),
    "fiscal_supply": ("fiscal policy and the supply of government debt: deficits, Treasury "
                      "issuance and refunding, auctions, the Treasury's cash balance and the "
                      "term premium."),
    "liquidity_conditions": ("liquidity and financial conditions: the Fed's balance sheet, "
                             "bank reserves, money-market rates and funding stress, credit "
                             "spreads, bank lending and the dollar."),
    "positioning_sentiment": ("positioning and sentiment: speculative futures positions and "
                              "how they changed, how crowded a trade is, and volatility and "
                              "the appetite for risk it implies."),
    "global_fx": ("the rest of the world: growth and policy in the euro area, Japan, China "
                  "and the UK, capital flows and exchange rates, and how they reach US "
                  "markets."),
    "commodities_energy": ("commodities: the supply, demand and inventories of oil and "
                           "natural gas, OPEC, metals, and how they pass through to inflation "
                           "and growth."),
    "politics_geopolitics": ("politics and geopolitics: elections, legislation, trade policy "
                             "and tariffs, sanctions, conflicts and other shocks that can move "
                             "markets."),
}

MACRO_ANALYST_SYSTEM = (
    "You are a macro strategist at a hedge fund. You see a snapshot of the markets and the "
    "economy as of the close of a given date: the targets you forecast with their levels and "
    "recent changes, economic data, positioning, the calendar of scheduled events and "
    "official texts. Assume nothing about what happened after that date; reason only from "
    "the documents. Analyze through the single perspective you are given and, for each "
    "target and horizon, estimate the probability that the target's level is higher at the "
    "end of the horizon than the level the snapshot starts from. What the market already "
    "prices is the baseline: 0.50 means no edge over the market."
)

MACRO_JUDGE_SYSTEM = (
    "You are the portfolio manager. Several strategists have each judged a snapshot of the "
    "markets and the economy, as of the close of a given date, from one perspective. Assume "
    "nothing about what happened after that date. Weigh their views against the documents "
    "and the playbook and give, for each target and horizon, your own probability that the "
    "target's level is higher at the end of the horizon than the level the snapshot starts "
    "from, with a short thesis. 0.50 means no edge over the market."
)

MACRO_ISOLATED_ANALYST_SYSTEM = (
    "You are a macro strategist at a hedge fund. You see a snapshot of the markets and the "
    "economy as of the close of a given date: the targets you forecast with their levels and "
    "recent changes, economic data, positioning, the calendar of scheduled events and "
    "official texts. Assume nothing about what happened after that date; reason only from "
    "the documents. You judge one factor in isolation. Assume that everything outside your "
    "factor stays as the market already prices it, and estimate, for each target and "
    "horizon, the probability that the target's level is higher at the end of the horizon "
    "than the level the snapshot starts from, from your factor alone. 0.50 means your factor "
    "gives no signal either way; move away from 0.50 only as far as this factor by itself "
    "would move the target. Do not give an overall view: another strategist combines the "
    "factors."
)

MACRO_ISOLATED_JUDGE_SYSTEM = (
    "You are the portfolio manager. Several strategists have each judged one factor of a "
    "snapshot of the markets and the economy in isolation, as of the close of a given date, "
    "each as if everything else stayed as the market already prices it. Assume nothing about "
    "what happened after that date. Their probabilities are evidence from different factors: "
    "they add up rather than average, and a view at 0.50 is a factor that gives no signal. "
    "Weigh them against the documents and the playbook and give, for each target and "
    "horizon, your own probability that the target's level is higher at the end of the "
    "horizon than the level the snapshot starts from, with a short thesis."
)

MACRO_INSTRUCTION = (
    "Judge the snapshot above only from this perspective. For each target and horizon give "
    "the probability that the target's level is higher at the end of the horizon than the "
    "level the snapshot starts from, and the key points behind your view."
)

MACRO_ISOLATED_INSTRUCTION = (
    "Judge only this factor, as if everything else stayed as the market already prices it. "
    "For each target and horizon give the probability that the target's level is higher at "
    "the end of the horizon than the level the snapshot starts from, from this factor alone "
    "(0.50 if it gives no signal), and the key points behind your view."
)

MACRO_JUDGE_INSTRUCTION = (
    "Give, for each target and horizon, your own probability that the target's level is "
    "higher at the end of the horizon than the level the snapshot starts from, and your "
    "thesis."
)

# How the targets block names what a target's change measures, by kind.
MEASURES = {"bp": "change in basis points", "return": "simple return",
            "points": "change in points"}

HOLISTIC_COMBINED = "Playbook-weighted probabilities of the views:"
ISOLATED_COMBINED = "Evidence-pooled probabilities of the views (their log-odds added):"


def perspective_set(domain) -> list[str]:
    """A new list of the perspectives of `domain`, a key of PERSPECTIVE_SETS."""
    if not isinstance(domain, str) or domain not in PERSPECTIVE_SETS:
        raise ValueError(f"unknown domain {domain!r}: expected one of "
                         f"{', '.join(PERSPECTIVE_SETS)}")
    return list(PERSPECTIVE_SETS[domain])


# ---- schemas ----------------------------------------------------------------------------

def _strict_object(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def _prob_name(horizon: str) -> str:
    return f"prob_up_{horizon}"


def _checked_targets(targets) -> list:
    """The targets as a list, once cases.check_targets accepts them (its ValueError passes)."""
    try:
        items = list(targets)
    except TypeError:
        items = targets  # check_targets says what is wrong with it
    check_targets(items)
    return items


def forecasts_schema(targets) -> dict:
    """One object per target, in order, holding a number prob_up_<h> per horizon."""
    items = _checked_targets(targets)
    return _strict_object({
        target.id: _strict_object({_prob_name(h): {"type": "number"} for h in target.horizons})
        for target in items
    })


def view_schema(targets) -> dict:
    """An analyst's reply: the forecasts and the key points behind them."""
    return _strict_object({"forecasts": forecasts_schema(targets),
                           "key_points": {"type": "array", "items": {"type": "string"}}})


def judge_schema(targets) -> dict:
    """The judge's reply: the forecasts and a thesis."""
    return _strict_object({"forecasts": forecasts_schema(targets),
                           "thesis": {"type": "string"}})


# ---- results ----------------------------------------------------------------------------

@dataclass
class TargetView:
    probs: dict[str, float]  # target key -> P(up), clamped to [0.01, 0.99], in key order
    key_points: list[str]


@dataclass
class TargetAnalysis:
    case_id: str
    mode: str  # "holistic" | "isolated"
    perspectives: list[str]
    keys: list[str]  # cases.target_keys(case)
    views: dict[str, TargetView]  # one per perspective, in its order
    prob_weighted: dict[str, float]  # key -> the views combined, in key order
    prob_judge: dict[str, float]  # key -> the judge's probability, in key order
    thesis: str
    model: str = ""
    knowledge_cutoff: str = ""


def target_analysis_from_dict(data: dict) -> TargetAnalysis:
    """The inverse of dataclasses.asdict; model and knowledge_cutoff default to ""."""
    return TargetAnalysis(
        case_id=data["case_id"],
        mode=data["mode"],
        perspectives=list(data["perspectives"]),
        keys=list(data["keys"]),
        views={name: TargetView(probs=dict(view["probs"]), key_points=list(view["key_points"]))
               for name, view in data["views"].items()},
        prob_weighted=dict(data["prob_weighted"]),
        prob_judge=dict(data["prob_judge"]),
        thesis=data["thesis"],
        model=data.get("model", ""),
        knowledge_cutoff=data.get("knowledge_cutoff", ""),
    )


# ---- the analysis -----------------------------------------------------------------------

def _focus(name: str) -> str:
    return MACRO_PERSPECTIVE_FOCUS.get(name, name.replace("_", " "))


def _check_perspectives(perspectives) -> None:
    if (not isinstance(perspectives, list) or not perspectives
            or not all(isinstance(name, str) for name in perspectives)
            or len(set(perspectives)) != len(perspectives)):
        raise ValueError(f"perspectives must be a non-empty list of distinct strings, "
                         f"got {perspectives!r}")


def _targets_block(targets) -> str:
    lines = ["Targets:"]
    for target in targets:
        lines.append(f"- {target.id}: {target.name}; {MEASURES[target.kind]}; from "
                     f"{format_level(target.as_of_value)} on {target.as_of_date}; "
                     f"horizons {', '.join(target.horizons)}")
    return "\n".join(lines)


def _read_forecasts(output, targets, who: str) -> dict[str, float]:
    """Every key's probability in a reply, clamped; LLMError naming `who` otherwise."""
    if not isinstance(output, dict):
        raise LLMError(f"{who} returned a non-object")
    forecasts = output.get("forecasts")
    if not isinstance(forecasts, dict):
        raise LLMError(f"{who} returned forecasts that are not an object: {forecasts!r}")
    probs: dict[str, float] = {}
    for target in targets:
        entry = forecasts.get(target.id)
        if not isinstance(entry, dict):
            raise LLMError(f"{who} gave no forecasts object for target {target.id}: {entry!r}")
        for horizon in target.horizons:
            key = target_key(target.id, horizon)
            probs[key] = clamp_prob(entry.get(_prob_name(horizon)), f"{who} for {key}")
    return probs


def _view_from_output(output, targets, name: str) -> TargetView:
    probs = _read_forecasts(output, targets, f"analyst ({name})")
    points = output.get("key_points")
    return TargetView(
        probs=probs,
        key_points=[p for p in points if isinstance(p, str)] if isinstance(points, list) else [],
    )


def _render_view(name: str, view: TargetView, keys: list[str]) -> str:
    lines = [f"### {name}",
             "P(up): " + ", ".join(f"{key} {view.probs[key]:.2f}" for key in keys)]
    lines.extend(f"- {point}" for point in view.key_points)
    return "\n".join(lines)


def _combine(probs: dict[str, float], weights, isolated: bool) -> float:
    if isolated:
        # Views of separate factors are separate evidence: add their log-odds.
        pooled = pool_log_odds(probs, weights)
        if pooled is None:  # no perspective has a positive weight
            pooled = pool_log_odds(probs)
        return min(max(float(pooled), PROB_MIN), PROB_MAX)
    combined = combine_probabilities(probs, weights)
    if combined is None:
        return sum(probs.values()) / len(probs)
    return combined


def analyze_targets(masked, case, playbook, analyst, judge, *, perspectives=MACRO_PERSPECTIVES,
                    mode="holistic") -> TargetAnalysis:
    """One analyst call per perspective over every target and horizon, then one judge call.

    `masked` is what the analysts read (masking.unmasked(case) for a macro case); `case`
    gives the targets and the date. The case id is sent in no prompt.
    """
    check_mode(mode)
    if case.domain != "macro":
        raise ValueError(f"case {case.case_id!r} is not a macro case (its domain is "
                         f"{case.domain!r})")
    _check_perspectives(perspectives)
    keys = target_keys(case)
    targets = list(case.targets)
    analyst_schema = view_schema(targets)
    verdict_schema = judge_schema(targets)

    isolated = mode == "isolated"
    names = list(perspectives)
    prefix = f"Macro snapshot as of {case.event_date}:\n\n" + render_documents(masked.documents)
    block = _targets_block(targets)
    playbook_text = render_for_prompt(playbook)

    analyst_system = MACRO_ISOLATED_ANALYST_SYSTEM if isolated else MACRO_ANALYST_SYSTEM
    views: dict[str, TargetView] = {}
    for name in names:
        if isolated:
            head = f"Perspective: {name}\nYour factor: {_focus(name)}\n{MACRO_ISOLATED_INSTRUCTION}"
        else:
            head = f"Perspective: {name}\nLook at {_focus(name)}\n{MACRO_INSTRUCTION}"
        prompt = f"{head}\n\n{block}\n\n{playbook_text}"
        output = analyst.json(analyst_system, prompt, analyst_schema, prefix=prefix)
        views[name] = _view_from_output(output, targets, name)

    prob_weighted = {
        key: _combine({name: views[name].probs[key] for name in names}, playbook.weights,
                      isolated)
        for key in keys
    }

    combined_part = "\n".join([ISOLATED_COMBINED if isolated else HOLISTIC_COMBINED]
                              + [f"- {key}: {prob_weighted[key]:.3f}" for key in keys])
    parts = ["The strategists' views:"]
    parts.extend(_render_view(name, views[name], keys) for name in names)
    parts.extend([combined_part, block, playbook_text, MACRO_JUDGE_INSTRUCTION])
    judge_system = MACRO_ISOLATED_JUDGE_SYSTEM if isolated else MACRO_JUDGE_SYSTEM
    verdict = judge.json(judge_system, "\n\n".join(parts), verdict_schema, prefix=prefix)
    prob_judge = _read_forecasts(verdict, targets, "judge")
    thesis = verdict.get("thesis")

    return TargetAnalysis(
        case_id=case.case_id,
        mode=mode,
        perspectives=names,
        keys=keys,
        views=views,
        prob_weighted=prob_weighted,
        prob_judge=prob_judge,
        thesis=thesis if isinstance(thesis, str) else "",
    )


# ---- scoring and reflecting on many targets (v27) ---------------------------------------

MACRO_REFLECTOR_SYSTEM = (
    "You are the reflector in a research pipeline that trains macro strategists to forecast "
    "where market levels go in the weeks and months after a snapshot of the markets and the "
    "economy. The forecasts were sealed before the outcomes were known; now you see both. "
    "Explain why each target moved the way it did, where the forecasts went wrong or right, "
    "and write lessons that would hold on other dates. A lesson must not make a rule of a "
    "particular date, level or event: it is shown to the strategists on every later "
    "snapshot. Each lesson says when it applies and which perspective it belongs to."
)

MACRO_REFLECT_REQUEST = (
    "Why did these targets move the way they did? Where were the forecasts wrong, and where "
    "right? Write lessons that would hold on other dates; never make a rule of a particular "
    "date, level or event."
)


def _check_analysis(analysis, case) -> None:
    """ValueError unless the analysis is of this case and of its targets, in their order."""
    if analysis.case_id != case.case_id:
        raise ValueError(f"the analysis is of case {analysis.case_id!r}, not of case "
                         f"{case.case_id!r}")
    keys = target_keys(case)  # its ValueError passes through
    if analysis.keys != keys:
        raise ValueError(f"the analysis of case {case.case_id!r} has the keys "
                         f"{analysis.keys!r}, not the keys of the case's targets {keys!r}")


def _key_targets(case) -> dict:
    """target key -> (the Target, the horizon), for every key of the case."""
    return {target_key(target.id, horizon): (target, horizon)
            for target in case.targets for horizon in target.horizons}


def _target_outcome(outcome, key):
    """The key's TargetOutcome in outcome.targets; None when it has none (not resolved yet)."""
    found = outcome.targets.get(key)
    return found if isinstance(found, TargetOutcome) else None


def _resolution_date(case, horizon: str) -> str:
    """horizon_end(<event_date>, horizon) as YYYY-MM-DD."""
    value = case.event_date
    when = parse_iso_date(value) if isinstance(value, str) else None
    if when is None:
        raise ValueError(f"case {case.case_id}: event_date {value!r} is not an ISO date "
                         f"(YYYY-MM-DD)")
    return horizon_end(when, horizon).isoformat()


def _brier(p: float, y: int) -> float:
    return brier_score([p], [y])


def score_targets(analysis, outcome, case) -> list[dict]:
    """One record per key of analysis.keys, in that order, that has a TargetOutcome.

    A key without one is not scored yet and gives no record. Raises ValueError when the
    analysis is of another case, or of other targets (analysis.keys is not
    target_keys(case)). A record holds no text from the documents or the analysis.
    """
    _check_analysis(analysis, case)
    by_key = _key_targets(case)
    names = list(analysis.perspectives)
    records: list[dict] = []
    for key in analysis.keys:
        resolved = _target_outcome(outcome, key)
        if resolved is None:
            continue  # not scored yet
        target, horizon = by_key[key]
        y = 1 if resolved.up else 0
        prob_weighted = analysis.prob_weighted[key]
        prob_judge = analysis.prob_judge[key]
        probs = {name: analysis.views[name].probs[key] for name in names}
        prob_uniform = sum(probs.values()) / len(probs)
        records.append({
            "case_id": f"{case.case_id}/{key}",
            "case": case.case_id,
            "key": key,
            "target": target.id,
            "horizon": horizon,
            "kind": target.kind,
            "event_date": case.event_date,
            "resolution_date": _resolution_date(case, horizon),
            "start_date": resolved.start_date,
            "start": resolved.start,
            "end_date": resolved.end_date,
            "end": resolved.end,
            "change": resolved.change,
            "trailing_change": target.trailing.get(horizon),
            "actual_up": resolved.up,
            "prob_weighted": prob_weighted,
            "prob_judge": prob_judge,
            "prob_uniform": prob_uniform,
            "prob_by_perspective": probs,
            "brier_weighted": _brier(prob_weighted, y),
            "brier_judge": _brier(prob_judge, y),
            "brier_uniform": _brier(prob_uniform, y),
            "brier_coin": 0.25,
            "brier_by_perspective": {name: _brier(p, y) for name, p in probs.items()},
            "correct_weighted": (prob_weighted > 0.5) == resolved.up,
            "correct_judge": (prob_judge > 0.5) == resolved.up,
        })
    return records


def _reflect_keys(analysis, outcome, keys) -> list[str]:
    """The keys to reflect on, in the order of analysis.keys, each once."""
    if keys is None:
        return [key for key in analysis.keys if _target_outcome(outcome, key) is not None]
    if isinstance(keys, str):
        raise ValueError(f"keys must be a list of target keys, not the string {keys!r}")
    try:
        wanted = list(keys)
    except TypeError:
        raise ValueError(f"keys must be a list of target keys, got {keys!r}") from None
    for key in wanted:
        if key not in analysis.keys:
            raise ValueError(f"key {key!r} is not one of the analysis's keys "
                             f"({', '.join(analysis.keys)})")
        if _target_outcome(outcome, key) is None:
            raise ValueError(f"key {key!r} has no TargetOutcome in the outcome: it has not "
                             f"resolved yet")
    return [key for key in analysis.keys if key in wanted]


def _render_key(key: str, target, horizon: str, analysis, resolved) -> str:
    direction = "up" if resolved.up else "not up"
    return "\n".join([
        f"### {key} ({target.name}, {horizon})",
        "Perspectives: " + ", ".join(f"{name} {analysis.views[name].probs[key]:.2f}"
                                     for name in analysis.perspectives),
        f"Combined: {analysis.prob_weighted[key]:.3f}; judge: {analysis.prob_judge[key]:.3f}",
        f"Outcome: from {format_level(resolved.start)} on {resolved.start_date} to "
        f"{format_level(resolved.end)} on {resolved.end_date}, "
        f"{format_change(resolved.change, resolved.kind)}: {direction}",
    ])


def _lessons_from_output(output, names: list[str], case_id: str) -> list[Lesson]:
    """The reply's lessons, read as reflect.reflect reads its own."""
    items = output.get("lessons") if isinstance(output, dict) else None
    if not isinstance(items, list):
        return []
    lessons: list[Lesson] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = item.get("lesson")
        if not isinstance(text, str) or not text.strip():
            continue
        applies_when = item.get("applies_when")
        perspective = item.get("perspective")
        perspective = perspective.strip().lower() if isinstance(perspective, str) else ""
        if perspective not in names:
            perspective = "general"
        lessons.append(Lesson(
            text=text.strip(),
            applies_when=applies_when.strip() if isinstance(applies_when, str) else "",
            perspective=perspective,
            case_id=case_id,
        ))
    return lessons


def reflect_targets(case, analysis, outcome, reflector, *, keys=None) -> list[Lesson]:
    """One reflector call after some of the case's targets have resolved.

    `keys` are the keys to reflect on (None: every key of analysis.keys with a
    TargetOutcome), taken in the order of analysis.keys, each once. The checks of
    score_targets come first; a key that is not in analysis.keys, or has no TargetOutcome,
    raises ValueError before any call. With no key to reflect on, returns [] uncalled.
    """
    _check_analysis(analysis, case)
    chosen = _reflect_keys(analysis, outcome, keys)
    if not chosen:
        return []
    by_key = _key_targets(case)
    names = list(analysis.perspectives)
    parts = [f"Snapshot as of {case.event_date}: the sealed forecasts and what happened."]
    for key in chosen:
        target, horizon = by_key[key]
        parts.append(_render_key(key, target, horizon, analysis, outcome.targets[key]))
    parts.append(f"Judge's thesis: {analysis.thesis}")
    parts.append("Documents\n\n" + render_documents(case.documents))
    parts.append(MACRO_REFLECT_REQUEST)
    parts.append(f"Each lesson's perspective is one of: {', '.join(names)}, or \"general\".")
    output = reflector.json(MACRO_REFLECTOR_SYSTEM, "\n\n".join(parts), REFLECT_SCHEMA)
    return _lessons_from_output(output, names, case.case_id)
