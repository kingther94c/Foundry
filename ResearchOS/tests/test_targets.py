"""Many targets, one analysis (SPEC.md v26): researchos.targets."""

import builtins
import math
from dataclasses import asdict, fields, replace
from pathlib import Path

import pytest

from conftest import FakeLLM, make_case

from researchos.cases import Document, Target, check_targets, macro_case, target_keys
from researchos.llm import LLMError
from researchos.masking import unmasked
from researchos.perspectives import MODES, PERSPECTIVES
from researchos.playbook import Lesson, Playbook, render_for_prompt
from researchos.targets import (MACRO_ANALYST_SYSTEM, MACRO_INSTRUCTION,
                                MACRO_ISOLATED_ANALYST_SYSTEM, MACRO_ISOLATED_INSTRUCTION,
                                MACRO_ISOLATED_JUDGE_SYSTEM, MACRO_JUDGE_SYSTEM,
                                MACRO_PERSPECTIVE_FOCUS, MACRO_PERSPECTIVES, PERSPECTIVE_SETS,
                                TargetAnalysis, TargetView, analyze_targets, forecasts_schema,
                                judge_schema, perspective_set, target_analysis_from_dict,
                                view_schema)

SPEC_PERSPECTIVES = ["growth", "inflation", "labor", "monetary_policy", "fiscal_supply",
                     "liquidity_conditions", "positioning_sentiment", "global_fx",
                     "commodities_energy", "politics_geopolitics"]

# The SPEC's texts, each copied whole on one line.
SPEC_FOCUS = {
    "growth": "real activity: GDP and its nowcasts, industrial production, retail sales, housing, surveys of businesses and consumers, and whether growth is speeding up or slowing down.",
    "inflation": "prices: consumer, producer and import prices, their core and services parts, inflation expectations and breakevens, and wages as a source of inflation.",
    "labor": "the labor market: payrolls, unemployment, jobless claims, job openings, participation and wage growth, and whether it is tightening or loosening.",
    "monetary_policy": "the Federal Reserve and other central banks: their statements, projections and speeches, how they react to the data, and what the market prices for the path of policy rates against what they are likely to do.",
    "fiscal_supply": "fiscal policy and the supply of government debt: deficits, Treasury issuance and refunding, auctions, the Treasury's cash balance and the term premium.",
    "liquidity_conditions": "liquidity and financial conditions: the Fed's balance sheet, bank reserves, money-market rates and funding stress, credit spreads, bank lending and the dollar.",
    "positioning_sentiment": "positioning and sentiment: speculative futures positions and how they changed, how crowded a trade is, and volatility and the appetite for risk it implies.",
    "global_fx": "the rest of the world: growth and policy in the euro area, Japan, China and the UK, capital flows and exchange rates, and how they reach US markets.",
    "commodities_energy": "commodities: the supply, demand and inventories of oil and natural gas, OPEC, metals, and how they pass through to inflation and growth.",
    "politics_geopolitics": "politics and geopolitics: elections, legislation, trade policy and tariffs, sanctions, conflicts and other shocks that can move markets.",
}

SPEC_ANALYST = "You are a macro strategist at a hedge fund. You see a snapshot of the markets and the economy as of the close of a given date: the targets you forecast with their levels and recent changes, economic data, positioning, the calendar of scheduled events and official texts. Assume nothing about what happened after that date; reason only from the documents. Analyze through the single perspective you are given and, for each target and horizon, estimate the probability that the target's level is higher at the end of the horizon than the level the snapshot starts from. What the market already prices is the baseline: 0.50 means no edge over the market."

SPEC_JUDGE = "You are the portfolio manager. Several strategists have each judged a snapshot of the markets and the economy, as of the close of a given date, from one perspective. Assume nothing about what happened after that date. Weigh their views against the documents and the playbook and give, for each target and horizon, your own probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, with a short thesis. 0.50 means no edge over the market."

SPEC_ISOLATED_ANALYST = "You are a macro strategist at a hedge fund. You see a snapshot of the markets and the economy as of the close of a given date: the targets you forecast with their levels and recent changes, economic data, positioning, the calendar of scheduled events and official texts. Assume nothing about what happened after that date; reason only from the documents. You judge one factor in isolation. Assume that everything outside your factor stays as the market already prices it, and estimate, for each target and horizon, the probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, from your factor alone. 0.50 means your factor gives no signal either way; move away from 0.50 only as far as this factor by itself would move the target. Do not give an overall view: another strategist combines the factors."

