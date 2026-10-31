"""Acceptance tests for scoring and reflecting on many targets (v27), written from SPEC.md
independently of the code (held back from the agent that built it).

Each case traces to the "Scoring and reflecting on many targets (v27)" section of SPEC.md. The
probabilities are exact in binary, so the Brier scores below are worked out by hand.
"""

from __future__ import annotations

import builtins
import io
import json
import os
from pathlib import Path

import pytest

from researchos import cases, compare, reflect, targets
from researchos.cases import Document, Outcome, Target, TargetOutcome
from researchos.playbook import Lesson

REFLECTOR = "You are the reflector in a research pipeline that trains macro strategists to forecast where market levels go in the weeks and months after a snapshot of the markets and the economy. The forecasts were sealed before the outcomes were known; now you see both. Explain why each target moved the way it did, where the forecasts went wrong or right, and write lessons that would hold on other dates. A lesson must not make a rule of a particular date, level or event: it is shown to the strategists on every later snapshot. Each lesson says when it applies and which perspective it belongs to."
CLOSING = "Why did these targets move the way they did? Where were the forecasts wrong, and where right? Write lessons that would hold on other dates; never make a rule of a particular date, level or event."

CASE_ID = "macro-2026-10-30"
KEYS = ["DGS10_1w", "DGS10_1m", "SP500_1w"]
PERSP = ["growth", "inflation", "labor"]


def make_case():
    return cases.macro_case(CASE_ID, "2026-10-30", [
        Target("DGS10", "10-year Treasury yield", "bp", ["1w", "1m"], "2026-10-29", 4.0,
               {"1w": -2.0, "1m": 31.0}),
        Target("SP500", "S&P 500", "return", ["1w"], "2026-10-28", 6800.0),
    ], [Document("dashboard", "Markets as of 2026-10-30", "| Target | Level |"),
        Document("statement", "FOMC statement (2026-10-28)", "The Committee decided.",
                 "https://fed.example/1028")])


def make_analysis(**changes):
    views = {
        "growth": targets.TargetView({"DGS10_1w": 0.25, "DGS10_1m": 0.5, "SP500_1w": 0.75},
                                     ["growth point"]),
        "inflation": targets.TargetView({"DGS10_1w": 0.5, "DGS10_1m": 0.75, "SP500_1w": 0.625},
                                        []),
        "labor": targets.TargetView({"DGS10_1w": 0.75, "DGS10_1m": 0.25, "SP500_1w": 0.5},
                                    ["labor point"]),
    }
    data = dict(case_id=CASE_ID, mode="holistic", perspectives=list(PERSP), keys=list(KEYS),
                views=views, prob_weighted={"DGS10_1w": 0.5, "DGS10_1m": 0.5, "SP500_1w": 0.625},
                prob_judge={"DGS10_1w": 0.375, "DGS10_1m": 0.625, "SP500_1w": 0.875},
                thesis="Rates drift lower.", model="claude-opus-5-5",
                knowledge_cutoff="2026-06-30")
    data.update(changes)
    return targets.TargetAnalysis(**data)


DGS10_1W = TargetOutcome("DGS10", "1w", "bp", "2026-10-29", 4.0, "2026-11-06", 3.8125, -18.75,
                         False)
SP500_1W = TargetOutcome("SP500", "1w", "return", "2026-10-28", 6800.0, "2026-11-05", 6970.0,
                         0.025, True)


def make_outcome(**extra):
    return Outcome(targets={"SP500_1w": SP500_1W, "DGS10_1w": DGS10_1W, **extra})


# --- score_targets ---------------------------------------------------------------------------------

