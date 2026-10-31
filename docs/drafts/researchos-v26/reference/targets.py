"""Many targets, one analysis (v26 reference)."""

from __future__ import annotations

from dataclasses import dataclass

from tools.stats import combine_probabilities, pool_log_odds

from researchos.cases import render_documents, target_key, target_keys, check_targets
from researchos.llm import LLMError
from researchos.perspectives import PERSPECTIVES, PROB_MAX, PROB_MIN, check_mode, clamp_prob

MACRO_PERSPECTIVES = ["growth", "inflation", "labor", "monetary_policy", "fiscal_supply",
                      "liquidity_conditions", "positioning_sentiment", "global_fx",
                      "commodities_energy", "politics_geopolitics"]
PERSPECTIVE_SETS = {"earnings": PERSPECTIVES, "macro": MACRO_PERSPECTIVES}

MACRO_PERSPECTIVE_FOCUS = {
    "growth": "real activity: GDP and its nowcasts, industrial production, retail sales, "
              "housing, surveys of businesses and consumers, and whether growth is speeding "
              "up or slowing down.",
    "inflation": "prices: consumer, producer and import prices, their core and services "
                 "parts, inflation expectations and breakevens, and wages as a source of "
                 "inflation.",
    "labor": "the labor market: payrolls, unemployment, jobless claims, job openings, "
             "participation and wage growth, and whether it is tightening or loosening.",
    "monetary_policy": "the Federal Reserve and other central banks: their statements, "
                       "projections and speeches, how they react to the data, and what the "
                       "market prices for the path of policy rates against what they are "
                       "likely to do.",
    "fiscal_supply": "fiscal policy and the supply of government debt: deficits, Treasury "
                     "issuance and refunding, auctions, the Treasury's cash balance and the "
                     "term premium.",
    "liquidity_conditions": "liquidity and financial conditions: the Fed's balance sheet, "
                            "bank reserves, money-market rates and funding stress, credit "
                            "spreads, bank lending and the dollar.",
    "positioning_sentiment": "positioning and sentiment: speculative futures positions and "
                             "how they changed, how crowded a trade is, and volatility and "
                             "the appetite for risk it implies.",
    "global_fx": "the rest of the world: growth and policy in the euro area, Japan, China "
                 "and the UK, capital flows and exchange rates, and how they reach US "
                 "markets.",
    "commodities_energy": "commodities: the supply, demand and inventories of oil and "
                          "natural gas, OPEC, metals, and how they pass through to inflation "
                          "and growth.",
    "politics_geopolitics": "politics and geopolitics: elections, legislation, trade policy "
                            "and tariffs, sanctions, conflicts and other shocks that can move "
                            "markets.",
}

_SNAPSHOT = ("You see a snapshot of the markets and the economy as of the close of a given "
             "date: the targets you forecast with their levels and recent changes, economic "
             "data, positioning, the calendar of scheduled events and official texts. Assume "
             "nothing about what happened after that date; reason only from the documents. ")
_HIGHER = ("the probability that the target's level is higher at the end of the horizon than "
           "the level the snapshot starts from")

MACRO_ANALYST_SYSTEM = (
    "You are a macro strategist at a hedge fund. " + _SNAPSHOT
    + "Analyze through the single perspective you are given and, for each target and horizon, "
    "estimate " + _HIGHER + ". What the market already prices is the baseline: 0.50 means no "
    "edge over the market.")

MACRO_JUDGE_SYSTEM = (
    "You are the portfolio manager. Several strategists have each judged a snapshot of the "
    "markets and the economy, as of the close of a given date, from one perspective. Assume "
    "nothing about what happened after that date. Weigh their views against the documents "
    "and the playbook and give, for each target and horizon, your own probability that the "
    "target's level is higher at the end of the horizon than the level the snapshot starts "
    "from, with a short thesis. 0.50 means no edge over the market.")