SPEC_ISOLATED_JUDGE = "You are the portfolio manager. Several strategists have each judged one factor of a snapshot of the markets and the economy in isolation, as of the close of a given date, each as if everything else stayed as the market already prices it. Assume nothing about what happened after that date. Their probabilities are evidence from different factors: they add up rather than average, and a view at 0.50 is a factor that gives no signal. Weigh them against the documents and the playbook and give, for each target and horizon, your own probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, with a short thesis."

SPEC_INSTRUCTION = "Judge the snapshot above only from this perspective. For each target and horizon give the probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, and the key points behind your view."

SPEC_ISOLATED_INSTRUCTION = "Judge only this factor, as if everything else stayed as the market already prices it. For each target and horizon give the probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, from this factor alone (0.50 if it gives no signal), and the key points behind your view."

SPEC_JUDGE_REQUEST = "Give, for each target and horizon, your own probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, and your thesis."

CASE_ID = "snap-zq9"
THREE = ["growth", "inflation", "labor"]
KEYS = ["DGS10_1w", "DGS10_1m", "SP500_1m", "VIXCLS_1w"]


def dgs10(**changes):
    target = Target(id="DGS10", name="10-year Treasury yield", kind="bp", horizons=["1w", "1m"],
                    as_of_date="2026-10-30", as_of_value=4.12, trailing={"1w": -3.0})
    return replace(target, **changes)


def make_targets():
    return [dgs10(),
            Target(id="SP500", name="S&P 500", kind="return", horizons=["1m"],
                   as_of_date="2026-10-29", as_of_value=6012.5),
            Target(id="VIXCLS", name="VIX", kind="points", horizons=["1w"],
                   as_of_date="2026-10-30", as_of_value=17)]


def make_macro():
    documents = [Document("markets", "Markets at the close", "  Yields rose 5bp on the week.\n",
                          source="https://example.com/markets"),
                 Document("calendar", "Scheduled", "- 2026-11-06: payrolls")]
    return macro_case(CASE_ID, "2026-10-30", make_targets(), documents)


PREFIX = ("Macro snapshot as of 2026-10-30:\n\n"
          "## [markets] Markets at the close\n\nYields rose 5bp on the week.\n\n"
          "## [calendar] Scheduled\n\n- 2026-11-06: payrolls\n")

TARGETS_BLOCK = (
    "Targets:\n"
    "- DGS10: 10-year Treasury yield; change in basis points; from 4.12 on 2026-10-30; "
    "horizons 1w, 1m\n"
    "- SP500: S&P 500; simple return; from 6012.50 on 2026-10-29; horizons 1m\n"
    "- VIXCLS: VIX; change in points; from 17.00 on 2026-10-30; horizons 1w")

# Each perspective's probabilities in KEYS order; labor's 0.995 is clamped to 0.99.
PROBS = {"growth": [0.6, 0.7, 0.8, 0.3], "inflation": [0.8, 0.9, 0.4, 0.45],
         "labor": [0.4, 0.9, 0.6, 0.995]}
POINTS = {"growth": ["growth point", 7, "second growth point"], "inflation": ["inflation point"],
          "labor": "not a list"}

EXPECTED_VIEWS = {
    "growth": TargetView({"DGS10_1w": 0.6, "DGS10_1m": 0.7, "SP500_1m": 0.8, "VIXCLS_1w": 0.3},
                         ["growth point", "second growth point"]),
    "inflation": TargetView({"DGS10_1w": 0.8, "DGS10_1m": 0.9, "SP500_1m": 0.4,
                             "VIXCLS_1w": 0.45}, ["inflation point"]),
    "labor": TargetView({"DGS10_1w": 0.4, "DGS10_1m": 0.9, "SP500_1m": 0.6, "VIXCLS_1w": 0.99},
                        []),
}


def forecasts_reply(probs, key_points):
    """A reply giving probs (in KEYS order), with keys the schema does not ask for."""
    a, b, c, d = probs
    return {"forecasts": {"DGS10": {"prob_up_1w": a, "prob_up_1m": b, "prob_up_3m": 0.123},
                          "SP500": {"prob_up_1m": c},
                          "VIXCLS": {"prob_up_1w": d},
                          "EXTRA": {"prob_up_1w": 0.5}},
            "key_points": key_points,
            "stance": "ignored"}


