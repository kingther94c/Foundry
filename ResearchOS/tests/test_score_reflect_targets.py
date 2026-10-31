"""Scoring and reflecting on many targets (SPEC.md v27): score_targets, reflect_targets."""

import copy
import json
from dataclasses import replace

import pytest

from conftest import FakeLLM

from researchos.cases import Document, Outcome, Target, TargetOutcome, macro_case, target_keys
from researchos.playbook import Lesson
from researchos.reflect import REFLECT_SCHEMA
from researchos.targets import (MACRO_REFLECTOR_SYSTEM, TargetAnalysis, TargetView,
                                reflect_targets, score_targets)

# The SPEC's text, copied whole on one line.
SPEC_REFLECTOR = "You are the reflector in a research pipeline that trains macro strategists to forecast where market levels go in the weeks and months after a snapshot of the markets and the economy. The forecasts were sealed before the outcomes were known; now you see both. Explain why each target moved the way it did, where the forecasts went wrong or right, and write lessons that would hold on other dates. A lesson must not make a rule of a particular date, level or event: it is shown to the strategists on every later snapshot. Each lesson says when it applies and which perspective it belongs to."

SPEC_REQUEST = "Why did these targets move the way they did? Where were the forecasts wrong, and where right? Write lessons that would hold on other dates; never make a rule of a particular date, level or event."

CASE_ID = "snap-k41"
KEYS = ["DGS10_1w", "DGS10_1m", "SP500_1m", "VIXCLS_1w"]
FOUR = ["growth", "inflation", "labor", "monetary_policy"]

# Each perspective's probabilities in KEYS order. Quarters, so that every mean of the four
# and every Brier score below is exact in binary.
PROBS = {"growth": [0.75, 0.5, 0.25, 0.5],
         "inflation": [0.75, 0.25, 0.25, 0.75],
         "labor": [0.5, 0.75, 0.75, 0.25],
         "monetary_policy": [0.25, 0.5, 0.25, 0.75]}
WEIGHTED = [0.625, 0.375, 0.5, 0.25]
JUDGE = [0.75, 0.5, 0.875, 0.125]
THESIS = "Rates drift up while volatility stays bid."


def make_case():
    targets = [Target(id="DGS10", name="10-year Treasury yield", kind="bp",
                      horizons=["1w", "1m"], as_of_date="2026-10-30", as_of_value=4.12,
                      trailing={"1w": -3.0}),
               Target(id="SP500", name="S&P 500", kind="return", horizons=["1m"],
                      as_of_date="2026-10-29", as_of_value=6012.5, trailing={"1m": 0.021}),
               Target(id="VIXCLS", name="VIX", kind="points", horizons=["1w"],
                      as_of_date="2026-10-30", as_of_value=17)]
    documents = [Document("markets", "Markets at the close", "  Yields rose 5bp on the week.\n",
                          source="https://example.com/markets"),
                 Document("calendar", "Scheduled", "- 2026-11-06: payrolls")]
    return macro_case(CASE_ID, "2026-10-30", targets, documents)


def make_analysis(**changes):
    # The views are stored in another order than the perspectives: the perspectives decide.
    views = {name: TargetView(dict(zip(KEYS, PROBS[name])), [f"{name} key point"])
             for name in ["labor", "monetary_policy", "growth", "inflation"]}
    analysis = TargetAnalysis(case_id=CASE_ID, mode="holistic", perspectives=list(FOUR),
                              keys=list(KEYS), views=views,
                              prob_weighted=dict(zip(KEYS, WEIGHTED)),
                              prob_judge=dict(zip(KEYS, JUDGE)), thesis=THESIS)
    return replace(analysis, **changes)


DGS10_1W = TargetOutcome(target="DGS10", horizon="1w", kind="bp", start_date="2026-10-30",
                         start=4.12, end_date="2026-11-06", end=4.24, change=12.0, up=True)
SP500_1M = TargetOutcome(target="SP500", horizon="1m", kind="return", start_date="2026-10-29",
                         start=6012.5, end_date="2026-11-30", end=5938.5, change=-0.0123,
                         up=False)
VIXCLS_1W = TargetOutcome(target="VIXCLS", horizon="1w", kind="points",
                          start_date="2026-10-30", start=17, end_date="2026-11-06", end=17.85,
                          change=0.85, up=True)


def make_outcome():
    # DGS10_1m has not resolved; OTHER_1w is not a key of the case. Out of key order.
    other = replace(DGS10_1W, target="OTHER")
    return Outcome(targets={"VIXCLS_1w": VIXCLS_1W, "OTHER_1w": other,
                            "SP500_1m": SP500_1M, "DGS10_1w": DGS10_1W})


# ---- score_targets ----------------------------------------------------------------------

