"""Perspectives built to disagree (SPEC.md v20): the isolated mode, pool rule and CLI."""

import json
import statistics

import pytest
from tools.stats import combine_probabilities, pool_log_odds

from conftest import VIEW_PROBS, FakeLLM, default_answer, make_case, make_outcome, make_roles
from conftest import schema_client

from researchos.__main__ import main
from researchos.blend import agreement, replay
from researchos.cases import Document, save_case
from researchos.loop import evaluate, train
from researchos.masking import MaskedCase
from researchos.perspectives import (ANALYST_SYSTEM, ISOLATED_ANALYST_SYSTEM,
                                     ISOLATED_JUDGE_SYSTEM, JUDGE_SCHEMA, JUDGE_SYSTEM, MODES,
                                     PERSPECTIVE_FOCUS, PERSPECTIVES, VIEW_SCHEMA, analyze)
from researchos.playbook import Lesson, Playbook, render_for_prompt

SPEC_ANALYST = (
    "You are an equity analyst judging how a stock will react to a quarterly earnings report. "
    "The company's identity is hidden: names are replaced, money amounts are multiplied by a "
    "secret factor (absolute sizes are meaningless; ratios, growth rates and margins are "
    "intact) and years are shifted. Do not try to identify the company; reason only from the "
    "documents. You judge one factor in isolation. Assume that everything in the report "
    "outside your factor came in exactly as the market expected, and estimate the probability "
    "that the stock beats the market (positive abnormal return, stock minus market) on the "
    "first trading session that can react to the report, from your factor alone. 0.50 means "
    "your factor gives no signal either way; move away from 0.50 only as far as this factor "
    "by itself would move the stock. Do not give an overall view of the report: another "
    "analyst combines the factors.")

SPEC_JUDGE = (
    "You are the chief analyst. Several analysts have each judged one factor of a quarterly "
    "earnings report in isolation, each as if everything else in the report had come in as "
    "the market expected. The company's identity is hidden (names replaced, money scaled, "
    "years shifted). Their probabilities are evidence from different factors: they add up "
    "rather than average, and a view at 0.50 is a factor that gives no signal. Weigh them "
    "against the documents and the playbook, and give your own probability that the stock "
    "beats the market (positive abnormal return) on the first trading session that can react, "
    "with a short thesis.")

SPEC_PARAGRAPH = (
    "Judge only this factor, as if everything else in the report had come in exactly as the "
    "market expected. Give your stance on this factor, the probability that the stock beats "
    "the market on the first session that can react from this factor alone (0.50 if it gives "
    "no signal), your conviction, and the key points behind it.")


def masked_case():
    docs = [Document("release", "the Company reports Q4", "Revenue rose 12% to $900.0 million."),
            Document("transcript", "Call", "We are confident.")]
    return MaskedCase("case-1", docs, "masked", 1, {"Acme": "the Company"}, 0.75, -5, [])


def agent():
    return FakeLLM(lambda s, p, schema, prefix: default_answer(schema, p))


def playbook_with_lesson():
    playbook = Playbook.initial()
    playbook.lessons.append(Lesson("Watch guidance.", "always", "guidance", "old-case"))
    return playbook


# --- researchos.perspectives -------------------------------------------------------

def test_modes_and_verbatim_system_prompts():
    assert MODES == ("holistic", "isolated")
    assert ISOLATED_ANALYST_SYSTEM == SPEC_ANALYST
    assert ISOLATED_JUDGE_SYSTEM == SPEC_JUDGE
    for text in (ISOLATED_ANALYST_SYSTEM, ISOLATED_JUDGE_SYSTEM):
        assert "\n" not in text and text.isascii()


@pytest.mark.parametrize("bad", ["Isolated", "pooled", "", None, 1, ["isolated"]])
def test_unknown_mode_raises_before_any_call(bad):
    analyst, judge = agent(), agent()
    with pytest.raises(ValueError) as info:
        analyze(masked_case(), Playbook.initial(), analyst, judge, mode=bad)
    assert repr(bad) in str(info.value)
    assert analyst.calls == [] and judge.calls == []