def analyst_answer(system, prompt, schema, prefix):
    name = prompt.splitlines()[0].split(": ", 1)[1]
    return forecasts_reply(PROBS.get(name, [0.5] * 4), POINTS.get(name, []))


JUDGE_REPLY = {"forecasts": {"DGS10": {"prob_up_1w": 0.55, "prob_up_1m": 1.5},
                             "SP500": {"prob_up_1m": 0.62}, "VIXCLS": {"prob_up_1w": 0},
                             "OTHER": 3},
               "thesis": "Rates drift up.", "extra": True}
JUDGE_PROBS = {"DGS10_1w": 0.55, "DGS10_1m": 0.99, "SP500_1m": 0.62, "VIXCLS_1w": 0.01}


def make_playbook(weights=None):
    if weights is None:
        weights = {"growth": 2.0, "inflation": 1.0, "labor": 1.0, "risks": 5.0}
    playbook = Playbook(weights=weights)
    playbook.lessons.append(Lesson("Payrolls move the front end.", "a payrolls week", "labor",
                                   "old-case-77"))
    return playbook


def agents():
    return FakeLLM(analyst_answer), FakeLLM(responses=[JUDGE_REPLY])


def run(mode="holistic", perspectives=THREE, playbook=None, analyst=None, judge=None):
    case = make_macro()
    default_analyst, default_judge = agents()
    analyst = default_analyst if analyst is None else analyst
    judge = default_judge if judge is None else judge
    result = analyze_targets(unmasked(case), case,
                             make_playbook() if playbook is None else playbook,
                             analyst, judge, perspectives=perspectives, mode=mode)
    return result, analyst, judge


def combined_part(header, values):
    return "\n".join([header] + [f"- {key}: {value}" for key, value in zip(KEYS, values)])


def expected_judge_prompt(combined, playbook_text):
    return ("The strategists' views:\n\n"
            "### growth\n"
            "P(up): DGS10_1w 0.60, DGS10_1m 0.70, SP500_1m 0.80, VIXCLS_1w 0.30\n"
            "- growth point\n"
            "- second growth point\n\n"
            "### inflation\n"
            "P(up): DGS10_1w 0.80, DGS10_1m 0.90, SP500_1m 0.40, VIXCLS_1w 0.45\n"
            "- inflation point\n\n"
            "### labor\n"
            "P(up): DGS10_1w 0.40, DGS10_1m 0.90, SP500_1m 0.60, VIXCLS_1w 0.99\n\n"
            f"{combined}\n\n{TARGETS_BLOCK}\n\n{playbook_text}\n\n{SPEC_JUDGE_REQUEST}")


# --- constants ---------------------------------------------------------------------------

def test_perspective_lists():
    assert MACRO_PERSPECTIVES == SPEC_PERSPECTIVES
    assert PERSPECTIVES == ["growth", "profitability", "guidance", "cash_flow",
                            "management_tone", "expectations_gap", "risks"]
    assert PERSPECTIVE_SETS == {"earnings": PERSPECTIVES, "macro": MACRO_PERSPECTIVES}
    assert PERSPECTIVE_SETS["earnings"] is PERSPECTIVES
    assert PERSPECTIVE_SETS["macro"] is MACRO_PERSPECTIVES


def test_perspective_set_returns_a_new_list():
    for domain, expected in (("macro", SPEC_PERSPECTIVES), ("earnings", PERSPECTIVES)):
        got = perspective_set(domain)
        assert got == expected and got is not PERSPECTIVE_SETS[domain]
        got.append("extra")
        assert perspective_set(domain) == expected


@pytest.mark.parametrize("bad", ["Macro", "rates", "", None, 3, ["macro"]])
def test_perspective_set_refuses_any_other_domain(bad):
    with pytest.raises(ValueError) as info:
        perspective_set(bad)
    assert repr(bad) in str(info.value)