def test_records():
    records = targets.score_targets(make_analysis(), make_outcome(), make_case())
    assert records == [
        {"case_id": "macro-2026-10-30/DGS10_1w", "case": CASE_ID, "key": "DGS10_1w",
         "target": "DGS10", "horizon": "1w", "kind": "bp", "event_date": "2026-10-30",
         "resolution_date": "2026-11-06", "start_date": "2026-10-29", "start": 4.0,
         "end_date": "2026-11-06", "end": 3.8125, "change": -18.75, "trailing_change": -2.0,
         "actual_up": False, "prob_weighted": 0.5, "prob_judge": 0.375, "prob_uniform": 0.5,
         "prob_by_perspective": {"growth": 0.25, "inflation": 0.5, "labor": 0.75},
         "brier_weighted": 0.25, "brier_judge": 0.140625, "brier_uniform": 0.25,
         "brier_coin": 0.25,
         "brier_by_perspective": {"growth": 0.0625, "inflation": 0.25, "labor": 0.5625},
         "correct_weighted": True, "correct_judge": True},
        {"case_id": "macro-2026-10-30/SP500_1w", "case": CASE_ID, "key": "SP500_1w",
         "target": "SP500", "horizon": "1w", "kind": "return", "event_date": "2026-10-30",
         "resolution_date": "2026-11-06", "start_date": "2026-10-28", "start": 6800.0,
         "end_date": "2026-11-05", "end": 6970.0, "change": 0.025, "trailing_change": None,
         "actual_up": True, "prob_weighted": 0.625, "prob_judge": 0.875, "prob_uniform": 0.625,
         "prob_by_perspective": {"growth": 0.75, "inflation": 0.625, "labor": 0.5},
         "brier_weighted": 0.140625, "brier_judge": 0.015625, "brier_uniform": 0.140625,
         "brier_coin": 0.25,
         "brier_by_perspective": {"growth": 0.0625, "inflation": 0.140625, "labor": 0.25},
         "correct_weighted": True, "correct_judge": True},
    ]


def test_record_key_order():
    record = targets.score_targets(make_analysis(), make_outcome(), make_case())[0]
    assert list(record) == [
        "case_id", "case", "key", "target", "horizon", "kind", "event_date", "resolution_date",
        "start_date", "start", "end_date", "end", "change", "trailing_change", "actual_up",
        "prob_weighted", "prob_judge", "prob_uniform", "prob_by_perspective", "brier_weighted",
        "brier_judge", "brier_uniform", "brier_coin", "brier_by_perspective",
        "correct_weighted", "correct_judge"]
    assert list(record["prob_by_perspective"]) == PERSP
    assert list(record["brier_by_perspective"]) == PERSP


def test_a_month_later():
    later = TargetOutcome("DGS10", "1m", "bp", "2026-10-29", 4.0, "2026-11-27", 4.25, 25.0, True)
    records = targets.score_targets(make_analysis(), make_outcome(DGS10_1m=later), make_case())
    assert [r["key"] for r in records] == KEYS
    r = records[1]
    assert (r["resolution_date"], r["trailing_change"], r["actual_up"]) == ("2026-11-30", 31.0, True)
    assert r["prob_uniform"] == 0.5
    assert (r["brier_weighted"], r["brier_judge"]) == (0.25, 0.140625)
    # 0.5 is not above 0.5: a forecast of exactly 0.5 does not call it up
    assert (r["correct_weighted"], r["correct_judge"]) == (False, True)


def test_correct_when_wrong():
    analysis = make_analysis(prob_weighted={"DGS10_1w": 0.75, "DGS10_1m": 0.5, "SP500_1w": 0.25},
                             prob_judge={"DGS10_1w": 0.5, "DGS10_1m": 0.5, "SP500_1w": 0.5})
    a, b = targets.score_targets(analysis, make_outcome(), make_case())
    assert (a["correct_weighted"], a["correct_judge"]) == (False, True)
    assert (b["correct_weighted"], b["correct_judge"]) == (False, False)
    assert a["brier_weighted"] == 0.5625 and b["brier_judge"] == 0.25


def test_nothing_resolved_gives_no_record():
    assert targets.score_targets(make_analysis(), Outcome(), make_case()) == []


