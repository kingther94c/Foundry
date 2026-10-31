"""Acceptance tests for many targets, one analysis (v26), written from SPEC.md independently of the
code (held back from the agent that built it).

Each case traces to the "Many targets, one analysis (v26)" section of SPEC.md. The prompts below
are copied from that section.
"""

from __future__ import annotations

import builtins
import io
import math
import os
from dataclasses import asdict
from pathlib import Path

import pytest
from tools.stats import combine_probabilities, pool_log_odds

from researchos import cases, masking, perspectives, targets
from researchos.cases import Document, Target
from researchos.llm import LLMError
from researchos.playbook import Playbook, render_for_prompt

MACRO = ["growth", "inflation", "labor", "monetary_policy", "fiscal_supply",
         "liquidity_conditions", "positioning_sentiment", "global_fx", "commodities_energy",
         "politics_geopolitics"]

FOCUS = {
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

ANALYST = "You are a macro strategist at a hedge fund. You see a snapshot of the markets and the economy as of the close of a given date: the targets you forecast with their levels and recent changes, economic data, positioning, the calendar of scheduled events and official texts. Assume nothing about what happened after that date; reason only from the documents. Analyze through the single perspective you are given and, for each target and horizon, estimate the probability that the target's level is higher at the end of the horizon than the level the snapshot starts from. What the market already prices is the baseline: 0.50 means no edge over the market."
JUDGE = "You are the portfolio manager. Several strategists have each judged a snapshot of the markets and the economy, as of the close of a given date, from one perspective. Assume nothing about what happened after that date. Weigh their views against the documents and the playbook and give, for each target and horizon, your own probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, with a short thesis. 0.50 means no edge over the market."
ISO_ANALYST = "You are a macro strategist at a hedge fund. You see a snapshot of the markets and the economy as of the close of a given date: the targets you forecast with their levels and recent changes, economic data, positioning, the calendar of scheduled events and official texts. Assume nothing about what happened after that date; reason only from the documents. You judge one factor in isolation. Assume that everything outside your factor stays as the market already prices it, and estimate, for each target and horizon, the probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, from your factor alone. 0.50 means your factor gives no signal either way; move away from 0.50 only as far as this factor by itself would move the target. Do not give an overall view: another strategist combines the factors."
ISO_JUDGE = "You are the portfolio manager. Several strategists have each judged one factor of a snapshot of the markets and the economy in isolation, as of the close of a given date, each as if everything else stayed as the market already prices it. Assume nothing about what happened after that date. Their probabilities are evidence from different factors: they add up rather than average, and a view at 0.50 is a factor that gives no signal. Weigh them against the documents and the playbook and give, for each target and horizon, your own probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, with a short thesis."
INSTRUCTION = "Judge the snapshot above only from this perspective. For each target and horizon give the probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, and the key points behind your view."
ISO_INSTRUCTION = "Judge only this factor, as if everything else stayed as the market already prices it. For each target and horizon give the probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, from this factor alone (0.50 if it gives no signal), and the key points behind your view."
CLOSING = "Give, for each target and horizon, your own probability that the target's level is higher at the end of the horizon than the level the snapshot starts from, and your thesis."

CASE_ID = "macro-2026-10-30"
KEYS = ["DGS10_1w", "DGS10_1m", "DGS10_3m", "SP500_1w", "SP500_1m"]
BLOCK = ("Targets:\n"
         "- DGS10: 10-year Treasury yield; change in basis points; from 4.98 on 2026-10-29; "
         "horizons 1w, 1m, 3m\n"
         "- SP500: S&P 500; simple return; from 7743.98 on 2026-10-28; horizons 1w, 1m")


def make_case(target_list=None, case_id=CASE_ID):
    return cases.macro_case(case_id, "2026-10-30", target_list or [
        Target("DGS10", "10-year Treasury yield", "bp", ["1w", "1m", "3m"], "2026-10-29", 4.98,
               {"1w": -2.0}),
        Target("SP500", "S&P 500", "return", ["1w", "1m"], "2026-10-28", 7743.98),
    ], [Document("dashboard", "Markets as of 2026-10-30", "| Target | Level |"),
        Document("statement", "FOMC statement (2026-10-28)", "The Committee decided.",
                 "https://fed.example/1028")])


def prob(i, j):
    """The fake strategist i's probability for key j: spread out, exact in binary."""
    return 0.25 + 0.03125 * ((3 * i + 5 * j) % 16)


class Fake:
    """An analyst or judge that answers from the prompt's first line, and records calls."""

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer or self.default

    def default(self, system, prompt, schema, prefix):
        if "thesis" in schema["properties"]:
            return {"forecasts": {"DGS10": {"prob_up_1w": 0.4, "prob_up_1m": 0.45,
                                            "prob_up_3m": 0.5},
                                  "SP500": {"prob_up_1w": 0.6, "prob_up_1m": 0.65}},
                    "thesis": "Rates drift lower."}
        name = prompt.splitlines()[0].split(": ", 1)[1]
        i = MACRO.index(name) if name in MACRO else 3
        values = [prob(i, j) for j in range(5)]
        return {"forecasts": {"DGS10": {"prob_up_1w": values[0], "prob_up_1m": values[1],
                                        "prob_up_3m": values[2]},
                              "SP500": {"prob_up_1w": values[3], "prob_up_1m": values[4]}},
                "key_points": [f"{name} point", 7, f"{name} second"]}

    def json(self, system, prompt, schema, *, prefix=None):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema,
                           "prefix": prefix})
        return self.answer(system, prompt, schema, prefix)


