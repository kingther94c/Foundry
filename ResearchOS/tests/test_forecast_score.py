"""Forecast now, score later (SPEC.md v28): loop.forecast, loop.load_prediction,
loop.score_sealed, train and evaluate refusing a macro case, and the forecast and score
commands."""

import builtins
import io
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pytest

from conftest import (VIEW_PROBS, FakeClient, FakeLLM, make_case, make_outcome, make_roles,
                      schema_client, text_response)

from researchos import cases as cases_mod
from researchos import loop as loop_mod
from researchos import snapshot as snapshot_mod
from researchos.__main__ import main
from researchos.cases import (Document, Outcome, Target, TargetOutcome, macro_case,
                              resolution_manifest, save_case, save_outcome, save_resolution)
from researchos.jsonio import read_json, write_json
from researchos.llm import DEFAULT_MODEL, LLMError
from researchos.loop import evaluate, forecast, load_prediction, score_sealed, train
from researchos.masking import MaskConfig
from researchos.perspectives import PERSPECTIVES, Analysis, View
from researchos.playbook import Lesson, Playbook, save_playbook
from researchos.targets import MACRO_PERSPECTIVES, TargetAnalysis, TargetView, score_targets

REPO = Path(__file__).resolve().parents[1]

# The seven earnings views of conftest with equal weights, worked out by hand:
# (0.8 + 0.7 + 0.6 + 0.55 + 0.5 + 0.3 + 0.2) / 7; the judge always says 0.65.
PW = 3.65 / 7

SNAP = "snap-2026-10-30"
LATER = "snap-2026-11-06"
KEYS = ["DGS10_1w", "DGS10_1m", "SP500_1m"]
CUTOFF = "2026-06-30"

# growth and inflation lean; the other eight macro perspectives say 0.50 to every key.
LEANS = {"growth": {"DGS10_1w": 0.9, "DGS10_1m": 0.6, "SP500_1m": 0.7},
         "inflation": {"DGS10_1w": 0.3, "DGS10_1m": 0.2, "SP500_1m": 0.4}}
JUDGE = {"DGS10_1w": 0.6, "DGS10_1m": 0.35, "SP500_1m": 0.55}
# Equal weights (0.1 each): the mean over the ten perspectives, worked out by hand,
# e.g. DGS10_1w: (0.9 + 0.3 + 8 * 0.5) / 10 = 0.52.
WEIGHTED = {"DGS10_1w": 0.52, "DGS10_1m": 0.48, "SP500_1m": 0.51}

OUTCOME = Outcome(targets={
    "DGS10_1w": TargetOutcome("DGS10", "1w", "bp", "2026-10-30", 4.12, "2026-11-06", 4.17,
                              5.0, True),
    "SP500_1m": TargetOutcome("SP500", "1m", "return", "2026-10-30", 6012.5, "2026-11-30",
                              5900.0, -0.0187, False),
})

HIT = {"guesses": [{"company": "Acme Widgets", "ticker": "ACMW", "confidence": 1.0}],
       "clues": ["obvious"]}

EARNINGS_ENTRY_KEYS = ["case_id", "event_date", "domain", "sealed", "prob_weighted",
                       "prob_judge", "prob_by_perspective", "identifiable"]
MACRO_ENTRY_KEYS = ["case_id", "event_date", "domain", "sealed", "mode", "model", "keys",
                    "prob_weighted", "prob_judge", "prob_by_perspective"]
REPORT_KEYS = ["cases", "n", "pending", "brier_weighted", "brier_judge", "brier_uniform",
               "brier_coin", "accuracy_weighted", "accuracy_judge"]


def make_macro(case_id=SNAP, as_of="2026-10-30"):
    targets = [Target(id="DGS10", name="10-year Treasury yield", kind="bp",
                      horizons=["1w", "1m"], as_of_date=as_of, as_of_value=4.12,
                      trailing={"1w": -3.0}),
               Target(id="SP500", name="S&P 500", kind="return", horizons=["1m"],
                      as_of_date=as_of, as_of_value=6012.5)]
    documents = [Document("markets", "Markets at the close", "Yields rose 5bp on the week.")]
    return macro_case(case_id, as_of, targets, documents)


def macro_root(tmp_path, *cases):
    root = tmp_path / "cases"
    for case in cases or (make_macro(),):
        save_case(root, case)  # a snapshot has no outcome when it is built
    return root


def earnings_root(tmp_path, specs):
    """specs: (case_id, abnormal return), None for an outcome not written yet."""
    root = tmp_path / "cases"
    for case_id, ar in specs:
        save_case(root, make_case(case_id), None if ar is None else make_outcome(ar=ar))
    return root


def _forecasts(probs):
    return {"DGS10": {"prob_up_1w": probs["DGS10_1w"], "prob_up_1m": probs["DGS10_1m"]},
            "SP500": {"prob_up_1m": probs["SP500_1m"]}}