def test_focus_system_prompts_and_instructions_are_verbatim():
    assert MACRO_PERSPECTIVE_FOCUS == SPEC_FOCUS
    assert list(MACRO_PERSPECTIVE_FOCUS) == SPEC_PERSPECTIVES
    assert MACRO_ANALYST_SYSTEM == SPEC_ANALYST
    assert MACRO_JUDGE_SYSTEM == SPEC_JUDGE
    assert MACRO_ISOLATED_ANALYST_SYSTEM == SPEC_ISOLATED_ANALYST
    assert MACRO_ISOLATED_JUDGE_SYSTEM == SPEC_ISOLATED_JUDGE
    assert MACRO_INSTRUCTION == SPEC_INSTRUCTION
    assert MACRO_ISOLATED_INSTRUCTION == SPEC_ISOLATED_INSTRUCTION
    texts = [MACRO_ANALYST_SYSTEM, MACRO_JUDGE_SYSTEM, MACRO_ISOLATED_ANALYST_SYSTEM,
             MACRO_ISOLATED_JUDGE_SYSTEM, MACRO_INSTRUCTION, MACRO_ISOLATED_INSTRUCTION,
             *MACRO_PERSPECTIVE_FOCUS.values()]
    for text in texts:
        assert "\n" not in text and text.isascii()


# --- schemas -----------------------------------------------------------------------------

def object_schemas(schema):
    found = []
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            found.append(schema)
        for value in schema.values():
            found.extend(object_schemas(value))
    return found


def test_forecasts_schema_follows_the_targets_and_their_horizons():
    expected = {
        "type": "object",
        "properties": {
            "DGS10": {"type": "object",
                      "properties": {"prob_up_1w": {"type": "number"},
                                     "prob_up_1m": {"type": "number"}},
                      "required": ["prob_up_1w", "prob_up_1m"],
                      "additionalProperties": False},
            "SP500": {"type": "object", "properties": {"prob_up_1m": {"type": "number"}},
                      "required": ["prob_up_1m"], "additionalProperties": False},
            "VIXCLS": {"type": "object", "properties": {"prob_up_1w": {"type": "number"}},
                       "required": ["prob_up_1w"], "additionalProperties": False},
        },
        "required": ["DGS10", "SP500", "VIXCLS"],
        "additionalProperties": False,
    }
    schema = forecasts_schema(make_targets())
    assert schema == expected
    assert list(schema["properties"]) == ["DGS10", "SP500", "VIXCLS"]
    assert list(schema["properties"]["DGS10"]["properties"]) == ["prob_up_1w", "prob_up_1m"]

    reordered = forecasts_schema([dgs10(horizons=["3m", "1w"]), *make_targets()[1:]][::-1])
    assert reordered["required"] == ["VIXCLS", "SP500", "DGS10"]
    assert reordered["properties"]["DGS10"]["required"] == ["prob_up_3m", "prob_up_1w"]

    assert view_schema(make_targets()) == {
        "type": "object",
        "properties": {"forecasts": expected,
                       "key_points": {"type": "array", "items": {"type": "string"}}},
        "required": ["forecasts", "key_points"],
        "additionalProperties": False,
    }
    assert judge_schema(make_targets()) == {
        "type": "object",
        "properties": {"forecasts": expected, "thesis": {"type": "string"}},
        "required": ["forecasts", "thesis"],
        "additionalProperties": False,
    }
    for built in (schema, view_schema(make_targets()), judge_schema(make_targets())):
        for obj in object_schemas(built):
            assert obj["additionalProperties"] is False
            assert obj["required"] == list(obj["properties"])


BAD_TARGETS = [
    lambda: [],
    lambda: [dgs10(), dgs10()],
    lambda: [dgs10(id="DGS-10")],
    lambda: [dgs10(horizons=[" 1w"])],
    lambda: [dgs10(horizons=["1w", "1w"])],
    lambda: [dgs10(horizons=[])],
    lambda: [dgs10(kind="level")],
    lambda: [dgs10(as_of_value=float("nan"))],
    lambda: [dgs10(), "SP500"],
]


@pytest.mark.parametrize("build", BAD_TARGETS)
def test_schemas_refuse_what_check_targets_refuses(build):
    with pytest.raises(ValueError) as expected:
        check_targets(build())
    for make_schema in (forecasts_schema, view_schema, judge_schema):
        with pytest.raises(ValueError) as info:
            make_schema(build())
        assert str(info.value) == str(expected.value)


# --- results -----------------------------------------------------------------------------