MACRO_ISOLATED_ANALYST_SYSTEM = (
    "You are a macro strategist at a hedge fund. " + _SNAPSHOT
    + "You judge one factor in isolation. Assume that everything outside your factor stays as "
    "the market already prices it, and estimate, for each target and horizon, " + _HIGHER
    + ", from your factor alone. 0.50 means your factor gives no signal either way; move away "
    "from 0.50 only as far as this factor by itself would move the target. Do not give an "
    "overall view: another strategist combines the factors.")

MACRO_ISOLATED_JUDGE_SYSTEM = (
    "You are the portfolio manager. Several strategists have each judged one factor of a "
    "snapshot of the markets and the economy in isolation, as of the close of a given date, "
    "each as if everything else stayed as the market already prices it. Assume nothing about "
    "what happened after that date. Their probabilities are evidence from different factors: "
    "they add up rather than average, and a view at 0.50 is a factor that gives no signal. "
    "Weigh them against the documents and the playbook and give, for each target and "
    "horizon, your own probability that the target's level is higher at the end of the "
    "horizon than the level the snapshot starts from, with a short thesis.")

MACRO_INSTRUCTION = (
    "Judge the snapshot above only from this perspective. For each target and horizon give "
    + _HIGHER + ", and the key points behind your view.")

MACRO_ISOLATED_INSTRUCTION = (
    "Judge only this factor, as if everything else stayed as the market already prices it. "
    "For each target and horizon give " + _HIGHER + ", from this factor alone (0.50 if it "
    "gives no signal), and the key points behind your view.")

_MEASURES = {"bp": "change in basis points", "return": "simple return",
             "points": "change in points"}


def perspective_set(domain) -> list:
    if not isinstance(domain, str) or domain not in PERSPECTIVE_SETS:
        raise ValueError(f"no perspectives for domain {domain!r}")
    return list(PERSPECTIVE_SETS[domain])