# Worked by hand from the SPEC. y is 1 for DGS10_1w and VIXCLS_1w, 0 for SP500_1m.
EXPECTED_RECORDS = [
    {"case_id": "snap-k41/DGS10_1w", "case": "snap-k41", "key": "DGS10_1w",
     "target": "DGS10", "horizon": "1w", "kind": "bp", "event_date": "2026-10-30",
     "resolution_date": "2026-11-06",
     "start_date": "2026-10-30", "start": 4.12, "end_date": "2026-11-06", "end": 4.24,
     "change": 12.0, "trailing_change": -3.0, "actual_up": True,
     "prob_weighted": 0.625, "prob_judge": 0.75,
     "prob_uniform": 0.5625,  # (0.75 + 0.75 + 0.5 + 0.25) / 4
     "prob_by_perspective": {"growth": 0.75, "inflation": 0.75, "labor": 0.5,
                             "monetary_policy": 0.25},
     "brier_weighted": 0.140625,  # (0.625 - 1) ** 2
     "brier_judge": 0.0625,  # (0.75 - 1) ** 2
     "brier_uniform": 0.19140625,  # (0.5625 - 1) ** 2
     "brier_coin": 0.25,
     "brier_by_perspective": {"growth": 0.0625, "inflation": 0.0625, "labor": 0.25,
                              "monetary_policy": 0.5625},
     "correct_weighted": True, "correct_judge": True},
    {"case_id": "snap-k41/SP500_1m", "case": "snap-k41", "key": "SP500_1m",
     "target": "SP500", "horizon": "1m", "kind": "return", "event_date": "2026-10-30",
     "resolution_date": "2026-11-30",  # from the case's date, not the target's as_of_date
     "start_date": "2026-10-29", "start": 6012.5, "end_date": "2026-11-30", "end": 5938.5,
     "change": -0.0123, "trailing_change": 0.021, "actual_up": False,
     "prob_weighted": 0.5, "prob_judge": 0.875,
     "prob_uniform": 0.375,  # (0.25 + 0.25 + 0.75 + 0.25) / 4
     "prob_by_perspective": {"growth": 0.25, "inflation": 0.25, "labor": 0.75,
                             "monetary_policy": 0.25},
     "brier_weighted": 0.25, "brier_judge": 0.765625, "brier_uniform": 0.140625,
     "brier_coin": 0.25,
     "brier_by_perspective": {"growth": 0.0625, "inflation": 0.0625, "labor": 0.5625,
                              "monetary_policy": 0.0625},
     # 0.5 is not above 0.5, so it is a forecast of "not up": correct here.
     "correct_weighted": True, "correct_judge": False},
    {"case_id": "snap-k41/VIXCLS_1w", "case": "snap-k41", "key": "VIXCLS_1w",
     "target": "VIXCLS", "horizon": "1w", "kind": "points", "event_date": "2026-10-30",
     "resolution_date": "2026-11-06",
     "start_date": "2026-10-30", "start": 17, "end_date": "2026-11-06", "end": 17.85,
     "change": 0.85, "trailing_change": None, "actual_up": True,
     "prob_weighted": 0.25, "prob_judge": 0.125,
     "prob_uniform": 0.5625,  # (0.5 + 0.75 + 0.25 + 0.75) / 4
     "prob_by_perspective": {"growth": 0.5, "inflation": 0.75, "labor": 0.25,
                             "monetary_policy": 0.75},
     "brier_weighted": 0.5625, "brier_judge": 0.765625, "brier_uniform": 0.19140625,
     "brier_coin": 0.25,
     "brier_by_perspective": {"growth": 0.25, "inflation": 0.0625, "labor": 0.5625,
                              "monetary_policy": 0.0625},
     "correct_weighted": False, "correct_judge": False},
]

RECORD_KEYS = ["case_id", "case", "key", "target", "horizon", "kind", "event_date",
               "resolution_date", "start_date", "start", "end_date", "end", "change",
               "trailing_change", "actual_up", "prob_weighted", "prob_judge", "prob_uniform",
               "prob_by_perspective", "brier_weighted", "brier_judge", "brier_uniform",
               "brier_coin", "brier_by_perspective", "correct_weighted", "correct_judge"]


def test_score_targets_gives_one_record_per_resolved_key_in_key_order():
    records = score_targets(make_analysis(), make_outcome(), make_case())
    assert records == EXPECTED_RECORDS
    for record in records:
        assert list(record) == RECORD_KEYS
        assert list(record["prob_by_perspective"]) == FOUR
        assert list(record["brier_by_perspective"]) == FOUR