def test_result_fields_and_defaults():
    assert [f.name for f in fields(TargetView)] == ["probs", "key_points"]
    assert [f.name for f in fields(TargetAnalysis)] == [
        "case_id", "mode", "perspectives", "keys", "views", "prob_weighted", "prob_judge",
        "thesis", "model", "knowledge_cutoff"]
    analysis = TargetAnalysis("c", "holistic", [], [], {}, {}, {}, "")
    assert (analysis.model, analysis.knowledge_cutoff) == ("", "")


def test_target_analysis_round_trips_through_a_dict():
    result, _, _ = run()
    data = asdict(result)
    back = target_analysis_from_dict(data)
    assert back == result
    assert all(type(view) is TargetView for view in back.views.values())

    # Lists and dicts are new objects.
    back.perspectives.append("x")
    back.keys.append("x")
    back.views["growth"].probs["DGS10_1w"] = 0.0
    back.views["growth"].key_points.append("x")
    del back.views["labor"]
    back.prob_weighted.clear()
    back.prob_judge.clear()
    assert data == asdict(result)

    data = {**asdict(result), "model": "claude-opus-5-5", "knowledge_cutoff": "2026-06-30"}
    back = target_analysis_from_dict(data)
    assert (back.model, back.knowledge_cutoff) == ("claude-opus-5-5", "2026-06-30")
    for name in ("model", "knowledge_cutoff"):
        data = asdict(result)
        del data[name]
        assert getattr(target_analysis_from_dict(data), name) == ""
    for name in ("case_id", "mode", "perspectives", "keys", "views", "prob_weighted",
                 "prob_judge", "thesis"):
        data = asdict(result)
        del data[name]
        with pytest.raises(KeyError):
            target_analysis_from_dict(data)


# --- analyze_targets ---------------------------------------------------------------------

def test_holistic_prompts_calls_and_result():
    playbook = make_playbook()
    playbook_text = render_for_prompt(playbook)
    result, analyst, judge = run(playbook=playbook)

    assert len(analyst.calls) == 3 and len(judge.calls) == 1
    for name, call in zip(THREE, analyst.calls):
        assert call["system"] == SPEC_ANALYST
        assert call["prompt"] == (f"Perspective: {name}\nLook at {SPEC_FOCUS[name]}\n"
                                  f"{SPEC_INSTRUCTION}\n\n{TARGETS_BLOCK}\n\n{playbook_text}")
        assert call["schema"] == view_schema(make_targets())
        assert call["prefix"] == PREFIX

    assert result.views == EXPECTED_VIEWS
    assert list(result.views) == THREE
    assert all(list(view.probs) == KEYS for view in result.views.values())
    # Weights 2, 1, 1 (risks has no view): (2*0.6 + 0.8 + 0.4) / 4 = 0.6,
    # (2*0.7 + 0.9 + 0.9) / 4 = 0.8, (2*0.8 + 0.4 + 0.6) / 4 = 0.65,
    # (2*0.3 + 0.45 + 0.99) / 4 = 0.51.
    assert list(result.prob_weighted) == KEYS
    assert result.prob_weighted == pytest.approx(
        {"DGS10_1w": 0.6, "DGS10_1m": 0.8, "SP500_1m": 0.65, "VIXCLS_1w": 0.51})
    # The judge's 1.5 and 0 are clamped; keys the schema does not ask for are ignored.
    assert result.prob_judge == JUDGE_PROBS and list(result.prob_judge) == KEYS
    assert result.thesis == "Rates drift up."
    assert (result.case_id, result.mode, result.keys) == (CASE_ID, "holistic", KEYS)
    assert result.perspectives == THREE and result.perspectives is not THREE
    assert (result.model, result.knowledge_cutoff) == ("", "")

    call = judge.calls[0]
    assert call["system"] == SPEC_JUDGE
    assert call["schema"] == judge_schema(make_targets())
    assert call["prefix"] == PREFIX
    combined = combined_part("Playbook-weighted probabilities of the views:",
                             ["0.600", "0.800", "0.650", "0.510"])
    assert call["prompt"] == expected_judge_prompt(combined, playbook_text)


def test_holistic_falls_back_to_the_plain_mean():
    playbook = make_playbook({"growth": 0.0, "risks": 1.0})  # no view has a positive weight
    result, _, judge = run(playbook=playbook)
    # (0.6 + 0.8 + 0.4) / 3, (0.7 + 0.9 + 0.9) / 3, (0.8 + 0.4 + 0.6) / 3, (0.3 + 0.45 + 0.99) / 3
    assert result.prob_weighted == pytest.approx(
        {"DGS10_1w": 0.6, "DGS10_1m": 2.5 / 3, "SP500_1m": 0.6, "VIXCLS_1w": 0.58})
    combined = combined_part("Playbook-weighted probabilities of the views:",
                             ["0.600", "0.833", "0.600", "0.580"])
    assert judge.calls[0]["prompt"] == expected_judge_prompt(combined,
                                                             render_for_prompt(playbook))


