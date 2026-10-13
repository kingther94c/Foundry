"""The analyst's perspectives, their combination, and the judge."""

from __future__ import annotations

import math
from dataclasses import dataclass

from tools.stats import combine_probabilities

from researchos.cases import render_documents
from researchos.llm import LLMError

PERSPECTIVES = ["growth", "profitability", "guidance", "cash_flow",
                "management_tone", "expectations_gap", "risks"]

STANCES = ["bullish", "bearish", "neutral"]
CONVICTIONS = ["low", "medium", "high"]
PROB_MIN, PROB_MAX = 0.01, 0.99

PERSPECTIVE_FOCUS = {
    "growth": "revenue and volume growth: its rate, acceleration or deceleration, by "
              "segment and region, and whether it is organic.",
    "profitability": "gross, operating and net margins, operating leverage, cost "
                     "discipline and earnings quality (one-offs, adjustments).",
    "guidance": "forward guidance: raised, maintained or cut, its range versus the "
                "prior outlook and versus expectations, and how credible it is.",
    "cash_flow": "operating and free cash flow, working capital, capital spending, "
                 "balance sheet and capital returns.",
    "management_tone": "management's tone on the call: confidence or hedging, how "
                       "analysts' questions were answered, what was avoided.",
    "expectations_gap": "the results and outlook against what the market expected "
                        "(consensus, whisper numbers, positioning): beat or miss and "
                        "by how much on the metrics that matter.",
    "risks": "risks and red flags: demand weakness, competition, regulation, "
             "accounting, concentration, macro exposure, anything that could "
             "disappoint.",
}

ANALYST_SYSTEM = """\
You are an equity analyst judging how a stock will react to a quarterly \
earnings report. The company's identity is hidden: names are replaced, money \
amounts are multiplied by a secret factor (absolute sizes are meaningless; \
ratios, growth rates and margins are intact) and years are shifted. Do not \
try to identify the company; reason only from the documents. Analyze through \
the single perspective you are given and estimate the probability that the \
stock beats the market (positive abnormal return, stock minus market) on the \
first trading session that can react to the report."""

JUDGE_SYSTEM = """\
You are the chief analyst. Several analysts have each judged a quarterly \
earnings report from one perspective. The company's identity is hidden \
(names replaced, money scaled, years shifted). Weigh their views against the \
documents and the playbook, and give your own probability that the stock \
beats the market (positive abnormal return) on the first trading session that \
can react, with a short thesis."""

VIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "stance": {"type": "string", "enum": list(STANCES)},
        "prob_up": {"type": "number"},
        "conviction": {"type": "string", "enum": list(CONVICTIONS)},
        "key_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["stance", "prob_up", "conviction", "key_points"],
    "additionalProperties": False,
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"prob_up": {"type": "number"}, "thesis": {"type": "string"}},
    "required": ["prob_up", "thesis"],
    "additionalProperties": False,
}


@dataclass
class View:
    stance: str  # "bullish" | "bearish" | "neutral"
    prob_up: float  # P(abnormal_return_1d > 0), clamped to [0.01, 0.99]
    conviction: str  # "low" | "medium" | "high"
    key_points: list[str]


@dataclass
class Analysis:
    case_id: str
    views: dict[str, View]  # one per perspective, in PERSPECTIVES order
    prob_weighted: float
    prob_judge: float
    thesis: str


def clamp_prob(value, who: str = "model") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
        raise LLMError(f"{who} returned an invalid prob_up: {value!r}")
    return min(max(float(value), PROB_MIN), PROB_MAX)


def _choice(value, allowed: list[str], default: str) -> str:
    if isinstance(value, str) and value.strip().lower() in allowed:
        return value.strip().lower()
    return default


def view_from_output(output: dict, perspective: str) -> View:
    if not isinstance(output, dict):
        raise LLMError(f"analyst ({perspective}) returned a non-object")
    points = output.get("key_points")
    return View(
        stance=_choice(output.get("stance"), STANCES, "neutral"),
        prob_up=clamp_prob(output.get("prob_up"), f"analyst ({perspective})"),
        conviction=_choice(output.get("conviction"), CONVICTIONS, "low"),
        key_points=[p for p in points if isinstance(p, str)] if isinstance(points, list) else [],
    )


def render_view(name: str, view: View) -> str:
    lines = [f"### {name}: {view.stance}, P(up)={view.prob_up:.2f}, conviction {view.conviction}"]
    lines.extend(f"- {p}" for p in view.key_points)
    return "\n".join(lines)


def _perspective_prompt(name: str, playbook_text: str) -> str:
    focus = PERSPECTIVE_FOCUS.get(name, name.replace("_", " "))
    return (
        f"Perspective: {name}\n"
        f"Look at {focus}\n"
        "Judge the documents above only from this perspective. Give your stance, "
        "the probability that the stock beats the market on the first session "
        "that can react, your conviction, and the key points behind it.\n\n"
        f"{playbook_text}"
    )


def analyze(masked, playbook, analyst, judge) -> Analysis:
    """One analyst call per perspective (shared document prefix), then one judge call."""
    from researchos.playbook import render_for_prompt

    prefix = "Earnings documents (identity masked):\n\n" + render_documents(masked.documents)
    playbook_text = render_for_prompt(playbook)
    views: dict[str, View] = {}
    for name in PERSPECTIVES:
        output = analyst.json(ANALYST_SYSTEM, _perspective_prompt(name, playbook_text),
                              VIEW_SCHEMA, prefix=prefix)
        views[name] = view_from_output(output, name)

    probs = {name: view.prob_up for name, view in views.items()}
    prob_weighted = combine_probabilities(probs, playbook.weights)
    if prob_weighted is None:
        prob_weighted = sum(probs.values()) / len(probs)

    judge_prompt = (
        "The analysts' views:\n\n"
        + "\n\n".join(render_view(name, view) for name, view in views.items())
        + f"\n\nPlaybook-weighted probability of the views: {prob_weighted:.3f}\n\n"
        + playbook_text
        + "\n\nGive your own probability that the stock beats the market on the "
        "first session that can react, and your thesis."
    )
    verdict = judge.json(JUDGE_SYSTEM, judge_prompt, JUDGE_SCHEMA, prefix=prefix)
    if not isinstance(verdict, dict):
        raise LLMError("judge returned a non-object")
    thesis = verdict.get("thesis")
    return Analysis(
        case_id=masked.case_id,
        views=views,
        prob_weighted=prob_weighted,
        prob_judge=clamp_prob(verdict.get("prob_up"), "judge"),
        thesis=thesis if isinstance(thesis, str) else "",
    )
