import json

import pytest
from tools.textmask import find_terms

from conftest import VIEW_PROBS, FakeLLM, default_answer, make_case, make_outcome, make_roles

from researchos import cases as cases_mod
from researchos import loop as loop_mod
from researchos.cases import identity_terms, save_case
from researchos.loop import evaluate, score, train
from researchos.perspectives import PERSPECTIVES, Analysis, View
from researchos.playbook import Lesson, Playbook, load_playbook, save_playbook


def setup_root(tmp_path, specs):
    root = tmp_path / "cases"
    for cid, ar in specs:
        save_case(root, make_case(cid), make_outcome(ar=ar))
    return root


def test_score():
    views = {"growth": View("bullish", 0.8, "high", []), "risks": View("bearish", 0.3, "low", [])}
    a = Analysis("c", views, 0.6, 0.4, "")
    s = score(a, make_outcome(ar=0.01))
    assert s == {
        "actual_up": True,
        "brier_weighted": pytest.approx(0.16),
        "brier_judge": pytest.approx(0.36),
        "brier_by_perspective": {"growth": pytest.approx(0.04), "risks": pytest.approx(0.49)},
        "correct_weighted": True,
        "correct_judge": False,
    }
    s0 = score(a, make_outcome(ar=0.0))
    assert s0["actual_up"] is False and s0["brier_judge"] == pytest.approx(0.16)
    assert s0["correct_weighted"] is False and s0["correct_judge"] is True


def test_prediction_is_sealed_before_outcome_is_read(tmp_path, monkeypatch):
    root = setup_root(tmp_path, [("acme-q4", 0.02)])
    seen = []
    real = cases_mod.load_outcome

    def guarded(r, cid):
        pred = root / cid / "prediction.json"
        assert pred.is_file(), "outcome read before the prediction was sealed"
        seen.append(json.loads(pred.read_text(encoding="utf-8")))
        return real(r, cid)

    monkeypatch.setattr(loop_mod, "load_outcome", guarded)
    train(root, ["acme-q4"], tmp_path / "pb.json", make_roles())
    assert len(seen) == 1
    assert seen[0]["case_id"] == "acme-q4" and set(seen[0]["views"]) == set(PERSPECTIVES)


def test_train_updates_and_saves_playbook(tmp_path):
    root = setup_root(tmp_path, [("acme-q3", 0.02), ("acme-q4", -0.01)])
    pb_path = tmp_path / "pb.json"
    bad = {"why_market_moved": "", "where_we_were_wrong": "",
           "lessons": [{"lesson": "Acme always beats.", "applies_when": "x",
                        "perspective": "growth"},
                       {"lesson": "Raised guidance wins.", "applies_when": "guidance up",
                        "perspective": "guidance"}]}
    roles = make_roles(reflector=FakeLLM(responses=[bad]))
    results = train(root, ["acme-q3", "acme-q4"], pb_path, roles)
    assert [r["case_id"] for r in results] == ["acme-q3", "acme-q4"]
    assert results[0]["actual_up"] is True and results[1]["actual_up"] is False
    assert all(r["split"] == "train" and r["lessons_rejected"] == 1 for r in results)

    pb = load_playbook(pb_path)
    assert pb.history == results
    assert [l.text for l in pb.lessons] == ["Raised guidance wins.", "Raised guidance wins."]
    assert [l.case_id for l in pb.lessons] == ["acme-q3", "acme-q4"]
    assert pb.weights != Playbook.initial().weights
    assert sum(pb.weights.values()) == pytest.approx(1.0)
    # the second case was analyzed with the playbook the first case produced
    second_case_prompts = [c["prompt"] for c in roles.analyst.calls[7:]]
    assert all("Raised guidance wins." in p for p in second_case_prompts)
    for cid in ("acme-q3", "acme-q4"):
        assert (root / cid / "masked.json").is_file()
        assert (root / cid / "mask_key.json").is_file()
        assert (root / cid / "prediction.json").is_file()


def test_train_skips_identifiable_case(tmp_path):
    root = setup_root(tmp_path, [("acme-q4", 0.02)])
    hit = {"guesses": [{"company": "Acme Widgets", "ticker": "ACMW", "confidence": 1.0}],
           "clues": ["obvious"]}
    roles = make_roles(guesser=FakeLLM(responses=[hit]))
    pb_path = tmp_path / "pb.json"
    assert train(root, ["acme-q4"], pb_path, roles) == []
    assert not roles.analyst.calls and not roles.reflector.calls
    assert not (root / "acme-q4" / "prediction.json").exists()
    pb = load_playbook(pb_path)
    assert pb.history == [{"case_id": "acme-q4", "split": "train", "skipped": "identifiable"}]
    assert pb.weights == Playbook.initial().weights