def test_isolated_prompts_and_evidence_pooled_probabilities():
    playbook = make_playbook({"growth": 0.0, "risks": 1.0})  # no view has a positive weight
    playbook_text = render_for_prompt(playbook)
    result, analyst, judge = run(mode="isolated", playbook=playbook)

    assert len(analyst.calls) == 3 and len(judge.calls) == 1
    for name, call in zip(THREE, analyst.calls):
        assert call["system"] == SPEC_ISOLATED_ANALYST
        assert call["prompt"] == (f"Perspective: {name}\nYour factor: {SPEC_FOCUS[name]}\n"
                                  f"{SPEC_ISOLATED_INSTRUCTION}\n\n{TARGETS_BLOCK}\n\n"
                                  f"{playbook_text}")
        assert call["schema"] == view_schema(make_targets())
        assert call["prefix"] == PREFIX

    # Unweighted pooling multiplies the odds: 1.5 * 4 * 2/3 = 4 -> 0.8;
    # 7/3 * 9 * 9 = 189 -> 189/190, clamped to 0.99; 4 * 2/3 * 1.5 = 4 -> 0.8;
    # 3/7 * 9/11 * 99 = 2673/77 -> 2673/2750 = 0.972.
    assert list(result.prob_weighted) == KEYS
    assert result.prob_weighted == pytest.approx(
        {"DGS10_1w": 0.8, "DGS10_1m": 0.99, "SP500_1m": 0.8, "VIXCLS_1w": 0.972})
    assert result.prob_weighted["DGS10_1m"] == 0.99
    assert result.views == EXPECTED_VIEWS
    assert (result.mode, result.prob_judge, result.thesis) == ("isolated", JUDGE_PROBS,
                                                               "Rates drift up.")

    call = judge.calls[0]
    assert call["system"] == SPEC_ISOLATED_JUDGE
    assert call["schema"] == judge_schema(make_targets())
    assert call["prefix"] == PREFIX
    combined = combined_part("Evidence-pooled probabilities of the views (their log-odds added):",
                             ["0.800", "0.990", "0.800", "0.972"])
    assert call["prompt"] == expected_judge_prompt(combined, playbook_text)
    assert "Playbook-weighted" not in call["prompt"]


def test_isolated_pools_with_the_playbook_weights():
    result, _, _ = run(mode="isolated", playbook=make_playbook())

    def logit(p):
        return math.log(p / (1 - p))

    # The weights used (2, 1, 1; risks has no view), scaled to average 1: 1.5, 0.75, 0.75.
    for i, key in enumerate(KEYS):
        z = (1.5 * logit(PROBS["growth"][i]) + 0.75 * logit(PROBS["inflation"][i])
             + 0.75 * logit(min(PROBS["labor"][i], 0.99)))
        expected = min(max(1 / (1 + math.exp(-z)), 0.01), 0.99)
        assert result.prob_weighted[key] == pytest.approx(expected)
    # By hand: odds 1.5 ** 1.5 * 4 ** 0.75 * (2/3) ** 0.75 = 3.834, so 0.7931.
    assert result.prob_weighted["DGS10_1w"] == pytest.approx(0.7931, abs=1e-4)


def test_default_perspectives_and_mode():
    case = make_macro()
    analyst, judge = agents()
    result = analyze_targets(unmasked(case), case, Playbook.initial(), analyst, judge)
    assert result.mode == "holistic"
    assert result.perspectives == SPEC_PERSPECTIVES
    assert result.perspectives is not MACRO_PERSPECTIVES
    assert [c["prompt"].splitlines()[:3] for c in analyst.calls] == [
        [f"Perspective: {name}", f"Look at {SPEC_FOCUS[name]}", SPEC_INSTRUCTION]
        for name in SPEC_PERSPECTIVES]
    assert {c["system"] for c in analyst.calls} == {SPEC_ANALYST}
    assert judge.calls[0]["system"] == SPEC_JUDGE
    # The earnings playbook's weights share only "growth" with the macro perspectives.
    assert result.prob_weighted == pytest.approx(EXPECTED_VIEWS["growth"].probs)


