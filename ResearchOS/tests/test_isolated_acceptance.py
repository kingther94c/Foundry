"""Acceptance tests for "Perspectives built to disagree (v20)", written from SPEC.md
independently of the code (held back from the agent that built it)."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from test_acceptance import (OUTCOME, Fake, ScriptedClient, make_case, masked_case, role_of,
                             roles, view_reply)
from tools.stats import combine_probabilities, pool_log_odds

from researchos import blend, cases, loop, perspectives, playbook
from researchos.perspectives import PERSPECTIVES

P = list(PERSPECTIVES)

PARAGRAPH = ("Judge only this factor, as if everything else in the report had come in exactly as "
             "the market expected. Give your stance on this factor, the probability that the stock "
             "beats the market on the first session that can react from this factor alone (0.50 if "
             "it gives no signal), your conviction, and the key points behind it.")

ANALYST_TEXT = (
    "You are an equity analyst judging how a stock will react to a quarterly earnings report. "
    "The company's identity is hidden: names are replaced, money amounts are multiplied by a "
    "secret factor (absolute sizes are meaningless; ratios, growth rates and margins are intact) "
    "and years are shifted. Do not try to identify the company; reason only from the documents. "
    "You judge one factor in isolation. Assume that everything in the report outside your factor "
    "came in exactly as the market expected, and estimate the probability that the stock beats "
    "the market (positive abnormal return, stock minus market) on the first trading session that "
    "can react to the report, from your factor alone. 0.50 means your factor gives no signal "
    "either way; move away from 0.50 only as far as this factor by itself would move the stock. "
    "Do not give an overall view of the report: another analyst combines the factors.")

JUDGE_TEXT = (
    "You are the chief analyst. Several analysts have each judged one factor of a quarterly "
    "earnings report in isolation, each as if everything else in the report had come in as the "
    "market expected. The company's identity is hidden (names replaced, money scaled, years "
    "shifted). Their probabilities are evidence from different factors: they add up rather than "
    "average, and a view at 0.50 is a factor that gives no signal. Weigh them against the "
    "documents and the playbook, and give your own probability that the stock beats the market "
    "(positive abnormal return) on the first trading session that can react, with a short thesis.")

PROBS = {"growth": 0.7, "profitability": 0.55, "guidance": 0.4, "cash_flow": 0.5,
         "management_tone": 0.62, "expectations_gap": 0.8, "risks": 0.35}


def squash(text):
    return " ".join(text.split())


def run(mode=None, probs=PROBS, book=None, judge_prob=0.6):
    analyst = Fake(view_reply(probs))
    judge = Fake(lambda *a: {"prob_up": judge_prob, "thesis": "th"})
    book = book or playbook.Playbook.initial()
    kwargs = {} if mode is None else {"mode": mode}
    a = perspectives.analyze(masked_case(), book, analyst, judge, **kwargs)
    return a, analyst, judge, book


# --- the module's constants ------------------------------------------------------

def test_modes():
    assert perspectives.MODES == ("holistic", "isolated")


def test_system_prompts_verbatim():
    assert squash(perspectives.ISOLATED_ANALYST_SYSTEM) == ANALYST_TEXT
    assert squash(perspectives.ISOLATED_JUDGE_SYSTEM) == JUDGE_TEXT
    assert perspectives.ISOLATED_ANALYST_SYSTEM != perspectives.ANALYST_SYSTEM
    assert perspectives.ISOLATED_JUDGE_SYSTEM != perspectives.JUDGE_SYSTEM


# --- analyze, isolated ----------------------------------------------------------

def test_isolated_uses_the_isolated_prompts():
    a, analyst, judge, book = run("isolated")
    assert len(analyst.calls) == len(P) and len(judge.calls) == 1
    assert all(c["system"] == perspectives.ISOLATED_ANALYST_SYSTEM for c in analyst.calls)
    assert judge.calls[0]["system"] == perspectives.ISOLATED_JUDGE_SYSTEM
    rendered = playbook.render_for_prompt(book)
    for c in analyst.calls:
        lines = c["prompt"].splitlines()
        assert lines[0].startswith("Perspective: ")
        assert lines[1].startswith("Your factor: ")
        assert PARAGRAPH in squash(c["prompt"])
        assert rendered in c["prompt"]
        assert c["schema"] == perspectives.VIEW_SCHEMA
    assert judge.calls[0]["schema"] == perspectives.JUDGE_SCHEMA


def test_isolated_asks_every_perspective_once_in_order():
    _, analyst, _, _ = run("isolated")
    asked = [c["prompt"].splitlines()[0].removeprefix("Perspective:").strip() for c in analyst.calls]
    assert asked == P


def test_factor_line_is_the_holistic_focus():
    _, iso, _, _ = run("isolated")
    _, hol, _, _ = run()
    for i, name in enumerate(P):
        focus = iso.calls[i]["prompt"].splitlines()[1].removeprefix("Your factor: ").strip()
        assert focus, name
        assert hol.calls[i]["prompt"].splitlines()[0] == f"Perspective: {name}"
        assert focus in hol.calls[i]["prompt"], name


def test_isolated_shares_the_holistic_prefix():
    _, iso, iso_judge, _ = run("isolated")
    _, hol, hol_judge, _ = run()
    prefixes = {c["prefix"] for c in iso.calls}
    assert prefixes == {c["prefix"] for c in hol.calls}
    assert len(prefixes) == 1
    assert iso_judge.calls[0]["prefix"] == hol_judge.calls[0]["prefix"] == next(iter(prefixes))


def test_isolated_prob_weighted_pools_log_odds():
    a, _, judge, book = run("isolated")
    probs = {k: v.prob_up for k, v in a.views.items()}
    assert probs == pytest.approx(PROBS)
    expected = pool_log_odds(probs, book.weights)
    assert a.prob_weighted == pytest.approx(expected)
    assert a.prob_weighted != pytest.approx(combine_probabilities(probs, book.weights))
    assert a.prob_judge == 0.6 and a.thesis == "th"
    assert list(a.views) == P


def test_isolated_judge_prompt_names_the_pooled_probability():
    a, _, judge, _ = run("isolated")
    prompt = judge.calls[0]["prompt"]
    line = f"Evidence-pooled probability of the views (their log-odds added): {a.prob_weighted:.3f}"
    assert line in prompt
    assert "Playbook-weighted probability of the views" not in prompt
    for name in P:
        assert f"point from {name}" in prompt
    assert playbook.render_for_prompt(playbook.Playbook.initial()) in prompt


def test_isolated_weights_count():
    book = playbook.Playbook.initial()
    book.weights = {p: (4.0 if p == "risks" else 1.0) for p in P}
    a, _, _, _ = run("isolated", book=book)
    probs = {k: v.prob_up for k, v in a.views.items()}
    assert 0.01 < pool_log_odds(probs, book.weights) < 0.99
    assert a.prob_weighted == pytest.approx(pool_log_odds(probs, book.weights))
    assert a.prob_weighted != pytest.approx(pool_log_odds(probs))


def test_isolated_falls_back_to_unweighted_pool():
    book = playbook.Playbook.initial()
    book.weights = {p: 0.0 for p in P}
    a, _, _, _ = run("isolated", book=book)
    probs = {k: v.prob_up for k, v in a.views.items()}
    assert a.prob_weighted == pytest.approx(pool_log_odds(probs))
    book.weights = {}
    a, _, _, _ = run("isolated", book=book)
    assert a.prob_weighted == pytest.approx(pool_log_odds(probs))


@pytest.mark.parametrize("value,clamped", [(0.95, 0.99), (0.05, 0.01), (1.7, 0.99), (-3, 0.01)])
def test_isolated_pool_is_clamped(value, clamped):
    a, _, judge, _ = run("isolated", probs={p: value for p in P})
    assert a.prob_weighted == clamped
    assert f"(their log-odds added): {clamped:.3f}" in judge.calls[0]["prompt"]


def test_isolated_pool_of_neutral_views_is_one_half():
    a, _, _, _ = run("isolated", probs={p: 0.5 for p in P})
    assert a.prob_weighted == pytest.approx(0.5)


# --- analyze, holistic stays as it was -----------------------------------------------

@pytest.mark.parametrize("mode", [None, "holistic"])
def test_holistic_is_the_default_and_unchanged(mode):
    a, analyst, judge, book = run(mode)
    assert all(c["system"] == perspectives.ANALYST_SYSTEM for c in analyst.calls)
    assert judge.calls[0]["system"] == perspectives.JUDGE_SYSTEM
    probs = {k: v.prob_up for k, v in a.views.items()}
    assert a.prob_weighted == pytest.approx(combine_probabilities(probs, book.weights))
    prompt = judge.calls[0]["prompt"]
    assert f"Playbook-weighted probability of the views: {a.prob_weighted:.3f}" in prompt
    assert "Evidence-pooled" not in prompt
    assert all("Your factor:" not in c["prompt"] for c in analyst.calls)


@pytest.mark.parametrize("bad", ["Isolated", "pooled", "", None, 1])
def test_unknown_mode_raises_before_any_call(bad):
    analyst = Fake(view_reply(PROBS))
    judge = Fake(lambda *a: {"prob_up": 0.5, "thesis": ""})
    with pytest.raises(ValueError):
        perspectives.analyze(masked_case(), playbook.Playbook.initial(), analyst, judge, mode=bad)
    assert analyst.calls == [] and judge.calls == []


# --- the loop ------------------------------------------------------------------------

def one_case(tmp_path, case_id="c1", event_date="2026-07-30", outcome=OUTCOME):
    cases.save_case(tmp_path, replace(make_case(case_id), event_date=event_date), outcome)
    return tmp_path


def test_evaluate_isolated(tmp_path):
    one_case(tmp_path)
    r = roles()
    report = loop.evaluate(tmp_path, ["c1"], tmp_path / "pb.json", r, perspective_mode="isolated")
    assert report["perspective_mode"] == "isolated"
    assert report["n"] == 1
    assert all(c["system"] == perspectives.ISOLATED_ANALYST_SYSTEM for c in r.analyst.calls)
    assert all(c["system"] == perspectives.ISOLATED_JUDGE_SYSTEM for c in r.judge.calls)
    record = report["cases"][0]
    probs = record["prob_by_perspective"]
    pooled = pool_log_odds(probs, playbook.Playbook.initial().weights)
    assert record["brier_weighted"] == pytest.approx((pooled - 1.0) ** 2)
    mean = sum(probs.values()) / len(probs)
    assert record["brier_uniform"] == pytest.approx((mean - 1.0) ** 2)
    sealed = json.loads((tmp_path / "c1" / "prediction.json").read_text(encoding="utf-8"))
    assert sealed["prob_weighted"] == pytest.approx(pooled)


def test_evaluate_defaults_to_holistic(tmp_path):
    one_case(tmp_path)
    r = roles()
    report = loop.evaluate(tmp_path, ["c1"], tmp_path / "pb.json", r)
    assert report["perspective_mode"] == "holistic"
    assert all(c["system"] == perspectives.ANALYST_SYSTEM for c in r.analyst.calls)
    report = loop.evaluate(tmp_path, ["c1"], tmp_path / "pb.json", roles(),
                           perspective_mode="holistic")
    assert report["perspective_mode"] == "holistic"


def test_evaluate_with_nothing_scored_still_names_the_mode(tmp_path):
    report = loop.evaluate(tmp_path, [], tmp_path / "pb.json", roles(), perspective_mode="isolated")
    assert report["perspective_mode"] == "isolated" and report["n"] == 0


def test_train_isolated(tmp_path):
    one_case(tmp_path)
    r = roles()
    pb = tmp_path / "pb.json"
    records = loop.train(tmp_path, ["c1"], pb, r, perspective_mode="isolated")
    assert len(records) == 1
    assert all(c["system"] == perspectives.ISOLATED_ANALYST_SYSTEM for c in r.analyst.calls)
    assert all(c["system"] == perspectives.ISOLATED_JUDGE_SYSTEM for c in r.judge.calls)
    assert len(r.reflector.calls) == 1
    probs = records[0]["prob_by_perspective"]
    pooled = pool_log_odds(probs, playbook.Playbook.initial().weights)
    assert records[0]["brier_weighted"] == pytest.approx((pooled - 1.0) ** 2)
    assert playbook.load_playbook(pb).lessons


def test_train_defaults_to_holistic(tmp_path):
    one_case(tmp_path)
    r = roles()
    loop.train(tmp_path, ["c1"], tmp_path / "pb.json", r)
    assert all(c["system"] == perspectives.ANALYST_SYSTEM for c in r.analyst.calls)


@pytest.mark.parametrize("command", ["train", "evaluate"])
@pytest.mark.parametrize("bad", ["Isolated", "blind", None])
def test_loop_refuses_an_unknown_mode_before_anything(tmp_path, command, bad):
    one_case(tmp_path)
    r = roles()
    pb = tmp_path / "pb.json"
    fn = loop.train if command == "train" else loop.evaluate
    with pytest.raises(ValueError):
        fn(tmp_path, ["c1"], pb, r, perspective_mode=bad)
    assert not pb.exists()
    assert not (tmp_path / "c1" / "masked.json").exists()
    assert not (tmp_path / "c1" / "prediction.json").exists()
    for agent in (r.masker, r.guesser, r.analyst, r.judge, r.reflector):
        assert agent.calls == []


# --- blend ----------------------------------------------------------------------------

def rec(case_id, event_date, up, probs):
    y = 1.0 if up else 0.0
    return {"case_id": case_id, "split": "eval", "event_date": event_date, "actual_up": up,
            "brier_by_perspective": {p: (v - y) ** 2 for p, v in probs.items()},
            "prob_by_perspective": dict(probs)}


A = dict(zip(P, (0.9, 0.6, 0.7, 0.4, 0.5, 0.8, 0.2)))
B = dict(zip(P, (0.3, 0.55, 0.4, 0.6, 0.5, 0.35, 0.7)))
HIGH = {p: 0.9 for p in P}


def test_rules_gain_pool():
    assert blend.RULES == ("uniform", "median", "hedge", "leader", "pool")


def test_replay_pool():
    records = [rec("a", "2026-07-02", True, A), rec("b", "2026-07-01", False, B),
               rec("c", "2026-07-03", False, HIGH)]
    out = blend.replay(records, "pool", eta=2.0)
    assert out["rule"] == "pool" and out["eta"] is None and out["n"] == 3
    assert [c["case_id"] for c in out["cases"]] == ["b", "a", "c"]
    assert out["cases"][0]["prob"] == pytest.approx(pool_log_odds(B))
    assert out["cases"][1]["prob"] == pytest.approx(pool_log_odds(A))
    assert out["cases"][2]["prob"] == 0.99
    assert out["cases"][2]["brier_blend"] == pytest.approx(0.99 ** 2)
    assert out["cases"][1]["brier_blend"] == pytest.approx((pool_log_odds(A) - 1) ** 2)
    assert out["brier_blend"] == pytest.approx(sum(c["brier_blend"] for c in out["cases"]) / 3)


def test_replay_pool_learns_nothing():
    first = blend.replay([rec("a", "2026-07-01", True, A), rec("b", "2026-07-02", False, B)], "pool")
    alone = blend.replay([rec("b", "2026-07-02", False, B)], "pool")
    assert first["cases"][1]["prob"] == pytest.approx(alone["cases"][0]["prob"])


def test_replay_pool_low_clamp():
    out = blend.replay([rec("z", "2026-07-01", True, {p: 0.02 for p in P})], "pool")
    assert out["cases"][0]["prob"] == 0.01


def test_blend_cli_rule_pool(tmp_path, capsys):
    from researchos.__main__ import main
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"cases": [rec("a", "2026-07-02", True, A)]}), encoding="utf-8")
    assert main(["blend", str(path), "--rule", "pool"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rule"] == "pool" and out["cases"][0]["prob"] == pytest.approx(pool_log_odds(A))


# --- CLI ------------------------------------------------------------------------------

def analyst_systems(client):
    return {kw["system"] for kw in client.kwargs if role_of(kw) == "analyst"}


def judge_systems(client):
    return {kw["system"] for kw in client.kwargs if role_of(kw) == "judge"}


def test_cli_perspective_mode_isolated(tmp_path, capsys):
    from researchos.__main__ import main
    one_case(tmp_path)
    client = ScriptedClient()
    assert main(["evaluate", "--root", str(tmp_path), "--playbook", str(tmp_path / "pb.json"),
                 "--ids", "c1", "--perspective-mode", "isolated"], client_factory=lambda: client) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["perspective_mode"] == "isolated"
    assert analyst_systems(client) == {perspectives.ISOLATED_ANALYST_SYSTEM}
    assert judge_systems(client) == {perspectives.ISOLATED_JUDGE_SYSTEM}


def test_cli_train_perspective_mode(tmp_path, capsys):
    from researchos.__main__ import main
    one_case(tmp_path)
    client = ScriptedClient()
    assert main(["train", "--root", str(tmp_path), "--playbook", str(tmp_path / "pb.json"),
                 "--ids", "c1", "--perspective-mode", "isolated"], client_factory=lambda: client) == 0
    capsys.readouterr()
    assert analyst_systems(client) == {perspectives.ISOLATED_ANALYST_SYSTEM}


def test_cli_without_the_flag_passes_nothing(tmp_path, monkeypatch, capsys):
    from researchos import __main__ as cli
    one_case(tmp_path)
    seen = []

    def fake_run(root_, ids, playbook_, roles_, seed=0, mask_config=None, knowledge_cutoff=None):
        seen.append(ids)
        return {"cases": [], "n": 0}

    monkeypatch.setattr(cli, "evaluate", fake_run)
    monkeypatch.setattr(cli, "train", fake_run)
    for command in ("train", "evaluate"):
        assert cli.main([command, "--root", str(tmp_path), "--playbook", str(tmp_path / "pb.json"),
                         "--ids", "c1"], client_factory=ScriptedClient) == 0
    assert seen == [["c1"], ["c1"]]


def test_cli_passes_the_mode_when_given(tmp_path, monkeypatch, capsys):
    from researchos import __main__ as cli
    one_case(tmp_path)
    seen = []

    def fake_run(root_, ids, playbook_, roles_, **kwargs):
        seen.append(kwargs.get("perspective_mode", "absent"))
        return {"cases": [], "n": 0}

    monkeypatch.setattr(cli, "evaluate", fake_run)
    monkeypatch.setattr(cli, "train", fake_run)
    for command, mode in (("train", "isolated"), ("evaluate", "holistic")):
        assert cli.main([command, "--root", str(tmp_path), "--playbook", str(tmp_path / "pb.json"),
                         "--ids", "c1", "--perspective-mode", mode], client_factory=ScriptedClient) == 0
    assert seen == ["isolated", "holistic"]


@pytest.mark.parametrize("bad", ["Isolated", "pool", ""])
def test_cli_rejects_an_unknown_mode(tmp_path, bad, capsys):
    from researchos.__main__ import main
    one_case(tmp_path)
    client = ScriptedClient()
    assert main(["evaluate", "--root", str(tmp_path), "--playbook", str(tmp_path / "pb.json"),
                 "--ids", "c1", "--perspective-mode", bad], client_factory=lambda: client) == 2
    assert client.kwargs == []