def macro_playbook():
    return Playbook(weights={name: (k + 1) / 55 for k, name in enumerate(MACRO)})


def run(mode="holistic", playbook=None, analyst=None, judge=None, case=None, **kw):
    case = case or make_case()
    analyst = analyst or Fake()
    judge = judge or Fake()
    playbook = playbook or macro_playbook()
    result = targets.analyze_targets(masking.unmasked(case), case, playbook, analyst, judge,
                                     mode=mode, **kw)
    return result, analyst, judge, playbook, case


# --- constants ---------------------------------------------------------------------------------

def test_perspective_sets():
    assert targets.MACRO_PERSPECTIVES == MACRO
    assert targets.PERSPECTIVE_SETS == {"earnings": perspectives.PERSPECTIVES, "macro": MACRO}
    assert list(targets.PERSPECTIVE_SETS) == ["earnings", "macro"]
    assert targets.MACRO_PERSPECTIVE_FOCUS == FOCUS
    assert list(targets.MACRO_PERSPECTIVE_FOCUS) == MACRO


@pytest.mark.parametrize("name, text", [
    ("MACRO_ANALYST_SYSTEM", ANALYST), ("MACRO_JUDGE_SYSTEM", JUDGE),
    ("MACRO_ISOLATED_ANALYST_SYSTEM", ISO_ANALYST), ("MACRO_ISOLATED_JUDGE_SYSTEM", ISO_JUDGE),
    ("MACRO_INSTRUCTION", INSTRUCTION), ("MACRO_ISOLATED_INSTRUCTION", ISO_INSTRUCTION),
])
def test_prompts_verbatim(name, text):
    assert getattr(targets, name) == text


def test_perspective_set():
    assert targets.perspective_set("earnings") == perspectives.PERSPECTIVES
    got = targets.perspective_set("macro")
    assert got == MACRO
    got.append("x")
    assert targets.MACRO_PERSPECTIVES == MACRO and targets.perspective_set("macro") == MACRO
    for bad in ("Macro", "", None, "rates"):
        with pytest.raises(ValueError):
            targets.perspective_set(bad)


# --- schemas -----------------------------------------------------------------------------------

def strict(props):
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}


FORECASTS = strict({
    "DGS10": strict({"prob_up_1w": {"type": "number"}, "prob_up_1m": {"type": "number"},
                     "prob_up_3m": {"type": "number"}}),
    "SP500": strict({"prob_up_1w": {"type": "number"}, "prob_up_1m": {"type": "number"}}),
})