@pytest.mark.parametrize("change", [
    {"case_id": "macro-2026-11-06"},
    {"keys": ["DGS10_1m", "DGS10_1w", "SP500_1w"]},
    {"keys": ["DGS10_1w", "DGS10_1m"]},
])
def test_an_analysis_of_another_case_is_refused(change):
    with pytest.raises(ValueError):
        targets.score_targets(make_analysis(**change), make_outcome(), make_case())


def test_records_are_json_and_hold_no_text():
    text = json.dumps(targets.score_targets(make_analysis(), make_outcome(), make_case()))
    for word in ("Rates drift", "point", "Committee", "Treasury yield"):
        assert word not in text


def test_compare_reads_the_records():
    analysis = make_analysis()
    a = {"cases": targets.score_targets(analysis, make_outcome(), make_case())}
    other = make_analysis(prob_judge={"DGS10_1w": 0.5, "DGS10_1m": 0.5, "SP500_1w": 0.5})
    b = {"cases": targets.score_targets(other, make_outcome(), make_case())}
    assert compare.paired(a, b, "brier_judge") == [
        ("macro-2026-10-30/DGS10_1w", 0.140625, 0.25),
        ("macro-2026-10-30/SP500_1w", 0.015625, 0.25)]


# --- reflect_targets ------------------------------------------------------------------------------

class Fake:
    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply if reply is not None else {
            "why_market_moved": "x", "where_we_were_wrong": "y",
            "lessons": [{"lesson": " Curves steepen after cuts. ", "applies_when": " cuts ",
                         "perspective": " Monetary_Policy "}]}

    def json(self, system, prompt, schema, *, prefix=None):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema,
                           "prefix": prefix})
        return self.reply


DGS10_PART = ("### DGS10_1w (10-year Treasury yield, 1w)\n"
              "Perspectives: growth 0.25, inflation 0.50, labor 0.75\n"
              "Combined: 0.500; judge: 0.375\n"
              "Outcome: from 4.00 on 2026-10-29 to 3.81 on 2026-11-06, -19 bp: not up")
SP500_PART = ("### SP500_1w (S&P 500, 1w)\n"
              "Perspectives: growth 0.75, inflation 0.62, labor 0.50\n"
              "Combined: 0.625; judge: 0.875\n"
              "Outcome: from 6800.00 on 2026-10-28 to 6970.00 on 2026-11-05, +2.5%: up")


def expected_prompt(parts, perspectives=PERSP):
    case = make_case()
    return "\n\n".join(
        ["Snapshot as of 2026-10-30: the sealed forecasts and what happened."] + parts
        + ["Judge's thesis: Rates drift lower.",
           "Documents\n\n" + cases.render_documents(case.documents), CLOSING,
           "Each lesson's perspective is one of: " + ", ".join(perspectives) + ', or "general".'])


def test_reflector_system_verbatim():
    assert targets.MACRO_REFLECTOR_SYSTEM == REFLECTOR


def test_the_reflector_call():
    reflector = Fake()
    analysis = make_analysis(perspectives=["growth", "monetary_policy", "labor"],
                             views={"growth": make_analysis().views["growth"],
                                    "monetary_policy": make_analysis().views["inflation"],
                                    "labor": make_analysis().views["labor"]})
    lessons = targets.reflect_targets(make_case(), analysis, make_outcome(), reflector)
    assert len(reflector.calls) == 1
    call = reflector.calls[0]
    assert call["system"] == REFLECTOR
    assert call["schema"] == reflect.REFLECT_SCHEMA
    assert call["prefix"] is None
    assert call["prompt"] == expected_prompt(
        [DGS10_PART.replace("inflation 0.50", "monetary_policy 0.50"),
         SP500_PART.replace("inflation 0.62", "monetary_policy 0.62")],
        ["growth", "monetary_policy", "labor"])
    assert lessons == [Lesson("Curves steepen after cuts.", "cuts", "monetary_policy", CASE_ID)]


