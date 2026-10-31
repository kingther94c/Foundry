"""Acceptance tests for forecasting now and scoring later (v28), written from SPEC.md
independently of the code (held back from the agent that built it).

Each case traces to the "Forecast now, score later (v28)" section of SPEC.md. The fake models
answer by the schema they are given, so one set of fakes serves the macro and the earnings
roles, and the command line's fake client.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import researchos
from researchos import cases, compare, llm, loop, masking, playbook, targets
from researchos.__main__ import main
from researchos.cases import Case, Document, Outcome, Target, TargetOutcome
from researchos.perspectives import PERSPECTIVES
from researchos.playbook import Lesson, Playbook, load_playbook, save_playbook

MACRO_ID = "macro-2026-10-30"
CUTOFF = "2026-06-30"
MACRO_P = {name: round(0.30 + 0.04 * i, 2) for i, name in enumerate(targets.MACRO_PERSPECTIVES)}
OFFSET = {"DGS10_1w": 0.0, "DGS10_1m": 0.1, "SP500_1w": 0.2}
VIEW_PROBS = {"growth": 0.8, "profitability": 0.7, "guidance": 0.6, "cash_flow": 0.55,
              "management_tone": 0.5, "expectations_gap": 0.3, "risks": 0.2}
GUESS_MISS = {"guesses": [{"company": "Globex", "ticker": "GBX", "confidence": 0.2}],
              "clues": []}
GUESS_HIT = {"guesses": [{"company": "Acme Widgets", "ticker": "ACMW", "confidence": 1.0}],
             "clues": ["obvious"]}


# --- fakes ----------------------------------------------------------------------------------------

def answer(prompt, schema, guess=GUESS_MISS):
    props = schema["properties"]
    if "redactions" in props:
        return {"redactions": [{"term": "Zephyr", "replacement": "the product line",
                                "reason": "brand"}]}
    if "guesses" in props:
        return guess
    first = prompt.splitlines()[0] if prompt else ""
    name = first.split(": ", 1)[1] if first.startswith("Perspective: ") else None
    if "forecasts" in props:
        forecasts = {}
        for target_id, sub in props["forecasts"]["properties"].items():
            forecasts[target_id] = {}
            for field in sub["properties"]:
                key = f"{target_id}_{field[len('prob_up_'):]}"
                p = round(MACRO_P[name] + OFFSET[key], 2) if name else 0.55
                forecasts[target_id][field] = p
        if name:
            return {"key_points": [f"{name} point"], "forecasts": forecasts}
        return {"forecasts": forecasts, "thesis": "Rates drift lower."}
    if "stance" in props:
        return {"stance": "bullish", "prob_up": VIEW_PROBS[name], "conviction": "medium",
                "key_points": [f"{name} point"]}
    if "thesis" in props:
        return {"prob_up": 0.65, "thesis": "Beat and raise."}
    return {"lessons": []}


class Fake:
    def __init__(self, guess=GUESS_MISS, **attrs):
        self.calls = []
        self.guess = guess
        self.__dict__.update(attrs)

    def json(self, system, prompt, schema, *, prefix=None):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema,
                           "prefix": prefix})
        return answer(prompt, schema, self.guess)


def make_roles(guess=GUESS_MISS, analyst=None):
    return loop.Roles(masker=Fake(), guesser=Fake(guess), analyst=analyst or Fake(),
                      judge=Fake(), reflector=Fake())


def n_calls(roles):
    return sum(len(getattr(roles, name).calls)
               for name in ("masker", "guesser", "analyst", "judge", "reflector"))


class FakeClient:
    def __init__(self, stop_reason="end_turn"):
        self.requests = []
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        schema = kwargs["output_config"]["format"]["schema"]
        prompt = kwargs["messages"][0]["content"][-1]["text"]
        text = json.dumps(answer(prompt, schema))
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=None,
                                cache_creation_input_tokens=3)
        return SimpleNamespace(stop_reason=self.stop_reason, stop_details=None, usage=usage,
                               content=[SimpleNamespace(type="text", text=text)])


# --- cases ----------------------------------------------------------------------------------------

def days_before(as_of, n):
    return date.fromordinal(date(*map(int, as_of.split("-"))).toordinal() - n).isoformat()


def macro(case_id=MACRO_ID, as_of="2026-10-30"):
    return cases.macro_case(case_id, as_of, [
        Target("DGS10", "10-year Treasury yield", "bp", ["1w", "1m"], days_before(as_of, 1), 4.0,
               {"1w": -2.0, "1m": 31.0}),
        Target("SP500", "S&P 500", "return", ["1w"], days_before(as_of, 2), 6800.0),
    ], [Document("dashboard", f"Markets as of {as_of}", "| Target | Level |"),
        Document("statement", "FOMC statement", "The Committee decided.", "https://fed.example")])


def save_macro(root, case_id=MACRO_ID, as_of="2026-10-30"):
    case = macro(case_id, as_of)
    cases.save_case(root, case)
    cases.save_resolution(root, case_id, cases.resolution_manifest(case))
    return case


def earnings(case_id="acme-q4", event_date="2026-02-01"):
    return Case(case_id, "Acme Widgets Inc.", "ACMW", ["Acme", "WidgetPro"], "Q4 FY2025",
                event_date, [
                    Document("release", "Acme Widgets Inc. reports Q4 results",
                             "Acme Widgets Inc. reported revenue of $1,200.0 million, up 12%. "
                             "Zephyr sales grew 30%.", "https://acme.example/q4"),
                    Document("expectations", "Consensus", "Consensus revenue was $1,150.0 "
                                                          "million.")])


def save_earnings(root, case_id="acme-q4", event_date="2026-02-01", ar=0.031):
    case = earnings(case_id, event_date)
    outcome = None if ar is None else Outcome(ar, 0.04, 0.009, "Shares jumped.", [])
    cases.save_case(root, case, outcome)
    return case


def tree(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def sealed(root, case_id=MACRO_ID):
    return json.loads((Path(root) / case_id / "prediction.json").read_text(encoding="utf-8"))


def expected_macro(case, book, mode="holistic", model="", cutoff=CUTOFF):
    analysis = targets.analyze_targets(masking.unmasked(case), case, book, Fake(), Fake(),
                                       perspectives=targets.perspective_set("macro"), mode=mode)
    analysis.model = model
    analysis.knowledge_cutoff = cutoff
    return analysis


@pytest.fixture
def opened(monkeypatch):
    names = []
    real_open = io.open

    def spy(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, os.PathLike)):
            names.append(Path(file).name)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", spy)
    monkeypatch.setattr(builtins, "open", spy)
    return names


# --- cases, playbook, imports ---------------------------------------------------------------------

def test_the_prediction_file_moves_to_cases():
    assert cases.PREDICTION_FILE == "prediction.json"
    assert loop.PREDICTION_FILE == "prediction.json"


def test_snapshot_no_longer_imports_loop():
    from researchos import snapshot

    text = Path(snapshot.__file__).read_text(encoding="utf-8")
    assert "researchos.loop" not in text
    assert "PREDICTION_FILE" in text


@pytest.mark.parametrize("module", ["researchos.loop", "researchos.snapshot",
                                    "researchos.targets", "researchos.__main__"])
def test_each_module_imports_first(module):
    import tools

    paths = [str(Path(researchos.__file__).resolve().parents[1]),
             str(Path(tools.__file__).resolve().parents[1])]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        paths + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]))
    done = subprocess.run([sys.executable, "-c", f"import {module}"], env=env,
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def test_playbook_initial():
    assert Playbook.initial().weights == {p: 1.0 / len(PERSPECTIVES) for p in PERSPECTIVES}
    book = Playbook.initial(targets.MACRO_PERSPECTIVES)
    assert list(book.weights) == targets.MACRO_PERSPECTIVES
    assert all(w == 0.1 for w in book.weights.values())
    assert (book.lessons, book.history) == ([], [])
    other = Playbook.initial(("b", "a"))
    assert other.weights == {"b": 0.5, "a": 0.5} and list(other.weights) == ["b", "a"]


# --- train and evaluate ---------------------------------------------------------------------------

@pytest.mark.parametrize("run", ["train", "evaluate"])
def test_train_and_evaluate_refuse_a_macro_case(tmp_path, run):
    save_macro(tmp_path)
    before = tree(tmp_path)
    roles = make_roles()
    with pytest.raises(ValueError) as exc:
        getattr(loop, run)(tmp_path, [MACRO_ID], tmp_path / "pb.json", roles,
                           knowledge_cutoff=CUTOFF)
    assert MACRO_ID in str(exc.value)
    assert n_calls(roles) == 0
    assert tree(tmp_path) == before


def test_train_still_trains_an_earnings_case(tmp_path):
    save_earnings(tmp_path)
    results = loop.train(tmp_path, ["acme-q4"], tmp_path / "pb.json", make_roles())
    assert [r["case_id"] for r in results] == ["acme-q4"]


# --- forecast: a macro case ----------------------------------------------------------------------

def test_forecast_a_macro_case(tmp_path, opened):
    case = save_macro(tmp_path)
    cases.save_outcome(tmp_path, MACRO_ID, Outcome(targets={"DGS10_1w": TargetOutcome(
        "DGS10", "1w", "bp", "2026-10-29", 4.0, "2026-11-06", 3.8, -20.0, False)}))
    roles = make_roles()
    opened.clear()
    result = loop.forecast(tmp_path, [MACRO_ID], tmp_path / "pb.json", roles,
                           knowledge_cutoff=CUTOFF)
    assert "outcome.json" not in opened and "resolution.json" not in opened
    expected = expected_macro(case, Playbook.initial(targets.MACRO_PERSPECTIVES))
    assert sealed(tmp_path) == asdict(expected)
    assert result == [{
        "case_id": MACRO_ID, "event_date": "2026-10-30", "domain": "macro", "sealed": True,
        "mode": "holistic", "model": "", "keys": ["DGS10_1w", "DGS10_1m", "SP500_1w"],
        "prob_weighted": expected.prob_weighted, "prob_judge": expected.prob_judge,
        "prob_by_perspective": {name: view.probs for name, view in expected.views.items()}}]
    assert list(result[0]) == ["case_id", "event_date", "domain", "sealed", "mode", "model",
                               "keys", "prob_weighted", "prob_judge", "prob_by_perspective"]
    assert list(result[0]["prob_by_perspective"]) == targets.MACRO_PERSPECTIVES
    assert len(roles.analyst.calls) == 10 and len(roles.judge.calls) == 1
    assert not roles.masker.calls and not roles.guesser.calls and not roles.reflector.calls
    assert not (tmp_path / MACRO_ID / "masked.json").exists()
    assert not (tmp_path / "pb.json").exists()


def test_the_macro_prompts(tmp_path):
    save_macro(tmp_path)
    roles = make_roles()
    loop.forecast(tmp_path, [MACRO_ID], tmp_path / "pb.json", roles, knowledge_cutoff=CUTOFF)
    firsts = [c["prompt"].splitlines()[0] for c in roles.analyst.calls]
    assert firsts == [f"Perspective: {name}" for name in targets.MACRO_PERSPECTIVES]
    assert all(c["system"] == targets.MACRO_ANALYST_SYSTEM for c in roles.analyst.calls)
    assert roles.judge.calls[0]["system"] == targets.MACRO_JUDGE_SYSTEM
    for call in roles.analyst.calls + roles.judge.calls:
        assert MACRO_ID not in call["system"] + call["prompt"] + (call["prefix"] or "")


def test_forecast_isolated(tmp_path):
    case = save_macro(tmp_path)
    roles = make_roles()
    result = loop.forecast(tmp_path, [MACRO_ID], tmp_path / "pb.json", roles,
                           knowledge_cutoff=CUTOFF, perspective_mode="isolated")
    assert result[0]["mode"] == "isolated"
    assert roles.analyst.calls[0]["system"] == targets.MACRO_ISOLATED_ANALYST_SYSTEM
    expected = expected_macro(case, Playbook.initial(targets.MACRO_PERSPECTIVES), "isolated")
    assert sealed(tmp_path) == asdict(expected)


@pytest.mark.parametrize("attrs, model", [({"model": "claude-x"}, "claude-x"),
                                           ({"model": 5}, ""), ({}, "")])
def test_the_analysts_model_is_recorded(tmp_path, attrs, model):
    save_macro(tmp_path)
    roles = make_roles(analyst=Fake(**attrs))
    result = loop.forecast(tmp_path, [MACRO_ID], tmp_path / "pb.json", roles,
                           knowledge_cutoff=date(2026, 6, 30))
    assert result[0]["model"] == model
    assert sealed(tmp_path)["model"] == model
    assert sealed(tmp_path)["knowledge_cutoff"] == "2026-06-30"


def test_a_macro_playbook_is_used_and_never_written(tmp_path):
    case = save_macro(tmp_path)
    book = Playbook(weights={name: (2.0 if name == "labor" else 1.0)
                             for name in reversed(targets.MACRO_PERSPECTIVES)},
                    lessons=[Lesson("Curves steepen after cuts.", "cuts", "monetary_policy",
                                    "macro-2026-07-03")],
                    history=[{"case_id": "macro-2026-07-03/DGS10_1w", "event_date": "2026-07-03"}])
    save_playbook(tmp_path / "pb.json", book)
    before = (tmp_path / "pb.json").read_bytes()
    roles = make_roles()
    loop.forecast(tmp_path, [MACRO_ID], tmp_path / "pb.json", roles, knowledge_cutoff=CUTOFF)
    assert (tmp_path / "pb.json").read_bytes() == before
    assert all("Curves steepen after cuts." in c["prompt"] for c in roles.analyst.calls)
    assert sealed(tmp_path) == asdict(expected_macro(case, load_playbook(tmp_path / "pb.json")))


@pytest.mark.parametrize("weights", [
    {p: 1.0 for p in PERSPECTIVES},
    {p: 1.0 for p in targets.MACRO_PERSPECTIVES[1:]},
    {**{p: 1.0 for p in targets.MACRO_PERSPECTIVES}, "risks": 1.0},
])
def test_a_playbook_of_another_domain_is_refused(tmp_path, weights):
    save_macro(tmp_path)
    save_playbook(tmp_path / "pb.json", Playbook(weights=weights))
    before = tree(tmp_path)
    roles = make_roles()
    with pytest.raises(ValueError) as exc:
        loop.forecast(tmp_path, [MACRO_ID], tmp_path / "pb.json", roles, knowledge_cutoff=CUTOFF)
    assert "pb.json" in str(exc.value) and "macro" in str(exc.value)
    assert n_calls(roles) == 0 and tree(tmp_path) == before


@pytest.mark.parametrize("cutoff", [None, "2026-10-30", "2026-11-01"])
def test_a_macro_case_needs_a_date_after_the_cutoff(tmp_path, cutoff):
    save_macro(tmp_path)
    before = tree(tmp_path)
    roles = make_roles()
    with pytest.raises(ValueError) as exc:
        loop.forecast(tmp_path, [MACRO_ID], tmp_path / "pb.json", roles, knowledge_cutoff=cutoff)
    assert MACRO_ID in str(exc.value)
    assert n_calls(roles) == 0 and tree(tmp_path) == before


@pytest.mark.parametrize("kwargs", [{"knowledge_cutoff": "June"},
                                    {"knowledge_cutoff": CUTOFF, "perspective_mode": "both"}])
def test_bad_arguments_are_refused_first(tmp_path, kwargs):
    roles = make_roles()
    with pytest.raises(ValueError):
        loop.forecast(tmp_path, ["no-such-case"], tmp_path / "pb.json", roles, **kwargs)
    assert n_calls(roles) == 0 and tree(tmp_path) == {}


def test_no_case_id(tmp_path):
    roles = make_roles()
    assert loop.forecast(tmp_path, [], tmp_path / "pb.json", roles, knowledge_cutoff=CUTOFF) == []
    assert n_calls(roles) == 0 and tree(tmp_path) == {}


def test_an_id_given_twice(tmp_path):
    save_macro(tmp_path)
    with pytest.raises(ValueError) as exc:
        loop.forecast(tmp_path, [MACRO_ID, MACRO_ID], tmp_path / "pb.json", make_roles(),
                      knowledge_cutoff=CUTOFF)
    assert MACRO_ID in str(exc.value)
    assert not (tmp_path / MACRO_ID / "prediction.json").exists()


def test_a_missing_case(tmp_path):
    save_macro(tmp_path)
    roles = make_roles()
    with pytest.raises(OSError):
        loop.forecast(tmp_path, [MACRO_ID, "macro-2026-11-06"], tmp_path / "pb.json", roles,
                      knowledge_cutoff=CUTOFF)
    assert n_calls(roles) == 0


def test_one_domain_per_run(tmp_path):
    save_macro(tmp_path)
    save_earnings(tmp_path, event_date="2026-10-01")
    before = tree(tmp_path)
    roles = make_roles()
    with pytest.raises(ValueError):
        loop.forecast(tmp_path, [MACRO_ID, "acme-q4"], tmp_path / "pb.json", roles,
                      knowledge_cutoff=CUTOFF)
    assert n_calls(roles) == 0 and tree(tmp_path) == before


def test_a_forecast_is_sealed_once(tmp_path):
    save_macro(tmp_path, "macro-2026-10-23", "2026-10-23")
    save_macro(tmp_path)
    (tmp_path / MACRO_ID / "prediction.json").write_text('{"sealed": "before"}', encoding="utf-8")
    before = tree(tmp_path)
    roles = make_roles()
    with pytest.raises(ValueError) as exc:
        loop.forecast(tmp_path, ["macro-2026-10-23", MACRO_ID], tmp_path / "pb.json", roles,
                      knowledge_cutoff=CUTOFF)
    assert MACRO_ID in str(exc.value)
    assert n_calls(roles) == 0 and tree(tmp_path) == before


def test_no_look_ahead_of_the_playbook(tmp_path):
    save_macro(tmp_path)
    book = Playbook.initial(targets.MACRO_PERSPECTIVES)
    book.history.append({"case_id": "macro-2026-10-30/DGS10_1w", "event_date": "2026-11-06"})
    save_playbook(tmp_path / "pb.json", book)
    roles = make_roles()
    with pytest.raises(ValueError) as exc:
        loop.forecast(tmp_path, [MACRO_ID], tmp_path / "pb.json", roles, knowledge_cutoff=CUTOFF)
    assert str(exc.value).startswith("forecast refused: ")
    assert MACRO_ID in str(exc.value)
    assert n_calls(roles) == 0


def test_an_undated_case_is_refused_before_the_cutoff_check(tmp_path):
    case = macro()
    case.event_date = "2026-10-32"
    cases.save_case(tmp_path, case)
    with pytest.raises(ValueError) as exc:
        loop.forecast(tmp_path, [MACRO_ID], tmp_path / "pb.json", make_roles(),
                      knowledge_cutoff=CUTOFF)
    assert str(exc.value).startswith("forecast refused: ")


def test_several_cases_in_the_order_given(tmp_path):
    later = save_macro(tmp_path)
    earlier = save_macro(tmp_path, "macro-2026-10-23", "2026-10-23")
    roles = make_roles()
    result = loop.forecast(tmp_path, [MACRO_ID, "macro-2026-10-23"], tmp_path / "pb.json", roles,
                           knowledge_cutoff=CUTOFF)
    assert [r["case_id"] for r in result] == [MACRO_ID, "macro-2026-10-23"]
    assert [r["event_date"] for r in result] == ["2026-10-30", "2026-10-23"]
    prefixes = [c["prefix"] for c in roles.judge.calls]
    assert prefixes[0].startswith("Macro snapshot as of 2026-10-30:")
    assert prefixes[1].startswith("Macro snapshot as of 2026-10-23:")
    book = Playbook.initial(targets.MACRO_PERSPECTIVES)
    assert sealed(tmp_path) == asdict(expected_macro(later, book))
    assert sealed(tmp_path, "macro-2026-10-23") == asdict(expected_macro(earlier, book))


def test_a_failure_part_way_keeps_what_was_sealed(tmp_path):
    save_macro(tmp_path, "macro-2026-10-23", "2026-10-23")
    save_macro(tmp_path)

    class Failing(Fake):
        def json(self, system, prompt, schema, *, prefix=None):
            if MACRO_ID[-10:] in (prefix or ""):
                raise llm.LLMError("no reply")
            return super().json(system, prompt, schema, prefix=prefix)

    with pytest.raises(llm.LLMError):
        loop.forecast(tmp_path, ["macro-2026-10-23", MACRO_ID], tmp_path / "pb.json",
                      make_roles(analyst=Failing()), knowledge_cutoff=CUTOFF)
    assert (tmp_path / "macro-2026-10-23" / "prediction.json").is_file()
    assert not (tmp_path / MACRO_ID / "prediction.json").exists()
    with pytest.raises(ValueError):
        loop.forecast(tmp_path, ["macro-2026-10-23", MACRO_ID], tmp_path / "pb.json",
                      make_roles(), knowledge_cutoff=CUTOFF)
    loop.forecast(tmp_path, [MACRO_ID], tmp_path / "pb.json", make_roles(),
                  knowledge_cutoff=CUTOFF)
    assert (tmp_path / MACRO_ID / "prediction.json").is_file()


# --- forecast: an earnings case -------------------------------------------------------------------

def test_forecast_an_earnings_case_as_evaluate_does(tmp_path, opened):
    a, b = tmp_path / "a", tmp_path / "b"
    save_earnings(a)
    book = Playbook.initial()
    book.lessons = [Lesson("Acme always beats.", "", "growth", "x"),
                    Lesson("Guidance raises dominate.", "raised", "guidance", "y")]
    save_playbook(a / "pb.json", book)
    shutil.copytree(a, b)
    forecast_roles, evaluate_roles = make_roles(), make_roles()
    opened.clear()
    result = loop.forecast(a, ["acme-q4"], a / "pb.json", forecast_roles, seed=3)
    assert "outcome.json" not in opened
    loop.evaluate(b, ["acme-q4"], b / "pb.json", evaluate_roles, seed=3)
    for name in ("prediction.json", "masked.json"):
        assert (a / "acme-q4" / name).read_bytes() == (b / "acme-q4" / name).read_bytes(), name
    for role in ("masker", "guesser", "analyst", "judge"):
        assert getattr(forecast_roles, role).calls == getattr(evaluate_roles, role).calls, role
    assert not any("Acme always beats." in c["prompt"] for c in forecast_roles.analyst.calls)
    assert not forecast_roles.reflector.calls
    data = sealed(a, "acme-q4")
    assert result == [{"case_id": "acme-q4", "event_date": "2026-02-01", "domain": "earnings",
                       "sealed": True, "prob_weighted": data["prob_weighted"],
                       "prob_judge": data["prob_judge"],
                       "prob_by_perspective": {name: data["views"][name]["prob_up"]
                                               for name in PERSPECTIVES},
                       "identifiable": False}]
    assert list(result[0]) == ["case_id", "event_date", "domain", "sealed", "prob_weighted",
                               "prob_judge", "prob_by_perspective", "identifiable"]
    assert load_playbook(a / "pb.json").lessons == book.lessons


def test_an_identifiable_earnings_case_is_skipped(tmp_path):
    save_earnings(tmp_path)
    roles = make_roles(GUESS_HIT)
    result = loop.forecast(tmp_path, ["acme-q4"], tmp_path / "pb.json", roles)
    assert result == [{"case_id": "acme-q4", "event_date": "2026-02-01", "domain": "earnings",
                       "sealed": False, "skipped": "identifiable"}]
    assert not roles.analyst.calls
    assert not (tmp_path / "acme-q4" / "prediction.json").exists()
    assert (tmp_path / "acme-q4" / "masked.json").is_file()


def test_an_identifiable_case_after_the_cutoff_is_forecast(tmp_path):
    save_earnings(tmp_path)
    result = loop.forecast(tmp_path, ["acme-q4"], tmp_path / "pb.json", make_roles(GUESS_HIT),
                           knowledge_cutoff="2026-01-31")
    assert result[0]["sealed"] is True and result[0]["identifiable"] is True


def test_an_earnings_run_needs_an_earnings_playbook(tmp_path):
    save_earnings(tmp_path)
    save_playbook(tmp_path / "pb.json", Playbook.initial(targets.MACRO_PERSPECTIVES))
    roles = make_roles()
    with pytest.raises(ValueError) as exc:
        loop.forecast(tmp_path, ["acme-q4"], tmp_path / "pb.json", roles)
    assert "earnings" in str(exc.value)
    assert n_calls(roles) == 0


# --- load_prediction ------------------------------------------------------------------------------

def test_load_a_macro_prediction(tmp_path):
    save_macro(tmp_path)
    loop.forecast(tmp_path, [MACRO_ID], tmp_path / "pb.json", make_roles(),
                  knowledge_cutoff=CUTOFF)
    analysis = loop.load_prediction(tmp_path, MACRO_ID)
    assert isinstance(analysis, targets.TargetAnalysis)
    assert asdict(analysis) == sealed(tmp_path)


def test_load_an_earnings_prediction(tmp_path):
    save_earnings(tmp_path)
    loop.forecast(tmp_path, ["acme-q4"], tmp_path / "pb.json", make_roles())
    analysis = loop.load_prediction(tmp_path, "acme-q4")
    assert type(analysis).__name__ == "Analysis"
    assert asdict(analysis) == sealed(tmp_path, "acme-q4")
    assert list(analysis.views) == PERSPECTIVES
    assert analysis.views["growth"].prob_up == 0.8


@pytest.mark.parametrize("text, error", [
    ("[1, 2]", ValueError),
    ('{"case_id": "acme-q4", "views": {}, "prob_weighted": 0.5, "prob_judge": 0.5}', KeyError),
    ('{"case_id": "acme-q4", "views": {"growth": {"stance": "bullish", "prob_up": 0.5, '
     '"conviction": "low"}}, "prob_weighted": 0.5, "prob_judge": 0.5, "thesis": ""}', KeyError),
    ('{"case_id": "m", "mode": "holistic", "perspectives": [], "keys": []}', KeyError),
])
def test_a_malformed_prediction(tmp_path, text, error):
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "prediction.json").write_text(text, encoding="utf-8")
    with pytest.raises(error):
        loop.load_prediction(tmp_path, "c")


def test_a_missing_prediction(tmp_path):
    with pytest.raises(OSError):
        loop.load_prediction(tmp_path, "c")


# --- score_sealed ---------------------------------------------------------------------------------

def forecast_macro(root, *case_ids):
    loop.forecast(root, list(case_ids), Path(root) / "pb.json", make_roles(),
                  knowledge_cutoff=CUTOFF)


DGS10_1W = TargetOutcome("DGS10", "1w", "bp", "2026-10-29", 4.0, "2026-11-06", 3.8125, -18.75,
                         False)


def test_score_what_has_resolved(tmp_path, opened):
    case = save_macro(tmp_path)
    forecast_macro(tmp_path, MACRO_ID)
    outcome = Outcome(targets={"DGS10_1w": DGS10_1W})
    cases.save_outcome(tmp_path, MACRO_ID, outcome)
    before = tree(tmp_path)
    opened.clear()
    report = loop.score_sealed(tmp_path, [MACRO_ID])
    assert "resolution.json" not in opened
    assert tree(tmp_path) == before
    records = targets.score_targets(loop.load_prediction(tmp_path, MACRO_ID), outcome, case)
    assert len(records) == 1
    record = records[0]
    assert report == {
        "cases": records, "n": 1,
        "pending": [f"{MACRO_ID}/DGS10_1m", f"{MACRO_ID}/SP500_1w"],
        "brier_weighted": record["brier_weighted"], "brier_judge": record["brier_judge"],
        "brier_uniform": record["brier_uniform"], "brier_coin": 0.25,
        "accuracy_weighted": 1.0 if record["correct_weighted"] else 0.0,
        "accuracy_judge": 0.0}
    assert list(report) == ["cases", "n", "pending", "brier_weighted", "brier_judge",
                            "brier_uniform", "brier_coin", "accuracy_weighted", "accuracy_judge"]
    assert report["brier_judge"] == pytest.approx(0.55 ** 2)


def test_nothing_resolved_yet(tmp_path):
    save_macro(tmp_path)
    forecast_macro(tmp_path, MACRO_ID)
    report = loop.score_sealed(tmp_path, [MACRO_ID])
    assert report == {"cases": [], "n": 0,
                      "pending": [f"{MACRO_ID}/{k}" for k in ("DGS10_1w", "DGS10_1m", "SP500_1w")],
                      "brier_weighted": None, "brier_judge": None, "brier_uniform": None,
                      "brier_coin": None, "accuracy_weighted": None, "accuracy_judge": None}


def test_several_macro_cases(tmp_path):
    save_macro(tmp_path)
    save_macro(tmp_path, "macro-2026-10-23", "2026-10-23")
    forecast_macro(tmp_path, MACRO_ID, "macro-2026-10-23")
    up = TargetOutcome("SP500", "1w", "return", "2026-10-21", 6800.0, "2026-10-30", 6900.0,
                       100 / 6800, True)
    cases.save_outcome(tmp_path, "macro-2026-10-23", Outcome(targets={"SP500_1w": up}))
    cases.save_outcome(tmp_path, MACRO_ID, Outcome(targets={"DGS10_1w": DGS10_1W}))
    report = loop.score_sealed(tmp_path, ["macro-2026-10-23", MACRO_ID])
    assert [r["case_id"] for r in report["cases"]] == ["macro-2026-10-23/SP500_1w",
                                                       f"{MACRO_ID}/DGS10_1w"]
    assert report["pending"] == ["macro-2026-10-23/DGS10_1w", "macro-2026-10-23/DGS10_1m",
                                 f"{MACRO_ID}/DGS10_1m", f"{MACRO_ID}/SP500_1w"]
    assert report["n"] == 2
    assert report["brier_judge"] == pytest.approx((0.45 ** 2 + 0.55 ** 2) / 2)
    assert report["accuracy_judge"] == 0.5


def test_score_an_earnings_case(tmp_path):
    save_earnings(tmp_path)
    save_earnings(tmp_path, "acme-q1", "2026-05-01", ar=None)
    loop.forecast(tmp_path, ["acme-q4", "acme-q1"], tmp_path / "pb.json", make_roles())
    report = loop.score_sealed(tmp_path, ["acme-q4", "acme-q1"])
    analysis = loop.load_prediction(tmp_path, "acme-q4")
    outcome = cases.load_outcome(tmp_path, "acme-q4")
    mean = sum(VIEW_PROBS.values()) / len(VIEW_PROBS)
    record = report["cases"][0]
    assert record == {"case_id": "acme-q4", "event_date": "2026-02-01",
                      **loop.score(analysis, outcome), "brier_uniform": pytest.approx((mean - 1) ** 2)}
    assert list(record) == ["case_id", "event_date", *loop.score(analysis, outcome),
                            "brier_uniform"]
    assert record["brier_judge"] == pytest.approx(0.35 ** 2)
    assert report["pending"] == ["acme-q1"] and report["n"] == 1
    assert report["accuracy_judge"] == 1.0


def test_compare_reads_the_report(tmp_path):
    save_macro(tmp_path)
    forecast_macro(tmp_path, MACRO_ID)
    cases.save_outcome(tmp_path, MACRO_ID, Outcome(targets={"DGS10_1w": DGS10_1W}))
    report = loop.score_sealed(tmp_path, [MACRO_ID])
    assert compare.paired(report, report, "brier_judge") == [
        (f"{MACRO_ID}/DGS10_1w", report["brier_judge"], report["brier_judge"])]


def test_score_refusals(tmp_path):
    save_macro(tmp_path)
    save_macro(tmp_path, "macro-2026-10-23", "2026-10-23")
    save_earnings(tmp_path)
    forecast_macro(tmp_path, MACRO_ID)
    with pytest.raises(ValueError):
        loop.score_sealed(tmp_path, [MACRO_ID, MACRO_ID])
    with pytest.raises(OSError):
        loop.score_sealed(tmp_path, [MACRO_ID, "nope"])
    with pytest.raises(ValueError):
        loop.score_sealed(tmp_path, [MACRO_ID, "acme-q4"])
    with pytest.raises(ValueError) as exc:
        loop.score_sealed(tmp_path, [MACRO_ID, "macro-2026-10-23"])
    assert "macro-2026-10-23" in str(exc.value)


def test_a_forecast_of_another_case_is_refused(tmp_path):
    save_macro(tmp_path)
    save_macro(tmp_path, "macro-2026-10-23", "2026-10-23")
    forecast_macro(tmp_path, MACRO_ID)
    shutil.copyfile(tmp_path / MACRO_ID / "prediction.json",
                    tmp_path / "macro-2026-10-23" / "prediction.json")
    with pytest.raises(ValueError) as exc:
        loop.score_sealed(tmp_path, ["macro-2026-10-23"])
    assert "macro-2026-10-23" in str(exc.value)


def test_a_forecast_of_another_kind_is_refused(tmp_path):
    save_earnings(tmp_path)
    loop.forecast(tmp_path, ["acme-q4"], tmp_path / "pb.json", make_roles())
    save_macro(tmp_path)
    shutil.copyfile(tmp_path / "acme-q4" / "prediction.json",
                    tmp_path / MACRO_ID / "prediction.json")
    data = sealed(tmp_path)
    data["case_id"] = MACRO_ID
    (tmp_path / MACRO_ID / "prediction.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        loop.score_sealed(tmp_path, [MACRO_ID])
    assert MACRO_ID in str(exc.value)


# --- the command line -----------------------------------------------------------------------------

def run_main(argv, capsys, client=None):
    made = []

    def factory():
        made.append(1)
        if client is None:
            raise AssertionError("no client should be built")
        return client

    code = main([str(a) for a in argv], client_factory=factory)
    out, err = capsys.readouterr()
    return code, out, err, made


def test_the_forecast_command(tmp_path, capsys):
    save_macro(tmp_path)
    out_file = tmp_path / "forecast.json"
    code, out, err, made = run_main(
        ["forecast", "--root", tmp_path, "--playbook", tmp_path / "pb.json", "--ids", MACRO_ID,
         "--knowledge-cutoff", CUTOFF, "--perspective-mode", "isolated", "--out", out_file],
        capsys, FakeClient())
    assert (code, out) == (0, "")
    printed = json.loads(out_file.read_text(encoding="utf-8"))
    assert list(printed) == ["cases"]
    assert printed["cases"][0]["mode"] == "isolated"
    assert printed["cases"][0]["model"] == llm.DEFAULT_MODEL
    data = sealed(tmp_path)
    assert (data["model"], data["knowledge_cutoff"], data["mode"]) == (
        llm.DEFAULT_MODEL, CUTOFF, "isolated")
    assert printed["cases"][0]["prob_judge"] == data["prob_judge"]


def test_the_forecast_command_prints(tmp_path, capsys):
    save_macro(tmp_path)
    code, out, err, made = run_main(
        ["forecast", "--root", tmp_path, "--playbook", tmp_path / "pb.json", "--ids", MACRO_ID,
         "--knowledge-cutoff", CUTOFF], capsys, FakeClient())
    assert code == 0
    assert json.loads(out)["cases"][0]["mode"] == "holistic"


def test_the_forecast_command_fails_cleanly(tmp_path, capsys):
    save_macro(tmp_path)
    base = ["forecast", "--root", tmp_path, "--playbook", tmp_path / "pb.json"]
    code, out, err, made = run_main(base + ["--ids", " , "], capsys, FakeClient())
    assert code == 2 and not made
    code, out, err, made = run_main(base + ["--ids", "nope", "--knowledge-cutoff", CUTOFF],
                                    capsys, FakeClient())
    assert code == 1 and not made and "nope" in err
    code, out, err, made = run_main(base + ["--ids", MACRO_ID], capsys, FakeClient())
    assert code == 1 and "error" in err and out == ""
    code, out, err, made = run_main(base + ["--ids", MACRO_ID, "--knowledge-cutoff", CUTOFF],
                                    capsys, FakeClient(stop_reason="refusal"))
    assert code == 1 and "error" in err
    assert not (tmp_path / MACRO_ID / "prediction.json").exists()


def test_the_score_command(tmp_path, capsys):
    save_macro(tmp_path)
    forecast_macro(tmp_path, MACRO_ID)
    cases.save_outcome(tmp_path, MACRO_ID, Outcome(targets={"DGS10_1w": DGS10_1W}))
    code, out, err, made = run_main(["score", "--root", tmp_path, "--ids", MACRO_ID], capsys)
    assert (code, made) == (0, [])
    assert json.loads(out) == json.loads(json.dumps(loop.score_sealed(tmp_path, [MACRO_ID])))
    out_file = tmp_path / "score.json"
    code, out, err, made = run_main(["score", "--root", tmp_path, "--ids", MACRO_ID, "--out",
                                     out_file], capsys)
    assert (code, out) == (0, "")
    assert json.loads(out_file.read_text(encoding="utf-8"))["n"] == 1


def test_the_score_command_fails_cleanly(tmp_path, capsys):
    save_macro(tmp_path)
    save_earnings(tmp_path)
    code, out, err, made = run_main(["score", "--root", tmp_path, "--ids", ","], capsys)
    assert code == 2
    code, out, err, made = run_main(["score", "--root", tmp_path, "--ids", MACRO_ID], capsys)
    assert code == 1 and MACRO_ID in err
    code, out, err, made = run_main(["score", "--root", tmp_path, "--ids", "nope"], capsys)
    assert code == 1 and "error" in err
    (tmp_path / "acme-q4" / "prediction.json").write_text('{"case_id": "acme-q4"}',
                                                          encoding="utf-8")
    code, out, err, made = run_main(["score", "--root", tmp_path, "--ids", "acme-q4"], capsys)
    assert code == 1 and "error" in err and made == []