def _strict(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def forecasts_schema(targets) -> dict:
    check_targets(targets)
    return _strict({t.id: _strict({f"prob_up_{h}": {"type": "number"} for h in t.horizons})
                    for t in targets})


def view_schema(targets) -> dict:
    return _strict({"forecasts": forecasts_schema(targets),
                    "key_points": {"type": "array", "items": {"type": "string"}}})


def judge_schema(targets) -> dict:
    return _strict({"forecasts": forecasts_schema(targets), "thesis": {"type": "string"}})


@dataclass
class TargetView:
    probs: dict
    key_points: list


@dataclass
class TargetAnalysis:
    case_id: str
    mode: str
    perspectives: list
    keys: list
    views: dict
    prob_weighted: dict
    prob_judge: dict
    thesis: str
    model: str = ""
    knowledge_cutoff: str = ""


def target_analysis_from_dict(data) -> TargetAnalysis:
    return TargetAnalysis(
        case_id=data["case_id"], mode=data["mode"], perspectives=list(data["perspectives"]),
        keys=list(data["keys"]),
        views={name: TargetView(probs=dict(v["probs"]), key_points=list(v["key_points"]))
               for name, v in data["views"].items()},
        prob_weighted=dict(data["prob_weighted"]), prob_judge=dict(data["prob_judge"]),
        thesis=data["thesis"], model=data.get("model", ""),
        knowledge_cutoff=data.get("knowledge_cutoff", ""))


def _focus(name: str) -> str:
    return MACRO_PERSPECTIVE_FOCUS.get(name, name.replace("_", " "))


def _level(value) -> str:
    from researchos.snapshot import format_level

    return format_level(value)


def _targets_block(case) -> str:
    lines = ["Targets:"]
    for t in case.targets:
        lines.append(f"- {t.id}: {t.name}; {_MEASURES[t.kind]}; from {_level(t.as_of_value)} "
                     f"on {t.as_of_date}; horizons {', '.join(t.horizons)}")
    return "\n".join(lines)


def _read_forecasts(output, case, who: str) -> dict:
    if not isinstance(output, dict):
        raise LLMError(f"{who} returned a non-object")
    forecasts = output.get("forecasts")
    if not isinstance(forecasts, dict):
        raise LLMError(f"{who} returned no forecasts object")
    probs = {}
    for t in case.targets:
        entry = forecasts.get(t.id)
        if not isinstance(entry, dict):
            raise LLMError(f"{who} gave no forecasts for target {t.id}")
        for h in t.horizons:
            key = target_key(t.id, h)
            probs[key] = clamp_prob(entry.get(f"prob_up_{h}"), f"{who} for {key}")
    return probs


def analyze_targets(masked, case, playbook, analyst, judge, *, perspectives=MACRO_PERSPECTIVES,
                    mode="holistic") -> TargetAnalysis:
    from researchos.playbook import render_for_prompt

    check_mode(mode)
    if case.domain != "macro":
        raise ValueError(f"case {case.case_id}: analyze_targets needs a macro case, not "
                         f"{case.domain!r}")
    if (not isinstance(perspectives, list) or not perspectives
            or not all(isinstance(p, str) for p in perspectives)
            or len(set(perspectives)) != len(perspectives)):
        raise ValueError(f"perspectives must be a non-empty list of distinct strings, got "
                         f"{perspectives!r}")
    keys = target_keys(case)
    isolated = mode == "isolated"
    prefix = f"Macro snapshot as of {case.event_date}:\n\n" + render_documents(masked.documents)
    block = _targets_block(case)
    playbook_text = render_for_prompt(playbook)
    schema = view_schema(case.targets)
    system = MACRO_ISOLATED_ANALYST_SYSTEM if isolated else MACRO_ANALYST_SYSTEM
    views = {}
    for name in perspectives:
        if isolated:
            head = f"Perspective: {name}\nYour factor: {_focus(name)}\n{MACRO_ISOLATED_INSTRUCTION}"
        else:
            head = f"Perspective: {name}\nLook at {_focus(name)}\n{MACRO_INSTRUCTION}"
        output = analyst.json(system, f"{head}\n\n{block}\n\n{playbook_text}", schema,
                              prefix=prefix)
        probs = _read_forecasts(output, case, f"analyst ({name})")
        points = output.get("key_points")
        views[name] = TargetView(
            probs=probs,
            key_points=[p for p in points if isinstance(p, str)] if isinstance(points, list) else [])

    combined = {}
    for key in keys:
        probs = {name: view.probs[key] for name, view in views.items()}
        if isolated:
            pooled = pool_log_odds(probs, playbook.weights)
            if pooled is None:
                pooled = pool_log_odds(probs)
            combined[key] = min(max(float(pooled), PROB_MIN), PROB_MAX)
        else:
            value = combine_probabilities(probs, playbook.weights)
            combined[key] = value if value is not None else sum(probs.values()) / len(probs)

    parts = ["The strategists' views:"]
    for name, view in views.items():
        lines = [f"### {name}",
                 "P(up): " + ", ".join(f"{key} {view.probs[key]:.2f}" for key in keys)]
        lines += [f"- {p}" for p in view.key_points]
        parts.append("\n".join(lines))
    title = ("Evidence-pooled probabilities of the views (their log-odds added):" if isolated
             else "Playbook-weighted probabilities of the views:")
    parts.append("\n".join([title] + [f"- {key}: {combined[key]:.3f}" for key in keys]))
    parts += [block, playbook_text,
              "Give, for each target and horizon, your own probability that the target's level "
              "is higher at the end of the horizon than the level the snapshot starts from, and "
              "your thesis."]
    verdict = judge.json(MACRO_ISOLATED_JUDGE_SYSTEM if isolated else MACRO_JUDGE_SYSTEM,
                         "\n\n".join(parts), judge_schema(case.targets), prefix=prefix)
    prob_judge = _read_forecasts(verdict, case, "judge")
    thesis = verdict.get("thesis")
    return TargetAnalysis(case_id=case.case_id, mode=mode, perspectives=list(perspectives),
                          keys=keys, views=views, prob_weighted=combined, prob_judge=prob_judge,
                          thesis=thesis if isinstance(thesis, str) else "")