def test_holistic_is_the_default_and_unchanged():
    playbook = playbook_with_lesson()
    a1, j1, a2, j2 = agent(), agent(), agent(), agent()
    default = analyze(masked_case(), playbook, a1, j1)
    explicit = analyze(masked_case(), playbook, a2, j2, mode="holistic")
    assert default == explicit
    assert a1.calls == a2.calls and j1.calls == j2.calls
    assert {c["system"] for c in a1.calls} == {ANALYST_SYSTEM}
    assert j1.calls[0]["system"] == JUDGE_SYSTEM
    assert "Playbook-weighted probability of the views: " in j1.calls[0]["prompt"]
    assert default.prob_weighted == pytest.approx(sum(VIEW_PROBS.values()) / 7)


def test_isolated_prompts_calls_and_pooled_probability():
    playbook = playbook_with_lesson()
    playbook_text = render_for_prompt(playbook)
    ha, hj, analyst, judge = agent(), agent(), agent(), agent()
    holistic = analyze(masked_case(), playbook, ha, hj)
    a = analyze(masked_case(), playbook, analyst, judge, mode="isolated")

    # Same calls, same order, prefix and schemas.
    assert len(analyst.calls) == len(PERSPECTIVES) and len(judge.calls) == 1
    assert [c["prefix"] for c in analyst.calls + judge.calls] == \
        [c["prefix"] for c in ha.calls + hj.calls]
    assert all(c["schema"] is VIEW_SCHEMA for c in analyst.calls)
    assert judge.calls[0]["schema"] is JUDGE_SCHEMA

    for name, call in zip(PERSPECTIVES, analyst.calls):
        assert call["system"] == ISOLATED_ANALYST_SYSTEM
        assert call["prompt"] == (f"Perspective: {name}\nYour factor: {PERSPECTIVE_FOCUS[name]}\n"
                                  f"{SPEC_PARAGRAPH}\n\n{playbook_text}")

    expected = pool_log_odds(VIEW_PROBS, playbook.weights)
    assert 0.01 < expected < 0.99
    assert a.prob_weighted == pytest.approx(expected)
    assert a.prob_weighted != pytest.approx(holistic.prob_weighted)
    assert list(a.views) == PERSPECTIVES and a.views == holistic.views
    assert (a.prob_judge, a.thesis) == (0.65, "Beat and raise.")

    call = judge.calls[0]
    assert call["system"] == ISOLATED_JUDGE_SYSTEM
    pooled_line = ("Evidence-pooled probability of the views (their log-odds added): "
                   f"{a.prob_weighted:.3f}")
    holistic_line = f"Playbook-weighted probability of the views: {holistic.prob_weighted:.3f}"
    assert pooled_line in call["prompt"].splitlines()
    assert "Playbook-weighted" not in call["prompt"]
    # The rest of the judge's prompt is unchanged.
    assert call["prompt"].replace(pooled_line, holistic_line) == hj.calls[0]["prompt"]


def test_isolated_uses_weights_and_falls_back_to_unweighted_pool():
    playbook = Playbook.initial()
    playbook.weights = {"growth": 3.0, "risks": 1.0}
    a = analyze(masked_case(), playbook, agent(), agent(), mode="isolated")
    assert a.prob_weighted == pytest.approx(pool_log_odds(VIEW_PROBS, playbook.weights))
    assert a.prob_weighted != pytest.approx(combine_probabilities(VIEW_PROBS, playbook.weights))

    playbook.weights = {name: 0.0 for name in PERSPECTIVES}
    a = analyze(masked_case(), playbook, agent(), agent(), mode="isolated")
    assert a.prob_weighted == pytest.approx(pool_log_odds(VIEW_PROBS))


@pytest.mark.parametrize("p, clamped", [(0.95, 0.99), (0.05, 0.01)])
def test_isolated_pooled_probability_is_clamped(p, clamped):
    analyst = FakeLLM(responses=[{"stance": "neutral", "prob_up": p, "conviction": "high",
                                  "key_points": []}])
    judge = FakeLLM(responses=[{"prob_up": 0.5, "thesis": "t"}])
    a = analyze(masked_case(), Playbook.initial(), analyst, judge, mode="isolated")
    assert a.prob_weighted == clamped
    assert f"(their log-odds added): {clamped:.3f}" in judge.calls[0]["prompt"]


# --- researchos.loop ---------------------------------------------------------------

def save_cases(tmp_path, specs):
    root = tmp_path / "cases"
    for cid, ar, when in specs:
        case = make_case(cid)
        case.event_date = when
        save_case(root, case, make_outcome(ar=ar))
    return root