def test_score_targets_with_nothing_resolved_gives_no_record():
    assert score_targets(make_analysis(), Outcome(), make_case()) == []
    assert score_targets(make_analysis(), Outcome(targets={"OTHER_1w": DGS10_1W}),
                         make_case()) == []


def test_score_targets_records_hold_no_text_and_are_json():
    records = score_targets(make_analysis(), make_outcome(), make_case())
    text = json.dumps(records)
    for secret in (THESIS, "key point", "Yields rose", "payrolls", "Treasury yield",
                   "example.com"):
        assert secret not in text
    assert json.loads(text) == records


def test_score_targets_does_not_change_its_inputs():
    analysis, outcome, case = make_analysis(), make_outcome(), make_case()
    before = copy.deepcopy((analysis, outcome, case))
    records = score_targets(analysis, outcome, case)
    records[0]["prob_by_perspective"]["growth"] = 0.0
    assert (analysis, outcome, case) == before


BAD_ANALYSES = [
    lambda: make_analysis(case_id="snap-other"),
    lambda: make_analysis(keys=KEYS[::-1]),
    lambda: make_analysis(keys=KEYS[:3]),
    lambda: make_analysis(keys=KEYS + ["SP500_3m"]),
]


@pytest.mark.parametrize("build", BAD_ANALYSES)
def test_score_targets_refuses_an_analysis_of_another_case_or_other_targets(build):
    with pytest.raises(ValueError) as info:
        score_targets(build(), make_outcome(), make_case())
    assert CASE_ID in str(info.value)


def test_the_case_id_check_names_both_cases():
    with pytest.raises(ValueError) as info:
        score_targets(make_analysis(case_id="snap-other"), make_outcome(), make_case())
    assert "snap-other" in str(info.value) and CASE_ID in str(info.value)


def test_target_keys_errors_pass_through():
    case = make_case()
    case.targets[0].kind = "level"
    with pytest.raises(ValueError) as expected:
        target_keys(case)
    for call in (lambda: score_targets(make_analysis(), make_outcome(), case),
                 lambda: reflect_targets(case, make_analysis(), make_outcome(), FakeLLM())):
        with pytest.raises(ValueError) as info:
            call()
        assert str(info.value) == str(expected.value)


# ---- reflect_targets --------------------------------------------------------------------

def test_reflector_system_prompt_is_verbatim():
    assert MACRO_REFLECTOR_SYSTEM == SPEC_REFLECTOR
    assert "\n" not in MACRO_REFLECTOR_SYSTEM and MACRO_REFLECTOR_SYSTEM.isascii()


DOCUMENTS = ("## [markets] Markets at the close\n\nYields rose 5bp on the week.\n\n"
             "## [calendar] Scheduled\n\n- 2026-11-06: payrolls\n")

PART_DGS10_1W = ("### DGS10_1w (10-year Treasury yield, 1w)\n"
                 "Perspectives: growth 0.75, inflation 0.75, labor 0.50, monetary_policy 0.25\n"
                 "Combined: 0.625; judge: 0.750\n"
                 "Outcome: from 4.12 on 2026-10-30 to 4.24 on 2026-11-06, +12 bp: up")
PART_SP500_1M = ("### SP500_1m (S&P 500, 1m)\n"
                 "Perspectives: growth 0.25, inflation 0.25, labor 0.75, monetary_policy 0.25\n"
                 "Combined: 0.500; judge: 0.875\n"
                 "Outcome: from 6012.50 on 2026-10-29 to 5938.50 on 2026-11-30, -1.2%: not up")
PART_VIXCLS_1W = ("### VIXCLS_1w (VIX, 1w)\n"
                  "Perspectives: growth 0.50, inflation 0.75, labor 0.25, monetary_policy 0.75\n"
                  "Combined: 0.250; judge: 0.125\n"
                  "Outcome: from 17.00 on 2026-10-30 to 17.85 on 2026-11-06, +0.85: up")


def expected_prompt(*key_parts):
    return ("Snapshot as of 2026-10-30: the sealed forecasts and what happened.\n\n"
            + "".join(part + "\n\n" for part in key_parts)
            + f"Judge's thesis: {THESIS}\n\n"
            + "Documents\n\n" + DOCUMENTS + "\n\n"
            + SPEC_REQUEST + "\n\n"
            + 'Each lesson\'s perspective is one of: growth, inflation, labor, '
              'monetary_policy, or "general".')