def test_a_perspective_without_a_focus_looks_at_its_name():
    result, analyst, judge = run(perspectives=["term_premium", "profitability"])
    assert [c["prompt"].splitlines()[1] for c in analyst.calls] == [
        "Look at term premium", "Look at profitability"]
    assert list(result.views) == ["term_premium", "profitability"]
    assert "### term_premium\nP(up): DGS10_1w 0.50, DGS10_1m 0.50, SP500_1m 0.50, " \
           "VIXCLS_1w 0.50" in judge.calls[0]["prompt"]
    _, analyst, _ = run(mode="isolated", perspectives=["term_premium"])
    assert analyst.calls[0]["prompt"].splitlines()[1] == "Your factor: term premium"


def test_the_analysts_read_the_masked_documents():
    case = make_macro()
    masked = unmasked(case)
    masked.documents[0].text = "Masked text."
    analyst, judge = agents()
    analyze_targets(masked, case, make_playbook(), analyst, judge, perspectives=THREE)
    expected = ("Macro snapshot as of 2026-10-30:\n\n"
                "## [markets] Markets at the close\n\nMasked text.\n\n"
                "## [calendar] Scheduled\n\n- 2026-11-06: payrolls\n")
    assert [c["prefix"] for c in analyst.calls + judge.calls] == [expected] * 4


@pytest.mark.parametrize("kwargs, needle", [
    ({"mode": "Isolated"}, "'Isolated'"),
    ({"mode": None}, "None"),
    ({"perspectives": []}, "perspectives"),
    ({"perspectives": "growth"}, "perspectives"),
    ({"perspectives": ("growth", "labor")}, "perspectives"),
    ({"perspectives": ["growth", "growth"]}, "perspectives"),
    ({"perspectives": ["growth", 3]}, "perspectives"),
    ({"perspectives": None}, "perspectives"),
])
def test_bad_arguments_raise_before_any_call(kwargs, needle):
    case = make_macro()
    analyst, judge = agents()
    with pytest.raises(ValueError) as info:
        analyze_targets(unmasked(case), case, make_playbook(), analyst, judge, **kwargs)
    assert needle in str(info.value)
    assert analyst.calls == [] and judge.calls == []


def test_only_a_macro_case_is_analyzed():
    earnings = make_case("acme-q4")
    analyst, judge = agents()
    with pytest.raises(ValueError, match="acme-q4"):
        analyze_targets(unmasked(earnings), earnings, make_playbook(), analyst, judge)
    case = make_macro()
    case.domain = "earnings"
    with pytest.raises(ValueError, match=CASE_ID):
        analyze_targets(unmasked(case), case, make_playbook(), analyst, judge)
    # The mode is checked first, then the domain, then the perspectives.
    with pytest.raises(ValueError, match="'pooled'"):
        analyze_targets(unmasked(earnings), earnings, make_playbook(), analyst, judge,
                        mode="pooled")
    with pytest.raises(ValueError, match="acme-q4"):
        analyze_targets(unmasked(earnings), earnings, make_playbook(), analyst, judge,
                        perspectives=[])
    assert analyst.calls == [] and judge.calls == []


@pytest.mark.parametrize("change", [
    lambda case: setattr(case.targets[0], "kind", "level"),
    lambda case: setattr(case.targets[1], "horizons", ["1m", "1m"]),
    lambda case: setattr(case.targets[2], "as_of_value", "17"),
    lambda case: case.targets.append(dgs10()),
    lambda case: case.targets.append("SP500"),
])
def test_invalid_targets_raise_the_error_of_target_keys(change):
    case = make_macro()
    change(case)
    with pytest.raises(ValueError) as expected:
        target_keys(case)
    analyst, judge = agents()
    with pytest.raises(ValueError) as info:
        analyze_targets(unmasked(case), case, make_playbook(), analyst, judge, perspectives=THREE)
    assert str(info.value) == str(expected.value)
    with pytest.raises(ValueError, match="perspectives"):
        analyze_targets(unmasked(case), case, make_playbook(), analyst, judge, perspectives=[])
    assert analyst.calls == [] and judge.calls == []