def test_evaluate_in_isolated_mode(tmp_path):
    root = save_cases(tmp_path, [("a-q1", 0.02, "2026-03-02"), ("b-q1", -0.01, "2026-03-05")])
    roles = make_roles()
    report = evaluate(root, ["a-q1", "b-q1"], tmp_path / "pb.json", roles,
                      perspective_mode="isolated")
    assert report["perspective_mode"] == "isolated"
    assert {c["system"] for c in roles.analyst.calls} == {ISOLATED_ANALYST_SYSTEM}
    assert {c["system"] for c in roles.judge.calls} == {ISOLATED_JUDGE_SYSTEM}
    pooled = pool_log_odds(VIEW_PROBS, Playbook.initial().weights)
    uniform = sum(VIEW_PROBS.values()) / 7
    for record, y in zip(report["cases"], (1, 0)):
        assert record["brier_weighted"] == pytest.approx((pooled - y) ** 2)
        assert record["brier_uniform"] == pytest.approx((uniform - y) ** 2)
        assert record["correct_weighted"] == ((pooled > 0.5) == (y == 1))
        assert record["prob_by_perspective"] == VIEW_PROBS
    sealed = json.loads((root / "a-q1" / "prediction.json").read_text(encoding="utf-8"))
    assert sealed["prob_weighted"] == pytest.approx(pooled)


def test_evaluate_and_train_default_to_holistic(tmp_path):
    root = save_cases(tmp_path, [("a-q1", 0.02, "2026-03-02")])
    roles = make_roles()
    report = evaluate(root, ["a-q1"], tmp_path / "pb.json", roles)
    assert report["perspective_mode"] == "holistic"
    assert {c["system"] for c in roles.analyst.calls} == {ANALYST_SYSTEM}
    roles = make_roles()
    records = train(root, ["a-q1"], tmp_path / "pb.json", roles)
    assert {c["system"] for c in roles.analyst.calls} == {ANALYST_SYSTEM}
    assert "perspective_mode" not in records[0]


def test_train_in_isolated_mode(tmp_path):
    root = save_cases(tmp_path, [("a-q1", 0.02, "2026-03-02"), ("b-q1", -0.01, "2026-03-05")])
    roles = make_roles()
    records = train(root, ["a-q1", "b-q1"], tmp_path / "pb.json", roles,
                    perspective_mode="isolated")
    assert len(records) == 2
    assert {c["system"] for c in roles.analyst.calls} == {ISOLATED_ANALYST_SYSTEM}
    pooled = pool_log_odds(VIEW_PROBS, Playbook.initial().weights)
    assert records[0]["brier_weighted"] == pytest.approx((pooled - 1) ** 2)
    # Nothing in the records changes: same keys as in holistic mode.
    holistic = train(save_cases(tmp_path / "h", [("a-q1", 0.02, "2026-03-02")]), ["a-q1"],
                     tmp_path / "h" / "pb.json", make_roles())
    assert set(records[0]) == set(holistic[0])


@pytest.mark.parametrize("run", [train, evaluate])
@pytest.mark.parametrize("bad", ["Holistic", "both", None])
def test_unknown_perspective_mode_refused_before_anything(tmp_path, run, bad):
    root = save_cases(tmp_path, [("a-q1", 0.02, "2026-03-02")])
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    roles = make_roles()
    with pytest.raises(ValueError) as info:
        run(root, ["a-q1"], tmp_path / "pb.json", roles, perspective_mode=bad)
    assert repr(bad) in str(info.value)
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
    for role in ("masker", "guesser", "analyst", "judge", "reflector"):
        assert getattr(roles, role).calls == []


# --- researchos.blend --------------------------------------------------------------

def rec(case_id, event_date, up, probs):
    y = 1.0 if up else 0.0
    return {"case_id": case_id, "split": "eval", "event_date": event_date, "actual_up": up,
            "brier_by_perspective": {n: (p - y) ** 2 for n, p in probs.items()},
            "prob_by_perspective": dict(probs)}