def test_schemas():
    t = make_case().targets
    assert targets.forecasts_schema(t) == FORECASTS
    assert list(targets.forecasts_schema(t)["properties"]) == ["DGS10", "SP500"]
    assert list(targets.forecasts_schema(t)["properties"]["DGS10"]["properties"]) == \
        ["prob_up_1w", "prob_up_1m", "prob_up_3m"]
    view = targets.view_schema(t)
    assert view == strict({"forecasts": FORECASTS,
                           "key_points": {"type": "array", "items": {"type": "string"}}})
    assert list(view["properties"]) == ["forecasts", "key_points"]
    judge = targets.judge_schema(t)
    assert judge == strict({"forecasts": FORECASTS, "thesis": {"type": "string"}})
    assert list(judge["properties"]) == ["forecasts", "thesis"]


def every_object(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for value in schema.values():
            yield from every_object(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from every_object(value)


@pytest.mark.parametrize("n", [1, 3, 12])
def test_schemas_are_strict(n):
    t = [Target(f"T{k}", f"t{k}", "points", ["1w", "10d", "3m"][: 1 + k % 3], "2026-10-29", 1.0)
         for k in range(n)]
    for schema in (targets.forecasts_schema(t), targets.view_schema(t), targets.judge_schema(t)):
        for obj in every_object(schema):
            assert obj["additionalProperties"] is False
            assert obj["required"] == list(obj["properties"])
    assert list(targets.forecasts_schema(t)["properties"]) == [f"T{k}" for k in range(n)]


@pytest.mark.parametrize("bad", [
    [],
    [Target("A", "a", "bp", ["1w"], "2026-10-29", 1.0), Target("A", "b", "bp", ["1m"], "2026-10-29", 1.0)],
    [Target("A-1", "a", "bp", ["1w"], "2026-10-29", 1.0)],
    [Target("A", "a", "bp", ["1W"], "2026-10-29", 1.0)],
    [Target("A", "a", "pct", ["1w"], "2026-10-29", 1.0)],
    ["A"],
])
def test_schema_refusals(bad):
    for fn in (targets.forecasts_schema, targets.view_schema, targets.judge_schema):
        with pytest.raises(ValueError):
            fn(bad)


# --- analyze_targets: holistic -----------------------------------------------------------------

def test_holistic_calls(tmp_path):
    result, analyst, judge, playbook, case = run()
    prefix = ("Macro snapshot as of 2026-10-30:\n\n"
              + cases.render_documents(masking.unmasked(case).documents))
    book = render_for_prompt(playbook)
    assert len(analyst.calls) == 10 and len(judge.calls) == 1
    for name, call in zip(MACRO, analyst.calls):
        assert call["system"] == ANALYST
        assert call["prompt"] == (f"Perspective: {name}\nLook at {FOCUS[name]}\n{INSTRUCTION}\n\n"
                                  f"{BLOCK}\n\n{book}")
        assert call["schema"] == strict({"forecasts": FORECASTS, "key_points": {
            "type": "array", "items": {"type": "string"}}})
        assert call["prefix"] == prefix
    assert judge.calls[0]["system"] == JUDGE
    assert judge.calls[0]["schema"] == strict({"forecasts": FORECASTS, "thesis": {"type": "string"}})
    assert judge.calls[0]["prefix"] == prefix


def expected_views():
    return {name: {key: prob(i, j) for j, key in enumerate(KEYS)} for i, name in enumerate(MACRO)}


def test_holistic_result():
    result, _, _, playbook, _ = run()
    views = expected_views()
    assert isinstance(result, targets.TargetAnalysis)
    assert (result.case_id, result.mode, result.perspectives, result.keys) == (
        CASE_ID, "holistic", MACRO, KEYS)
    assert list(result.views) == MACRO
    for name in MACRO:
        assert isinstance(result.views[name], targets.TargetView)
        assert result.views[name].probs == views[name]
        assert list(result.views[name].probs) == KEYS
        assert result.views[name].key_points == [f"{name} point", f"{name} second"]
    for key in KEYS:
        want = combine_probabilities({n: views[n][key] for n in MACRO}, playbook.weights)
        assert result.prob_weighted[key] == pytest.approx(want, abs=1e-15)
    assert list(result.prob_weighted) == KEYS
    assert result.prob_judge == {"DGS10_1w": 0.4, "DGS10_1m": 0.45, "DGS10_3m": 0.5,
                                 "SP500_1w": 0.6, "SP500_1m": 0.65}
    assert result.thesis == "Rates drift lower."
    assert (result.model, result.knowledge_cutoff) == ("", "")


def test_holistic_judge_prompt():
    result, _, judge, playbook, _ = run()
    views = expected_views()
    parts = ["The strategists' views:"]
    for name in MACRO:
        parts.append("\n".join([f"### {name}", "P(up): " + ", ".join(
            f"{k} {views[name][k]:.2f}" for k in KEYS), f"- {name} point", f"- {name} second"]))
    parts.append("\n".join(["Playbook-weighted probabilities of the views:"]
                           + [f"- {k}: {result.prob_weighted[k]:.3f}" for k in KEYS]))
    parts += [BLOCK, render_for_prompt(playbook), CLOSING]
    assert judge.calls[0]["prompt"] == "\n\n".join(parts)


def test_weights_for_none_of_the_perspectives_give_the_plain_mean():
    # the earnings weights, less growth (the one name both sets have), and zero weights
    earnings_only = {n: w for n, w in Playbook.initial().weights.items() if n != "growth"}
    for book in (Playbook(weights=earnings_only), Playbook(weights={n: 0.0 for n in MACRO})):
        result, *_ = run(playbook=book)
        views = expected_views()
        for key in KEYS:
            assert result.prob_weighted[key] == pytest.approx(
                sum(views[n][key] for n in MACRO) / 10, abs=1e-15)


# --- analyze_targets: isolated -----------------------------------------------------------------

def test_isolated_calls_and_result():
    result, analyst, judge, playbook, _ = run(mode="isolated")
    book = render_for_prompt(playbook)
    for name, call in zip(MACRO, analyst.calls):
        assert call["system"] == ISO_ANALYST
        assert call["prompt"] == (f"Perspective: {name}\nYour factor: {FOCUS[name]}\n"
                                  f"{ISO_INSTRUCTION}\n\n{BLOCK}\n\n{book}")
    assert judge.calls[0]["system"] == ISO_JUDGE
    views = expected_views()
    for key in KEYS:
        want = pool_log_odds({n: views[n][key] for n in MACRO}, playbook.weights)
        assert result.prob_weighted[key] == pytest.approx(min(max(want, 0.01), 0.99), abs=1e-15)
    assert result.mode == "isolated"
    prompt = judge.calls[0]["prompt"]
    title = "Evidence-pooled probabilities of the views (their log-odds added):"
    assert ("\n\n" + title + "\n- DGS10_1w: " + f"{result.prob_weighted['DGS10_1w']:.3f}") in prompt
    assert "Playbook-weighted" not in prompt


def test_isolated_pools_without_weights_and_clamps():
    def answer(system, prompt, schema, prefix):
        if "thesis" in schema["properties"]:
            return Fake().default(system, prompt, schema, prefix)
        return {"forecasts": {"DGS10": {"prob_up_1w": 0.9, "prob_up_1m": 0.5, "prob_up_3m": 0.1},
                              "SP500": {"prob_up_1w": 0.6, "prob_up_1m": 0.4}},
                "key_points": []}

    result, *_ = run(mode="isolated", playbook=Playbook(weights={"guidance": 1.0}),
                     analyst=Fake(answer))
    assert result.prob_weighted["DGS10_1w"] == 0.99
    assert result.prob_weighted["DGS10_3m"] == 0.01
    assert result.prob_weighted["DGS10_1m"] == pytest.approx(0.5, abs=1e-12)
    want = pool_log_odds({n: 0.6 for n in MACRO})
    assert result.prob_weighted["SP500_1w"] == pytest.approx(min(max(want, 0.01), 0.99))


# --- replies -------------------------------------------------------------------------------------

def test_probabilities_are_clamped():
    def answer(system, prompt, schema, prefix):
        return {"forecasts": {"DGS10": {"prob_up_1w": 0, "prob_up_1m": 1, "prob_up_3m": 1.5},
                              "SP500": {"prob_up_1w": -3, "prob_up_1m": 0.005}},
                "key_points": "not a list", "thesis": 5, "extra": 1}

    result, *_ = run(analyst=Fake(answer), judge=Fake(answer))
    assert result.views["growth"].probs == {"DGS10_1w": 0.01, "DGS10_1m": 0.99,
                                            "DGS10_3m": 0.99, "SP500_1w": 0.01,
                                            "SP500_1m": 0.01}
    assert result.views["growth"].key_points == []
    assert result.prob_judge["DGS10_3m"] == 0.99
    assert result.thesis == ""
    for value in result.views["growth"].probs.values():
        assert isinstance(value, float)


GOOD = {"DGS10": {"prob_up_1w": 0.5, "prob_up_1m": 0.5, "prob_up_3m": 0.5},
        "SP500": {"prob_up_1w": 0.5, "prob_up_1m": 0.5}}


def with_(change):
    data = {"forecasts": {k: dict(v) for k, v in GOOD.items()}, "key_points": []}
    change(data)
    return data


BAD_REPLIES = {
    "not an object": (lambda d: None, ["not", "a", "dict"], None),
    "no forecasts": (lambda d: d.pop("forecasts"), None, None),
    "forecasts a list": (lambda d: d.update(forecasts=[1]), None, None),
    "a target missing": (lambda d: d["forecasts"].pop("SP500"), None, "SP500"),
    "a target not an object": (lambda d: d["forecasts"].update(DGS10=0.5), None, "DGS10"),
    "a horizon missing": (lambda d: d["forecasts"]["DGS10"].pop("prob_up_3m"), None, "DGS10_3m"),
    "a string": (lambda d: d["forecasts"]["SP500"].update(prob_up_1m="0.6"), None, "SP500_1m"),
    "a bool": (lambda d: d["forecasts"]["SP500"].update(prob_up_1w=True), None, "SP500_1w"),
    "nan": (lambda d: d["forecasts"]["DGS10"].update(prob_up_1w=math.nan), None, "DGS10_1w"),
    "null": (lambda d: d["forecasts"]["DGS10"].update(prob_up_1m=None), None, "DGS10_1m"),
}


@pytest.mark.parametrize("problem", sorted(BAD_REPLIES))
def test_a_bad_view_raises_before_the_judge(problem):
    change, whole, named = BAD_REPLIES[problem]
    reply = whole if whole is not None else with_(change)

    def answer(system, prompt, schema, prefix):
        if prompt.startswith("Perspective: labor\n"):
            return reply
        return {"forecasts": GOOD, "key_points": []}

    analyst, judge = Fake(answer), Fake()
    with pytest.raises(LLMError) as exc:
        run(analyst=analyst, judge=judge)
    assert "labor" in str(exc.value)
    if named:
        assert named in str(exc.value)
    assert len(analyst.calls) == 3 and judge.calls == []


@pytest.mark.parametrize("problem", sorted(BAD_REPLIES))
def test_a_bad_verdict_raises(problem):
    change, whole, named = BAD_REPLIES[problem]
    reply = whole if whole is not None else with_(change)
    with pytest.raises(LLMError) as exc:
        run(judge=Fake(lambda *a: reply))
    assert "judge" in str(exc.value)
    if named:
        assert named in str(exc.value)


# --- refusals before any call ------------------------------------------------------------------

def earnings_case():
    return cases.Case("acme-q3", "Acme", "ACME", [], "Q3 FY2026", "2026-10-29",
                      [Document("release", "Q3", "text")])


@pytest.mark.parametrize("kw, case", [
    ({"mode": "both"}, None),
    ({"mode": None}, None),
    ({}, "earnings"),
    ({"perspectives": []}, None),
    ({"perspectives": ["growth", "growth"]}, None),
    ({"perspectives": "growth"}, None),
    ({"perspectives": ["growth", 3]}, None),
    ({"perspectives": ("growth",)}, None),
    ({}, "no targets"),
])
def test_refusals_before_any_call(kw, case):
    if case == "earnings":
        c = earnings_case()
    else:
        c = make_case()
        if case == "no targets":
            c.targets = []
    analyst, judge = Fake(), Fake()
    mode = kw.pop("mode", "holistic")
    with pytest.raises(ValueError) as exc:
        targets.analyze_targets(masking.unmasked(c), c, macro_playbook(), analyst, judge,
                                mode=mode, **kw)
    if case == "earnings":
        assert "acme-q3" in str(exc.value)
    assert analyst.calls == [] and judge.calls == []


# --- other perspectives, nothing leaked --------------------------------------------------------

def test_other_perspectives():
    result, analyst, judge, *_ = run(perspectives=["monetary_policy", "my_own_view"])
    assert [c["prompt"].splitlines()[1] for c in analyst.calls] == [
        f"Look at {FOCUS['monetary_policy']}", "Look at my own view"]
    assert result.perspectives == ["monetary_policy", "my_own_view"]
    assert list(result.views) == ["monetary_policy", "my_own_view"]
    assert judge.calls[0]["prompt"].count("### ") == 2


def test_nothing_about_the_case_id_or_files(monkeypatch):
    opened = []
    real_open = io.open

    def spy(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).suffix == ".json":
            opened.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", spy)
    monkeypatch.setattr(builtins, "open", spy)
    case = make_case(case_id="secret-case-7")
    result, analyst, judge, *_ = run(case=case)
    monkeypatch.undo()
    assert opened == []
    assert result.case_id == "secret-case-7"
    for call in analyst.calls + judge.calls:
        for text in (call["system"], call["prompt"], call["prefix"]):
            assert "secret-case-7" not in text
            assert "resolution" not in text.lower() and "outcome" not in text.lower()


def test_no_key_points_in_the_judge_prompt():
    def answer(system, prompt, schema, prefix):
        return {"forecasts": GOOD, "key_points": [], "thesis": "t"}

    run_result = run(analyst=Fake(answer), judge=Fake(answer))
    prompt = run_result[2].calls[0]["prompt"]
    assert ("### growth\nP(up): DGS10_1w 0.50, DGS10_1m 0.50, DGS10_3m 0.50, SP500_1w 0.50, "
            "SP500_1m 0.50\n\n### inflation\n") in prompt


# --- TargetAnalysis as JSON ------------------------------------------------------------------------

def test_round_trip():
    result, *_ = run()
    data = asdict(result)
    assert list(data) == ["case_id", "mode", "perspectives", "keys", "views", "prob_weighted",
                          "prob_judge", "thesis", "model", "knowledge_cutoff"]
    back = targets.target_analysis_from_dict(data)
    assert back == result
    data["views"]["growth"]["key_points"].append("changed")
    assert back.views["growth"].key_points == ["growth point", "growth second"]
    del data["model"], data["knowledge_cutoff"]
    assert targets.target_analysis_from_dict(data).model == ""
    for field in ("case_id", "mode", "perspectives", "keys", "views", "prob_weighted",
                  "prob_judge", "thesis"):
        partial = asdict(result)
        del partial[field]
        with pytest.raises(KeyError):
            targets.target_analysis_from_dict(partial)


def test_targets_module_rules():
    text = Path(targets.__file__).read_text(encoding="utf-8")
    text.encode("ascii")
    for word in ("anthropic", "fromisoformat", "isfinite"):
        assert word not in text, word