def macro_probs(name):
    return dict(LEANS.get(name, dict.fromkeys(KEYS, 0.5)))


def macro_analyst(system, prompt, schema, prefix):
    name = prompt.splitlines()[0].split(": ", 1)[1]
    return {"forecasts": _forecasts(macro_probs(name)), "key_points": [f"{name} point"]}


def macro_roles():
    def judge(system, prompt, schema, prefix):
        return {"forecasts": _forecasts(JUDGE), "thesis": "Rates drift up."}

    return make_roles(analyst=FakeLLM(macro_analyst), judge=FakeLLM(judge))


def no_client():
    raise AssertionError("no client may be built")


@pytest.fixture
def opened(monkeypatch):
    """The names of the files opened (through open() or pathlib) while the test runs."""
    names = []
    real = io.open

    def spy(file, *args, **kwargs):
        if isinstance(file, (str, Path)):
            names.append(Path(file).name)
        return real(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)
    return names


# ---- where PREDICTION_FILE lives, and Playbook.initial ---------------------------------

@pytest.mark.parametrize("module", ["researchos.loop", "researchos.snapshot",
                                    "researchos.targets", "researchos.__main__"])
def test_each_module_imports_first_on_its_own(module):
    import tools  # installed, or ../tools on pytest's pythonpath in a clone

    paths = [str(REPO), str(Path(tools.__file__).resolve().parents[1]),
             os.environ.get("PYTHONPATH", "")]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in paths if p))
    done = subprocess.run([sys.executable, "-c", f"import {module}"], cwd=REPO, env=env,
                          capture_output=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr


def test_prediction_file_lives_in_cases():
    assert cases_mod.PREDICTION_FILE == "prediction.json"
    assert loop_mod.PREDICTION_FILE == "prediction.json"
    assert snapshot_mod.PREDICTION_FILE == "prediction.json"


def test_playbook_initial_takes_the_perspectives():
    assert Playbook.initial() == Playbook(weights={p: 1.0 / 7 for p in PERSPECTIVES})
    pb = Playbook.initial(MACRO_PERSPECTIVES)
    assert list(pb.weights) == MACRO_PERSPECTIVES
    assert all(w == pytest.approx(0.1) for w in pb.weights.values())
    assert pb.lessons == [] and pb.history == []
    assert list(Playbook.initial(("b", "a")).weights.items()) == [("b", 0.5), ("a", 0.5)]
    assert Playbook.initial(None) == Playbook.initial()


# ---- evaluate refuses a macro case; train one that shares a run with an earnings case ---

@pytest.mark.parametrize("run", [train, evaluate])
def test_train_and_evaluate_refuse_a_macro_case(tmp_path, run):
    root = macro_root(tmp_path)
    save_case(root, make_case("a"), make_outcome(ar=0.02))
    roles = make_roles()
    pb_path = tmp_path / "pb.json"
    # evaluate refuses the macro case itself (v28). train takes a macro run (v30), but a
    # macro case cannot share a run with an earnings case (single_domain).
    refused = SNAP if run is evaluate else "more than one domain"
    with pytest.raises(ValueError, match=refused) as err:
        run(root, ["a", SNAP], pb_path, roles, knowledge_cutoff=CUTOFF)
    assert "macro" in str(err.value) and "case a " not in str(err.value)
    assert not any(agent.calls for agent in (roles.masker, roles.guesser, roles.analyst,
                                             roles.judge, roles.reflector))
    assert not pb_path.exists()
    assert not (root / "a" / "masked.json").exists()
    assert not (root / "a" / "prediction.json").exists()
    assert not (root / SNAP / "prediction.json").exists()


# ---- forecast: earnings cases ------------------------------------------------------------

def test_forecast_seals_earnings_cases_and_opens_no_outcome(tmp_path, opened):
    root = earnings_root(tmp_path, [("a", 0.02), ("b", -0.03)])
    (root / "a" / "resolution.json").write_text("{}", encoding="utf-8")
    pb_path = tmp_path / "pb.json"
    roles = make_roles()
    opened.clear()
    entries = forecast(root, ["a", "b"], pb_path, roles)
    expected = {"case_id": "a", "event_date": "2026-02-01", "domain": "earnings",
                "sealed": True, "prob_weighted": pytest.approx(PW), "prob_judge": 0.65,
                "prob_by_perspective": VIEW_PROBS, "identifiable": False}
    assert entries == [expected, {**expected, "case_id": "b"}]
    assert [list(entry) for entry in entries] == [EARNINGS_ENTRY_KEYS] * 2
    assert "outcome.json" not in opened and "resolution.json" not in opened
    assert not pb_path.exists()  # the playbook is never written
    assert not roles.reflector.calls
    assert len(roles.analyst.calls) == 2 * len(PERSPECTIVES) and len(roles.judge.calls) == 2
    for cid in ("a", "b"):
        assert (root / cid / "masked.json").is_file()
        sealed = load_prediction(root, cid)
        assert isinstance(sealed, Analysis) and sealed.case_id == cid
        assert sealed.prob_judge == 0.65 and sealed.prob_weighted == pytest.approx(PW)


def test_forecast_skips_an_identifiable_case_unless_it_is_after_the_cutoff(tmp_path):
    root = earnings_root(tmp_path, [("a", None)])
    pb_path = tmp_path / "pb.json"
    roles = make_roles(guesser=FakeLLM(responses=[HIT]))
    entries = forecast(root, ["a"], pb_path, roles)
    assert entries == [{"case_id": "a", "event_date": "2026-02-01", "domain": "earnings",
                        "sealed": False, "skipped": "identifiable"}]
    assert list(entries[0]) == ["case_id", "event_date", "domain", "sealed", "skipped"]
    assert not roles.analyst.calls and not (root / "a" / "prediction.json").exists()
    # After the knowledge cutoff it is analyzed; the saved masked.json is reused.
    roles = make_roles(guesser=FakeLLM(responses=[HIT]))
    entries = forecast(root, ["a"], pb_path, roles, knowledge_cutoff="2026-01-31")
    assert entries[0]["sealed"] is True and entries[0]["identifiable"] is True
    assert not roles.masker.calls and not roles.guesser.calls
    assert (root / "a" / "prediction.json").is_file() and not pb_path.exists()


def test_forecast_reads_the_playbook_and_never_changes_it(tmp_path):
    root = earnings_root(tmp_path, [("a", 0.02)])
    pb_path = tmp_path / "pb.json"
    pb = Playbook.initial()
    pb.weights = {p: (5.0 if p == "growth" else 1.0) for p in PERSPECTIVES}
    pb.lessons.append(Lesson("WidgetPro launches always pop.", "launch", "growth", "other"))
    pb.lessons.append(Lesson("Clean lesson.", "always", "general", "other"))
    save_playbook(pb_path, pb)
    before = pb_path.read_bytes()
    roles = make_roles()
    entries = forecast(root, ["a"], pb_path, roles, perspective_mode="isolated")
    assert pb_path.read_bytes() == before
    prompts = [call["prompt"] for call in roles.analyst.calls]
    assert prompts and all("Clean lesson." in p and "WidgetPro" not in p for p in prompts)
    assert all(p.splitlines()[1].startswith("Your factor: ") for p in prompts)
    sealed = read_json(root / "a" / "prediction.json")
    assert entries[0]["prob_weighted"] == sealed["prob_weighted"]


# ---- forecast: macro cases ---------------------------------------------------------------

def test_forecast_seals_a_macro_case_as_it_is(tmp_path, opened):
    root = macro_root(tmp_path)
    (root / SNAP / "outcome.json").write_text("{}", encoding="utf-8")
    (root / SNAP / "resolution.json").write_text("{}", encoding="utf-8")
    pb_path = tmp_path / "pb.json"
    roles = macro_roles()
    opened.clear()
    entries = forecast(root, [SNAP], pb_path, roles, knowledge_cutoff=CUTOFF)
    assert entries == [{"case_id": SNAP, "event_date": "2026-10-30", "domain": "macro",
                        "sealed": True, "mode": "holistic", "model": "", "keys": KEYS,
                        "prob_weighted": pytest.approx(WEIGHTED), "prob_judge": JUDGE,
                        "prob_by_perspective": {name: macro_probs(name)
                                                for name in MACRO_PERSPECTIVES}}]
    assert list(entries[0]) == MACRO_ENTRY_KEYS
    assert list(entries[0]["prob_by_perspective"]) == MACRO_PERSPECTIVES
    for name in ("outcome.json", "resolution.json", "masked.json"):
        assert name not in opened
    assert not (root / SNAP / "masked.json").exists()
    assert not roles.masker.calls and not roles.guesser.calls and not roles.reflector.calls
    assert len(roles.analyst.calls) == 10 and len(roles.judge.calls) == 1
    assert not pb_path.exists()
    # The initial playbook of the macro perspectives; the documents exactly as they are.
    assert "- monetary_policy: 0.100" in roles.analyst.calls[0]["prompt"]
    assert "Yields rose 5bp on the week." in roles.analyst.calls[0]["prefix"]
    sealed = read_json(root / SNAP / "prediction.json")
    assert sealed["knowledge_cutoff"] == "2026-06-30" and sealed["model"] == ""
    assert sealed["mode"] == "holistic" and sealed["perspectives"] == MACRO_PERSPECTIVES
    assert sealed["keys"] == KEYS and sealed["thesis"] == "Rates drift up."
    analysis = load_prediction(root, SNAP)
    assert isinstance(analysis, TargetAnalysis) and asdict(analysis) == sealed


@pytest.mark.parametrize("model, recorded", [("claude-opus-5-5", "claude-opus-5-5"),
                                             (5, ""), (None, "")])
def test_forecast_records_the_analysts_model_and_the_cutoff(tmp_path, model, recorded):
    root = macro_root(tmp_path)
    roles = macro_roles()
    if model is not None:
        roles.analyst.model = model
    entries = forecast(root, [SNAP], tmp_path / "pb.json", roles,
                       knowledge_cutoff=date(2026, 6, 30), perspective_mode="isolated")
    sealed = read_json(root / SNAP / "prediction.json")
    assert sealed["model"] == recorded and entries[0]["model"] == recorded
    assert sealed["knowledge_cutoff"] == "2026-06-30"
    assert sealed["mode"] == "isolated" and entries[0]["mode"] == "isolated"


# ---- forecast: the checks, in order -----------------------------------------------------

def test_forecast_with_no_case_id_reads_nothing(tmp_path, opened):
    bad = tmp_path / "pb.json"
    bad.write_text("not json", encoding="utf-8")
    opened.clear()
    assert forecast(tmp_path / "nowhere", [], bad, make_roles()) == []
    assert opened == []
    # step 1 still comes first
    with pytest.raises(ValueError):
        forecast(tmp_path / "nowhere", [], bad, make_roles(), perspective_mode="both")
    with pytest.raises(ValueError):
        forecast(tmp_path / "nowhere", [], bad, make_roles(), knowledge_cutoff="soon")


def test_forecast_refuses_a_repeated_id_before_loading_any_case(tmp_path):
    with pytest.raises(ValueError, match="zzz"):
        forecast(tmp_path / "cases", ["zzz", "zzz"], tmp_path / "pb.json", make_roles())


def test_forecast_lets_the_error_of_load_case_through(tmp_path):
    root = earnings_root(tmp_path, [("a", 0.02)])
    roles = make_roles()
    with pytest.raises(FileNotFoundError):
        forecast(root, ["a", "zzz"], tmp_path / "pb.json", roles)
    assert not roles.masker.calls and not (root / "a" / "masked.json").exists()


def test_forecast_refuses_cases_of_two_domains(tmp_path):
    root = macro_root(tmp_path)
    save_case(root, make_case("a"))
    roles = macro_roles()
    with pytest.raises(ValueError, match="more than one domain"):
        forecast(root, ["a", SNAP], tmp_path / "pb.json", roles, knowledge_cutoff=CUTOFF)
    assert not roles.masker.calls and not roles.analyst.calls


def test_forecast_refuses_a_playbook_of_another_domain(tmp_path):
    root = macro_root(tmp_path)
    pb_path = tmp_path / "pb.json"
    save_playbook(pb_path, Playbook.initial())  # the earnings perspectives
    roles = macro_roles()
    with pytest.raises(ValueError) as err:
        forecast(root, [SNAP], pb_path, roles, knowledge_cutoff=CUTOFF)
    assert str(pb_path) in str(err.value) and "macro" in str(err.value)
    assert not roles.analyst.calls and not (root / SNAP / "prediction.json").exists()
    earnings = earnings_root(tmp_path / "e", [("a", 0.02)])
    save_playbook(pb_path, Playbook.initial(MACRO_PERSPECTIVES))
    with pytest.raises(ValueError, match="earnings"):
        forecast(earnings, ["a"], pb_path, make_roles())
    # The domain's perspectives in another order are the same set: the playbook is used.
    save_playbook(pb_path, Playbook.initial(list(reversed(MACRO_PERSPECTIVES))))
    before = pb_path.read_bytes()
    entries = forecast(root, [SNAP], pb_path, macro_roles(), knowledge_cutoff=CUTOFF)
    assert entries[0]["sealed"] is True and pb_path.read_bytes() == before


def test_forecast_refuses_a_look_ahead_before_checking_the_cutoff(tmp_path):
    root = macro_root(tmp_path)
    pb_path = tmp_path / "pb.json"
    pb = Playbook.initial(MACRO_PERSPECTIVES)
    pb.history.append({"case_id": "old", "event_date": "2026-11-15"})
    save_playbook(pb_path, pb)
    with pytest.raises(ValueError, match="^forecast refused: ") as err:
        forecast(root, [SNAP], pb_path, macro_roles())  # no cutoff either
    assert SNAP in str(err.value) and "2026-11-15" in str(err.value)


def test_forecast_names_every_case_whose_date_is_refused(tmp_path):
    root = earnings_root(tmp_path, [("a", 0.02), ("b", 0.02)])
    undated = make_case("u")
    undated.event_date = "soon"
    save_case(root, undated)
    pb_path = tmp_path / "pb.json"
    pb = Playbook.initial()
    pb.history.append({"case_id": "old", "event_date": "2026-03-01"})
    save_playbook(pb_path, pb)
    roles = make_roles()
    with pytest.raises(ValueError, match="^forecast refused: ") as err:
        forecast(root, ["a", "u", "b"], pb_path, roles)
    message = str(err.value)
    assert "case a:" in message and "case u:" in message and "case b:" in message
    assert not roles.masker.calls


@pytest.mark.parametrize("cutoff", [None, "2026-10-30", "2026-12-31"])
def test_forecast_refuses_a_macro_case_not_after_the_cutoff(tmp_path, cutoff):
    root = macro_root(tmp_path)
    roles = macro_roles()
    with pytest.raises(ValueError, match=SNAP):
        forecast(root, [SNAP], tmp_path / "pb.json", roles, knowledge_cutoff=cutoff)
    assert not roles.analyst.calls and not (root / SNAP / "prediction.json").exists()


def test_forecast_never_replaces_a_sealed_forecast(tmp_path):
    root = macro_root(tmp_path, make_macro(), make_macro(LATER, "2026-11-06"))
    pb_path = tmp_path / "pb.json"
    forecast(root, [SNAP], pb_path, macro_roles(), knowledge_cutoff=CUTOFF)
    before = (root / SNAP / "prediction.json").read_bytes()
    roles = macro_roles()
    with pytest.raises(ValueError, match=SNAP) as err:
        forecast(root, [LATER, SNAP], pb_path, roles, knowledge_cutoff=CUTOFF)
    assert "already sealed" in str(err.value) and LATER not in str(err.value)
    assert not roles.analyst.calls  # the case not sealed yet is not forecast either
    assert (root / SNAP / "prediction.json").read_bytes() == before
    assert not (root / LATER / "prediction.json").exists()
    # without a cutoff, the cutoff check (step 7) comes first
    with pytest.raises(ValueError, match="no knowledge cutoff"):
        forecast(root, [SNAP], pb_path, roles)
    # an earnings case too
    earnings = earnings_root(tmp_path / "e", [("a", 0.02)])
    forecast(earnings, ["a"], pb_path, make_roles())
    with pytest.raises(ValueError, match="case a: .*already sealed"):
        forecast(earnings, ["a"], pb_path, make_roles())


def test_a_failure_part_way_leaves_the_forecasts_sealed_before_it(tmp_path):
    root = macro_root(tmp_path, make_macro(), make_macro(LATER, "2026-11-06"))
    pb_path = tmp_path / "pb.json"

    def failing(system, prompt, schema, prefix):
        if "as of 2026-11-06" in prefix:
            raise LLMError("model refused the request")
        return macro_analyst(system, prompt, schema, prefix)

    roles = macro_roles()
    roles.analyst = FakeLLM(failing)
    with pytest.raises(LLMError):
        forecast(root, [SNAP, LATER], pb_path, roles, knowledge_cutoff=CUTOFF)
    assert (root / SNAP / "prediction.json").is_file()
    assert not (root / LATER / "prediction.json").exists()
    with pytest.raises(ValueError, match=SNAP):
        forecast(root, [SNAP, LATER], pb_path, macro_roles(), knowledge_cutoff=CUTOFF)
    entries = forecast(root, [LATER], pb_path, macro_roles(), knowledge_cutoff=CUTOFF)
    assert [(e["case_id"], e["event_date"]) for e in entries] == [(LATER, "2026-11-06")]


# ---- load_prediction ----------------------------------------------------------------------

def test_load_prediction_reads_an_earnings_forecast(tmp_path):
    (tmp_path / "a").mkdir()
    path = tmp_path / "a" / "prediction.json"
    views = {"risks": View("bearish", 0.3, "low", ["a risk"]),
             "growth": View("bullish", 0.8, "high", [])}
    analysis = Analysis("a", views, 0.6, 0.4, "the thesis")
    write_json(path, asdict(analysis))
    loaded = load_prediction(tmp_path, "a")
    assert loaded == analysis and list(loaded.views) == ["risks", "growth"]
    for drop in (("thesis",), ("views", "growth", "conviction"), ("case_id",)):
        data = asdict(analysis)
        inner = data
        for key in drop[:-1]:
            inner = inner[key]
        del inner[drop[-1]]
        write_json(path, data)
        with pytest.raises(KeyError):
            load_prediction(tmp_path, "a")
    write_json(path, [asdict(analysis)])
    with pytest.raises(ValueError):
        load_prediction(tmp_path, "a")
    with pytest.raises(FileNotFoundError):
        load_prediction(tmp_path, "b")


def test_load_prediction_reads_a_macro_forecast(tmp_path):
    (tmp_path / SNAP).mkdir()
    analysis = TargetAnalysis(case_id=SNAP, mode="isolated", perspectives=["labor", "growth"],
                              keys=["DGS10_1w"],
                              views={"labor": TargetView({"DGS10_1w": 0.4}, []),
                                     "growth": TargetView({"DGS10_1w": 0.6}, ["up"])},
                              prob_weighted={"DGS10_1w": 0.5}, prob_judge={"DGS10_1w": 0.55},
                              thesis="t", model="m", knowledge_cutoff=CUTOFF)
    write_json(tmp_path / SNAP / "prediction.json", asdict(analysis))
    loaded = load_prediction(tmp_path, SNAP)
    assert isinstance(loaded, TargetAnalysis) and loaded == analysis


# ---- score_sealed -------------------------------------------------------------------------

def test_score_sealed_scores_earnings_cases_and_lists_the_pending(tmp_path):
    root = earnings_root(tmp_path, [("a", 0.02), ("b", -0.03), ("c", None)])
    forecast(root, ["a", "b", "c"], tmp_path / "pb.json", make_roles())
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    report = score_sealed(root, ["a", "b", "c"])
    assert list(report) == REPORT_KEYS
    assert report["n"] == 2 and report["pending"] == ["c"]
    first, second = report["cases"]
    assert list(first) == ["case_id", "event_date", "actual_up", "brier_weighted",
                           "brier_judge", "brier_by_perspective", "prob_by_perspective",
                           "correct_weighted", "correct_judge", "brier_uniform"]
    assert first["case_id"] == "a" and first["event_date"] == "2026-02-01"
    assert first["actual_up"] is True
    assert first["brier_weighted"] == pytest.approx((PW - 1) ** 2)
    assert first["brier_judge"] == pytest.approx(0.1225)  # (0.65 - 1) ** 2
    assert first["brier_uniform"] == pytest.approx((PW - 1) ** 2)
    assert first["brier_by_perspective"]["growth"] == pytest.approx(0.04)
    assert first["prob_by_perspective"] == VIEW_PROBS
    assert first["correct_weighted"] is True and first["correct_judge"] is True
    assert second["case_id"] == "b" and second["actual_up"] is False
    assert second["brier_judge"] == pytest.approx(0.4225)  # 0.65 ** 2
    assert second["correct_weighted"] is False and second["correct_judge"] is False
    assert report["brier_weighted"] == pytest.approx(((PW - 1) ** 2 + PW ** 2) / 2)
    assert report["brier_judge"] == pytest.approx(0.2725)
    assert report["brier_uniform"] == pytest.approx(((PW - 1) ** 2 + PW ** 2) / 2)
    assert report["brier_coin"] == 0.25
    assert report["accuracy_weighted"] == 0.5 and report["accuracy_judge"] == 0.5
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_score_sealed_with_nothing_resolved(tmp_path):
    root = macro_root(tmp_path)
    forecast(root, [SNAP], tmp_path / "pb.json", macro_roles(), knowledge_cutoff=CUTOFF)
    assert score_sealed(root, [SNAP]) == {
        "cases": [], "n": 0, "pending": [f"{SNAP}/{key}" for key in KEYS],
        "brier_weighted": None, "brier_judge": None, "brier_uniform": None, "brier_coin": None,
        "accuracy_weighted": None, "accuracy_judge": None}
    assert score_sealed(root, []) == {
        "cases": [], "n": 0, "pending": [], "brier_weighted": None, "brier_judge": None,
        "brier_uniform": None, "brier_coin": None, "accuracy_weighted": None,
        "accuracy_judge": None}


def test_score_sealed_scores_each_resolved_target_key(tmp_path):
    root = macro_root(tmp_path)
    forecast(root, [SNAP], tmp_path / "pb.json", macro_roles(), knowledge_cutoff=CUTOFF)
    save_outcome(root, SNAP, OUTCOME)
    report = score_sealed(root, [SNAP])
    assert list(report) == REPORT_KEYS
    assert report["n"] == 2 and report["pending"] == [f"{SNAP}/DGS10_1m"]
    first, second = report["cases"]
    assert first["case_id"] == f"{SNAP}/DGS10_1w" and second["case_id"] == f"{SNAP}/SP500_1m"
    assert first["case"] == SNAP and first["resolution_date"] == "2026-11-06"
    assert first["prob_weighted"] == pytest.approx(0.52) and first["prob_judge"] == 0.6
    assert first["brier_weighted"] == pytest.approx(0.2304)  # (0.52 - 1) ** 2
    assert first["brier_judge"] == pytest.approx(0.16)
    assert first["brier_uniform"] == pytest.approx(0.2304)
    assert first["correct_weighted"] is True and first["correct_judge"] is True
    assert second["brier_weighted"] == pytest.approx(0.2601)  # 0.51 ** 2
    assert second["brier_judge"] == pytest.approx(0.3025)  # 0.55 ** 2
    assert second["correct_weighted"] is False and second["correct_judge"] is False
    assert report["brier_weighted"] == pytest.approx(0.24525)
    assert report["brier_judge"] == pytest.approx(0.23125)
    assert report["brier_uniform"] == pytest.approx(0.24525)
    assert report["brier_coin"] == 0.25
    assert report["accuracy_weighted"] == 0.5 and report["accuracy_judge"] == 0.5
    assert report["cases"] == score_targets(load_prediction(root, SNAP),
                                            cases_mod.load_outcome(root, SNAP),
                                            cases_mod.load_case(root, SNAP))


def test_score_sealed_checks_every_case_first(tmp_path):
    root = earnings_root(tmp_path, [("a", 0.02), ("b", -0.03)])
    forecast(root, ["a"], tmp_path / "pb.json", make_roles())
    with pytest.raises(ValueError, match="case a is given more than once"):
        score_sealed(root, ["a", "a"])
    with pytest.raises(ValueError):  # before load_case of an id that has no case
        score_sealed(root, ["zzz", "zzz"])
    with pytest.raises(FileNotFoundError):
        score_sealed(root, ["a", "zzz"])
    with pytest.raises(ValueError, match="case b has no prediction.json"):
        score_sealed(root, ["a", "b"])
    # every case is checked for a sealed forecast before any forecast is read
    data = read_json(root / "a" / "prediction.json")
    del data["thesis"]
    write_json(root / "a" / "prediction.json", data)
    with pytest.raises(ValueError, match="case b has no prediction.json"):
        score_sealed(root, ["a", "b"])
    with pytest.raises(KeyError):
        score_sealed(root, ["a"])
    # single_domain comes before the sealed-forecast check
    save_case(root, make_macro())
    with pytest.raises(ValueError, match="more than one domain"):
        score_sealed(root, ["b", SNAP])


def test_score_sealed_refuses_a_forecast_of_another_case_or_kind(tmp_path):
    root = earnings_root(tmp_path, [("a", 0.02), ("b", -0.03)])
    forecast(root, ["a"], tmp_path / "pb.json", make_roles())
    (root / "b" / "prediction.json").write_bytes((root / "a" / "prediction.json").read_bytes())
    with pytest.raises(ValueError) as err:
        score_sealed(root, ["b"])
    assert "'a'" in str(err.value) and "'b'" in str(err.value)
    other = TargetAnalysis(case_id="b", mode="holistic", perspectives=["growth"],
                           keys=["DGS10_1w"], views={"growth": TargetView({"DGS10_1w": 0.6}, [])},
                           prob_weighted={"DGS10_1w": 0.6}, prob_judge={"DGS10_1w": 0.6},
                           thesis="")
    write_json(root / "b" / "prediction.json", asdict(other))
    with pytest.raises(ValueError, match="case b is an earnings case"):
        score_sealed(root, ["a", "b"])
    macro = macro_root(tmp_path / "m")
    write_json(macro / SNAP / "prediction.json",
               asdict(Analysis(SNAP, {"growth": View("bullish", 0.6, "low", [])}, 0.6, 0.6, "")))
    with pytest.raises(ValueError, match=f"case {SNAP} is a macro case"):
        score_sealed(macro, [SNAP])


# ---- the forecast and score commands -------------------------------------------------------

def macro_responder(kwargs):
    props = kwargs["output_config"]["format"]["schema"]["properties"]
    forecasts = {target: {name: 0.5 for name in schema["properties"]}
                 for target, schema in props["forecasts"]["properties"].items()}
    extra = {"thesis": "Flat."} if "thesis" in props else {"key_points": []}
    return text_response(json.dumps({"forecasts": forecasts, **extra}))


def test_cli_forecast_then_score_earnings_cases(tmp_path, capsys):
    root = earnings_root(tmp_path, [("a", 0.02), ("b", -0.02)])
    pb = tmp_path / "pb.json"
    args = ["forecast", "--root", str(root), "--playbook", str(pb), "--ids", "a,b"]
    code = main(args, client_factory=schema_client)
    captured = capsys.readouterr()
    assert code == 0, captured.err
    result = json.loads(captured.out)
    assert list(result) == ["cases"]
    assert [(e["case_id"], e["domain"], e["sealed"]) for e in result["cases"]] == [
        ("a", "earnings", True), ("b", "earnings", True)]
    assert not pb.exists() and (root / "a" / "prediction.json").is_file()
    # sealed once: a second run exits 1 with the reason on stderr
    code = main(args, client_factory=schema_client)
    captured = capsys.readouterr()
    assert code == 1 and "case a" in captured.err and captured.out == ""

    code = main(["score", "--root", str(root), "--ids", "a, b"], client_factory=no_client)
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert json.loads(captured.out) == score_sealed(root, ["a", "b"])
    out = tmp_path / "score.json"
    code = main(["score", "--root", str(root), "--ids", "a", "--out", str(out)],
                client_factory=no_client)
    assert code == 0 and capsys.readouterr().out == ""
    assert read_json(out)["n"] == 1


def test_cli_forecast_exits_as_train_does(tmp_path, capsys):
    root = earnings_root(tmp_path, [("a", 0.02)])
    base = ["forecast", "--root", str(root), "--playbook", str(tmp_path / "pb.json"), "--ids"]
    assert main(base + [" , "], client_factory=no_client) == 2
    capsys.readouterr()
    code = main(base + ["a,zzz"], client_factory=no_client)  # before any client is built
    captured = capsys.readouterr()
    assert code == 1 and "zzz" in captured.err and captured.out == ""
    client = FakeClient(lambda kw: text_response("{}", stop_reason="refusal"))
    code = main(base + ["a"], client_factory=lambda: client)
    captured = capsys.readouterr()
    assert code == 1 and "refused" in captured.err and captured.out == ""
    code = main(base + ["a,a"], client_factory=schema_client)
    captured = capsys.readouterr()
    assert code == 1 and "more than once" in captured.err and captured.out == ""


def test_cli_forecast_passes_on_only_the_options_given(tmp_path, capsys, monkeypatch):
    import researchos.__main__ as main_mod

    root = earnings_root(tmp_path, [("a", 0.02)])
    seen = []

    def fake_forecast(root_, ids, playbook, roles, **kwargs):
        seen.append((ids, kwargs))
        return [{"case_id": "a"}]

    monkeypatch.setattr(main_mod, "forecast", fake_forecast)
    base = ["forecast", "--root", str(root), "--playbook", str(tmp_path / "pb.json"),
            "--ids", "a"]
    assert main(base, client_factory=schema_client) == 0
    assert json.loads(capsys.readouterr().out) == {"cases": [{"case_id": "a"}]}
    assert main(base + ["--seed", "3", "--knowledge-cutoff", "2026-01-31",
                        "--mask-history-years", "2", "--perspective-mode", "isolated"],
                client_factory=schema_client) == 0
    capsys.readouterr()
    assert seen[0] == (["a"], {"seed": 0, "knowledge_cutoff": None})
    assert seen[1] == (["a"], {"seed": 3, "knowledge_cutoff": "2026-01-31",
                               "mask_config": MaskConfig(give_up_confidence=None,
                                                         abstract=False, history_years=2),
                               "perspective_mode": "isolated"})


def test_cli_forecast_and_score_a_macro_case(tmp_path, capsys):
    root = macro_root(tmp_path)
    pb = str(tmp_path / "pb.json")
    base = ["forecast", "--root", str(root), "--playbook", pb, "--ids", SNAP]
    code = main(base, client_factory=lambda: FakeClient(macro_responder))
    captured = capsys.readouterr()
    assert code == 1 and SNAP in captured.err  # no knowledge cutoff
    code = main(base + ["--knowledge-cutoff", CUTOFF],
                client_factory=lambda: FakeClient(macro_responder))
    captured = capsys.readouterr()
    assert code == 0, captured.err
    entry = json.loads(captured.out)["cases"][0]
    assert entry["domain"] == "macro" and entry["keys"] == KEYS
    assert entry["model"] == DEFAULT_MODEL
    assert read_json(root / SNAP / "prediction.json")["knowledge_cutoff"] == CUTOFF

    save_outcome(root, SNAP, OUTCOME)
    code = main(["score", "--root", str(root), "--ids", SNAP], client_factory=no_client)
    captured = capsys.readouterr()
    assert code == 0, captured.err
    report = json.loads(captured.out)
    assert report["n"] == 2 and report["pending"] == [f"{SNAP}/DGS10_1m"]
    code = main(["evaluate", "--root", str(root), "--playbook", pb, "--ids", SNAP,
                 "--knowledge-cutoff", CUTOFF],
                client_factory=lambda: FakeClient(macro_responder))
    captured = capsys.readouterr()
    assert code == 1 and "macro case" in captured.err and captured.out == ""
    # train takes the macro id (v30), but learns only once every key has resolved, and it
    # never replaces the forecast sealed above
    train_args = ["train", "--root", str(root), "--playbook", pb, "--ids", SNAP,
                  "--knowledge-cutoff", CUTOFF]
    save_resolution(root, SNAP, resolution_manifest(make_macro(),
                                                    resolved=["DGS10_1w", "SP500_1m"]))
    code = main(train_args, client_factory=lambda: FakeClient(macro_responder))
    captured = capsys.readouterr()
    assert code == 1 and SNAP in captured.err and "DGS10_1m" in captured.err
    assert "macro case" not in captured.err and captured.out == ""
    save_resolution(root, SNAP, resolution_manifest(make_macro(), resolved=KEYS))
    code = main(train_args, client_factory=lambda: FakeClient(macro_responder))
    captured = capsys.readouterr()
    assert code == 1 and SNAP in captured.err and "sealed" in captured.err
    assert captured.out == "" and not Path(pb).exists()


def test_cli_score_exit_codes(tmp_path, capsys):
    root = earnings_root(tmp_path, [("a", 0.02), ("b", 0.02)])
    assert main(["score", "--root", str(root), "--ids", " , "], client_factory=no_client) == 2
    assert "--ids" in capsys.readouterr().err
    assert main(["score", "--root", str(root)], client_factory=no_client) == 2
    capsys.readouterr()
    (root / "b" / "prediction.json").write_text('{"case_id": "b"}', encoding="utf-8")
    for ids, reason in (("zzz", "zzz"), ("a", "never forecast"), ("b", "views")):
        code = main(["score", "--root", str(root), "--ids", ids], client_factory=no_client)
        captured = capsys.readouterr()
        assert code == 1 and captured.out == ""
        assert captured.err.startswith("error:") and reason in captured.err