def test_a_macro_case_without_targets_raises_before_any_call():
    case = make_macro()
    case.targets = []
    analyst, judge = agents()
    with pytest.raises(ValueError, match="empty"):
        analyze_targets(unmasked(case), case, make_playbook(), analyst, judge)
    assert analyst.calls == [] and judge.calls == []


def growth_reply():
    return forecasts_reply(PROBS["growth"], [])


def with_target(reply, target_id, value):
    return {**reply, "forecasts": {**reply["forecasts"], target_id: value}}


def without_target(reply, target_id):
    return {**reply, "forecasts": {k: v for k, v in reply["forecasts"].items() if k != target_id}}


BAD_REPLIES = [
    (lambda r: [r], []),
    (lambda r: None, []),
    (lambda r: "forecasts", []),
    (lambda r: {"key_points": []}, []),
    (lambda r: {**r, "forecasts": [0.6]}, []),
    (lambda r: without_target(r, "SP500"), ["SP500"]),
    (lambda r: with_target(r, "VIXCLS", 0.4), ["VIXCLS"]),
    (lambda r: with_target(r, "VIXCLS", None), ["VIXCLS"]),
    (lambda r: with_target(r, "DGS10", {"prob_up_1w": 0.6}), ["DGS10_1m"]),
    (lambda r: with_target(r, "SP500", {"prob_up_1m": True}), ["SP500_1m"]),
    (lambda r: with_target(r, "SP500", {"prob_up_1m": "0.6"}), ["SP500_1m"]),
    (lambda r: with_target(r, "SP500", {"prob_up_1m": float("nan")}), ["SP500_1m"]),
    (lambda r: with_target(r, "SP500", {"prob_up_1m": None}), ["SP500_1m"]),
]


@pytest.mark.parametrize("breaks, names", BAD_REPLIES)
def test_a_bad_analyst_reply_raises_llm_error(breaks, names):
    analyst = FakeLLM(lambda system, prompt, schema, prefix: breaks(growth_reply()))
    _, judge = agents()
    with pytest.raises(LLMError) as info:
        run(analyst=analyst, judge=judge)
    for name in ["growth", *names]:
        assert name in str(info.value)
    assert len(analyst.calls) == 1 and judge.calls == []


@pytest.mark.parametrize("breaks, names", BAD_REPLIES)
def test_a_bad_judge_reply_raises_llm_error(breaks, names):
    judge = FakeLLM(lambda system, prompt, schema, prefix: breaks(growth_reply()))
    with pytest.raises(LLMError) as info:
        run(judge=judge)
    for name in ["judge", *names]:
        assert name in str(info.value)
    assert len(judge.calls) == 1


def test_each_reply_is_read_before_the_next_call():
    def answer(system, prompt, schema, prefix):
        if prompt.startswith("Perspective: inflation\n"):
            return {"forecasts": {}, "key_points": []}
        return analyst_answer(system, prompt, schema, prefix)

    analyst = FakeLLM(answer)
    _, judge = agents()
    with pytest.raises(LLMError) as info:
        run(analyst=analyst, judge=judge)
    assert "inflation" in str(info.value) and "DGS10" in str(info.value)
    assert len(analyst.calls) == 2 and judge.calls == []


def test_the_thesis_is_the_judges_string():
    without = {k: v for k, v in JUDGE_REPLY.items() if k != "thesis"}
    for reply in ({**JUDGE_REPLY, "thesis": 42}, {**JUDGE_REPLY, "thesis": ["a"]}, without):
        result, _, _ = run(judge=FakeLLM(responses=[reply]))
        assert result.thesis == "" and result.prob_judge == JUDGE_PROBS


def test_no_file_is_touched_and_the_case_id_is_never_sent(monkeypatch):
    case = make_macro()
    masked = unmasked(case)

    def refuse(*args, **kwargs):
        raise AssertionError("a file was opened")

    monkeypatch.setattr(builtins, "open", refuse)
    for name in ("open", "read_text", "write_text", "read_bytes", "write_bytes", "mkdir"):
        monkeypatch.setattr(Path, name, refuse)
    for mode in MODES:
        analyst, judge = agents()
        analyze_targets(masked, case, make_playbook(), analyst, judge, perspectives=THREE,
                        mode=mode)
        sent = analyst.texts() + judge.texts()
        assert len(sent) == 12 and all(sent)
        assert not any(CASE_ID in text or "zq9" in text for text in sent)