def test_masked_json_is_reused(tmp_path):
    root = setup_root(tmp_path, [("acme-q4", 0.02)])
    roles = make_roles()
    train(root, ["acme-q4"], tmp_path / "pb.json", roles)
    n_masker = len(roles.masker.calls)
    before = (root / "acme-q4" / "masked.json").read_text(encoding="utf-8")
    evaluate(root, ["acme-q4"], tmp_path / "pb.json", roles)
    assert len(roles.masker.calls) == n_masker and n_masker >= 1
    assert (root / "acme-q4" / "masked.json").read_text(encoding="utf-8") == before


def test_nothing_identifying_reaches_analyst_or_judge(tmp_path):
    root = setup_root(tmp_path, [("acme-q4", 0.02)])
    pb_path = tmp_path / "pb.json"
    pb = Playbook.initial()
    # a lesson from another case that happens to name this company must not be shown
    pb.lessons.append(Lesson("WidgetPro launches always pop.", "launch", "growth", "other"))
    pb.lessons.append(Lesson("Clean lesson.", "always", "general", "other"))
    save_playbook(pb_path, pb)
    # a stale masked.json that still leaks the name is guarded too
    roles = make_roles()
    train(root, ["acme-q4"], pb_path, roles)
    masked_path = root / "acme-q4" / "masked.json"
    data = json.loads(masked_path.read_text(encoding="utf-8"))
    data["documents"][0]["text"] += " Acme Widgets Inc. is great."
    masked_path.write_text(json.dumps(data), encoding="utf-8")
    roles2 = make_roles()
    evaluate(root, ["acme-q4"], pb_path, roles2)

    terms = identity_terms(make_case("acme-q4"))
    outcome = make_outcome()
    for r in (roles, roles2):
        texts = r.analyst.texts() + r.judge.texts()
        assert texts
        for t in texts:
            assert not find_terms(t, terms), t
            assert "acme-q4" not in t
            assert "jumped" not in t and "news.example.com" not in t
            assert str(outcome.abnormal_return_1d) not in t
        assert any("Clean lesson." in t for t in texts)


def test_evaluate_never_writes_playbook_or_reflects(tmp_path):
    root = setup_root(tmp_path, [("a", 0.02), ("b", -0.03)])
    pb_path = tmp_path / "pb.json"
    pb = Playbook.initial()
    pb.weights = {p: (5.0 if p == "growth" else 1.0) for p in PERSPECTIVES}
    save_playbook(pb_path, pb)
    before = pb_path.read_bytes()
    roles = make_roles()
    result = evaluate(root, ["a", "b"], pb_path, roles)
    assert pb_path.read_bytes() == before
    assert not roles.reflector.calls
    assert (root / "a" / "prediction.json").is_file()

    total = sum(pb.weights.values())
    pw = sum(VIEW_PROBS[p] * w for p, w in pb.weights.items()) / total
    uniform = sum(VIEW_PROBS.values()) / 7
    judge = 0.65
    assert result["n"] == 2 and len(result["cases"]) == 2
    assert result["brier_weighted"] == pytest.approx(((pw - 1) ** 2 + pw ** 2) / 2)
    assert result["brier_judge"] == pytest.approx(((judge - 1) ** 2 + judge ** 2) / 2)
    assert result["brier_uniform"] == pytest.approx(((uniform - 1) ** 2 + uniform ** 2) / 2)
    assert result["brier_coin"] == 0.25
    assert result["accuracy_weighted"] == 0.5 and result["accuracy_judge"] == 0.5


def test_evaluate_with_nothing_scored(tmp_path):
    root = setup_root(tmp_path, [("a", 0.02)])
    hit = {"guesses": [{"company": "Acme", "ticker": "", "confidence": 1.0}], "clues": []}
    pb_path = tmp_path / "pb.json"
    result = evaluate(root, ["a"], pb_path, make_roles(guesser=FakeLLM(responses=[hit])))
    assert result == {"cases": [], "n": 0, "brier_weighted": None, "brier_judge": None,
                      "brier_uniform": None, "brier_coin": None, "accuracy_weighted": None,
                      "accuracy_judge": None}
    assert not pb_path.exists()
    assert evaluate(root, [], pb_path, make_roles())["n"] == 0


def test_same_seed_same_masking(tmp_path):
    r1 = setup_root(tmp_path / "x", [("acme-q4", 0.02)])
    r2 = setup_root(tmp_path / "y", [("acme-q4", 0.02)])
    train(r1, ["acme-q4"], tmp_path / "p1.json", make_roles(), seed=3)
    train(r2, ["acme-q4"], tmp_path / "p2.json", make_roles(), seed=3)
    k1 = (r1 / "acme-q4" / "mask_key.json").read_text(encoding="utf-8")
    k2 = (r2 / "acme-q4" / "mask_key.json").read_text(encoding="utf-8")
    assert k1 == k2