REPLY = {
    "why_market_moved": "ignored", "where_we_were_wrong": "ignored", "extra": 1,
    "lessons": [
        {"lesson": "  Payrolls weeks move the front end.  ", "applies_when": " a payrolls week ",
         "perspective": " Labor "},
        {"lesson": "Guidance matters.", "applies_when": 3, "perspective": "guidance"},
        {"lesson": "   ", "applies_when": "never", "perspective": "growth"},
        "not a dict",
        {"lesson": 5, "applies_when": "x", "perspective": "growth"},
        {"lesson": "Ranges hold.", "perspective": "GENERAL"},
        {"lesson": "Auctions matter.", "applies_when": "a refunding", "perspective": "fiscal_supply"},
        {"lesson": "Policy leads.", "applies_when": "a meeting", "perspective": "monetary_policy",
         "extra": True},
        {"lesson": "No perspective.", "applies_when": "always", "perspective": None},
    ],
}

EXPECTED_LESSONS = [
    Lesson("Payrolls weeks move the front end.", "a payrolls week", "labor", CASE_ID),
    Lesson("Guidance matters.", "", "general", CASE_ID),  # an earnings perspective
    Lesson("Ranges hold.", "", "general", CASE_ID),
    # fiscal_supply is a macro perspective, but not one of this analysis's.
    Lesson("Auctions matter.", "a refunding", "general", CASE_ID),
    Lesson("Policy leads.", "a meeting", "monetary_policy", CASE_ID),
    Lesson("No perspective.", "always", "general", CASE_ID),
]


def test_reflect_targets_reflects_on_every_resolved_key():
    reflector = FakeLLM(responses=[REPLY])
    lessons = reflect_targets(make_case(), make_analysis(), make_outcome(), reflector)
    assert lessons == EXPECTED_LESSONS
    (call,) = reflector.calls
    assert call["system"] == SPEC_REFLECTOR
    assert call["schema"] is REFLECT_SCHEMA
    assert call["prefix"] is None
    assert call["prompt"] == expected_prompt(PART_DGS10_1W, PART_SP500_1M, PART_VIXCLS_1W)
    # The case id, the unresolved key and the documents' sources are not in the prompt.
    assert CASE_ID not in call["prompt"] and "DGS10_1m" not in call["prompt"]
    assert "example.com" not in call["prompt"]


def test_reflect_targets_takes_given_keys_in_key_order_each_once():
    reflector = FakeLLM(responses=[{"lessons": []}])
    got = reflect_targets(make_case(), make_analysis(), make_outcome(), reflector,
                          keys=["VIXCLS_1w", "DGS10_1w", "VIXCLS_1w"])
    assert got == []
    (call,) = reflector.calls
    assert call["prompt"] == expected_prompt(PART_DGS10_1W, PART_VIXCLS_1W)

    reflector = FakeLLM(responses=[{"lessons": []}])
    reflect_targets(make_case(), make_analysis(), make_outcome(), reflector,
                    keys=("SP500_1m",))
    assert reflector.calls[0]["prompt"] == expected_prompt(PART_SP500_1M)


@pytest.mark.parametrize("keys, named", [
    (["DGS10_1w", "NOPE_1w"], "NOPE_1w"),  # not a key of the analysis
    (["OTHER_1w"], "OTHER_1w"),  # in the outcome, but not a key of the analysis
    (["SP500_1m", "DGS10_1m"], "DGS10_1m"),  # a key, but it has no TargetOutcome
])
def test_reflect_targets_refuses_a_key_it_cannot_reflect_on_before_any_call(keys, named):
    reflector = FakeLLM(responses=[REPLY])
    with pytest.raises(ValueError) as info:
        reflect_targets(make_case(), make_analysis(), make_outcome(), reflector, keys=keys)
    assert named in str(info.value)
    assert reflector.calls == []


@pytest.mark.parametrize("build", BAD_ANALYSES)
def test_reflect_targets_checks_the_analysis_first(build):
    reflector = FakeLLM(responses=[REPLY])
    for keys in (None, [], ["NOPE_1w"]):
        with pytest.raises(ValueError) as info:
            reflect_targets(make_case(), build(), make_outcome(), reflector, keys=keys)
        assert CASE_ID in str(info.value) and "NOPE_1w" not in str(info.value)
    assert reflector.calls == []


def test_reflect_targets_with_no_key_makes_no_call():
    reflector = FakeLLM(responses=[REPLY])
    assert reflect_targets(make_case(), make_analysis(), Outcome(), reflector) == []
    assert reflect_targets(make_case(), make_analysis(), make_outcome(), reflector,
                           keys=[]) == []
    assert reflector.calls == []


@pytest.mark.parametrize("reply", [None, "lessons", ["a"], {}, {"lessons": None},
                                   {"lessons": "Rates matter."},
                                   {"lessons": {"lesson": "Rates matter."}}])
def test_reflect_targets_reads_a_reply_without_a_list_of_lessons_as_none(reply):
    reflector = FakeLLM(responses=[reply])
    assert reflect_targets(make_case(), make_analysis(), make_outcome(), reflector) == []
    assert len(reflector.calls) == 1