def test_pool_rule():
    p1 = dict(zip(PERSPECTIVES, (0.8, 0.7, 0.6, 0.55, 0.5, 0.3, 0.2)))
    p2 = dict(zip(PERSPECTIVES, (0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9)))
    p3 = dict(zip(PERSPECTIVES, (0.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5)))
    records = [rec("c2", "2026-02-01", False, p2), rec("c1", "2026-01-01", True, p1),
               rec("c3", "2026-03-01", True, p3)]
    out = replay(records, "pool", eta=2.0)
    assert (out["rule"], out["eta"], out["n"]) == ("pool", None, 3)
    assert [c["case_id"] for c in out["cases"]] == ["c1", "c2", "c3"]
    expected = [pool_log_odds(p1), 0.99, 0.01]
    assert [c["prob"] for c in out["cases"]] == [pytest.approx(p) for p in expected]
    assert [c["brier_blend"] for c in out["cases"]] == [
        pytest.approx((expected[0] - 1) ** 2), pytest.approx(0.99 ** 2),
        pytest.approx(0.99 ** 2)]
    assert out["brier_blend"] == pytest.approx(statistics.fmean(
        c["brier_blend"] for c in out["cases"]))
    # It learns nothing from earlier cases: alone, each case is forecast the same.
    for record, case in zip(sorted(records, key=lambda r: r["event_date"]), out["cases"]):
        assert replay([record], "pool")["cases"][0]["prob"] == case["prob"]
    # agreement is unchanged by the new rule.
    assert agreement(records)["n"] == 3


# --- CLI ---------------------------------------------------------------------------

def cli_root(tmp_path):
    root = tmp_path / "cases"
    save_case(root, make_case("a"), make_outcome(ar=0.02))
    return root


def test_perspective_mode_is_passed_on_only_when_given(tmp_path, capsys, monkeypatch):
    import researchos.__main__ as main_mod

    root = cli_root(tmp_path)
    seen = []

    def fake_run(root_, ids, playbook, roles, **kwargs):
        seen.append(kwargs)
        return {"n": 0}

    monkeypatch.setattr(main_mod, "train", fake_run)
    monkeypatch.setattr(main_mod, "evaluate", fake_run)
    base = ["--root", str(root), "--playbook", str(tmp_path / "pb.json"), "--ids", "a"]
    for cmd, mode in (("train", "isolated"), ("evaluate", "holistic"), ("evaluate", "isolated")):
        assert main([cmd, *base, "--perspective-mode", mode], client_factory=schema_client) == 0
        assert json.loads(capsys.readouterr().out) == {"n": 0}
    for cmd in ("train", "evaluate"):
        assert main([cmd, *base], client_factory=schema_client) == 0
    assert [kw.get("perspective_mode") for kw in seen[:3]] == ["isolated", "holistic",
                                                               "isolated"]
    assert all("perspective_mode" not in kw for kw in seen[3:])


def test_bad_perspective_mode_exits_2(tmp_path, capsys):
    for cmd in ("train", "evaluate"):
        for bad in ("Isolated", "pool", ""):
            assert main([cmd, "--root", "x", "--playbook", "p", "--ids", "a",
                         "--perspective-mode", bad]) == 2
    assert main(["blend", "f.json", "--perspective-mode", "isolated"]) == 2
    assert capsys.readouterr().out == ""


def test_cli_evaluate_isolated_end_to_end(tmp_path, capsys):
    root = cli_root(tmp_path)
    client = schema_client()
    code = main(["evaluate", "--root", str(root), "--playbook", str(tmp_path / "pb.json"),
                 "--ids", "a", "--perspective-mode", "isolated"], client_factory=lambda: client)
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["perspective_mode"] == "isolated" and result["n"] == 1
    systems = {r["system"] if isinstance(r["system"], str) else json.dumps(r["system"])
               for r in client.requests}
    assert any(ISOLATED_ANALYST_SYSTEM in s for s in systems)
    assert any(ISOLATED_JUDGE_SYSTEM in s for s in systems)


def test_cli_blend_rule_pool(tmp_path, capsys):
    p1 = dict(zip(PERSPECTIVES, (0.8, 0.7, 0.6, 0.55, 0.5, 0.3, 0.2)))
    report = tmp_path / "eval.json"
    report.write_text(json.dumps({"cases": [rec("c1", "2026-01-01", True, p1)]}),
                      encoding="utf-8")
    assert main(["blend", str(report), "--rule", "pool"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rule"] == "pool" and out["eta"] is None
    assert out["cases"][0]["prob"] == pytest.approx(pool_log_odds(p1))