def test_the_prompt_for_every_resolved_key():
    reflector = Fake()
    targets.reflect_targets(make_case(), make_analysis(), make_outcome(), reflector)
    assert reflector.calls[0]["prompt"] == expected_prompt([DGS10_PART, SP500_PART])


@pytest.mark.parametrize("keys, parts", [
    (["SP500_1w"], [SP500_PART]),
    (["SP500_1w", "DGS10_1w", "SP500_1w"], [DGS10_PART, SP500_PART]),
    (("DGS10_1w",), [DGS10_PART]),
])
def test_chosen_keys(keys, parts):
    reflector = Fake()
    targets.reflect_targets(make_case(), make_analysis(), make_outcome(), reflector, keys=keys)
    assert reflector.calls[0]["prompt"] == expected_prompt(parts)


@pytest.mark.parametrize("keys, named", [
    (["DGS10_1m"], "DGS10_1m"),
    (["SP500_1w", "GOLD_1w"], "GOLD_1w"),
])
def test_chosen_keys_must_have_resolved(keys, named):
    reflector = Fake()
    with pytest.raises(ValueError) as exc:
        targets.reflect_targets(make_case(), make_analysis(), make_outcome(), reflector, keys=keys)
    assert named in str(exc.value)
    assert reflector.calls == []


def test_nothing_to_reflect_on_makes_no_call():
    reflector = Fake()
    assert targets.reflect_targets(make_case(), make_analysis(), Outcome(), reflector) == []
    assert targets.reflect_targets(make_case(), make_analysis(), make_outcome(), reflector,
                                   keys=[]) == []
    assert reflector.calls == []


def test_another_case_is_refused_before_the_call():
    reflector = Fake()
    with pytest.raises(ValueError):
        targets.reflect_targets(make_case(), make_analysis(case_id="other"), make_outcome(),
                                reflector)
    assert reflector.calls == []


def test_reading_the_lessons():
    reply = {"lessons": [
        {"lesson": "A", "applies_when": "w", "perspective": "inflation"},
        {"lesson": "B", "applies_when": 3, "perspective": "general"},
        {"lesson": "C", "applies_when": "w", "perspective": "risks"},
        {"lesson": "D", "applies_when": "w", "perspective": None},
        {"lesson": "   ", "applies_when": "w", "perspective": "labor"},
        {"lesson": 5, "applies_when": "w", "perspective": "labor"},
        "a string",
        {"lesson": "E", "perspective": " LABOR "},
    ]}
    lessons = targets.reflect_targets(make_case(), make_analysis(), make_outcome(), Fake(reply))
    assert lessons == [Lesson("A", "w", "inflation", CASE_ID), Lesson("B", "", "general", CASE_ID),
                       Lesson("C", "w", "general", CASE_ID), Lesson("D", "w", "general", CASE_ID),
                       Lesson("E", "", "labor", CASE_ID)]


@pytest.mark.parametrize("reply", [["lessons"], {"lessons": "none"}, {"why": "x"}, {"lessons": []}])
def test_replies_without_lessons(reply):
    assert targets.reflect_targets(make_case(), make_analysis(), make_outcome(), Fake(reply)) == []


def test_no_file_is_opened(monkeypatch):
    opened = []
    real_open = io.open

    def spy(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).suffix == ".json":
            opened.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", spy)
    monkeypatch.setattr(builtins, "open", spy)
    targets.score_targets(make_analysis(), make_outcome(), make_case())
    targets.reflect_targets(make_case(), make_analysis(), make_outcome(), Fake())
    monkeypatch.undo()
    assert opened == []


def test_targets_module_rules():
    text = Path(targets.__file__).read_text(encoding="utf-8")
    text.encode("ascii")
    for word in ("anthropic", "fromisoformat", "isfinite"):
        assert word not in text, word
