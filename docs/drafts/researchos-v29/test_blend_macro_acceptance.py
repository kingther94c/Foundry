"""Acceptance tests for blend and agreement over the macro perspectives (v29), written from
SPEC.md independently of the code (held back from the agent that built it).

Each case traces to the "Blend and agreement over the macro perspectives (v29)" section of
SPEC.md. The leader rule makes the walk-forward visible: which perspective leads depends only
on which records the replay has learned from.
"""

from __future__ import annotations

import json
import statistics
from itertools import combinations

import pytest
from tools.stats import combine_probabilities, hedge_update, pool_log_odds

from researchos import blend, targets
from researchos.__main__ import main
from researchos.perspectives import PERSPECTIVES

ABC = ["a", "b", "c"]


def record(case_id, event_date, up, probs, resolution_date=None):
    y = 1.0 if up else 0.0
    data = {"case_id": case_id, "event_date": event_date}
    if resolution_date is not None:
        data["resolution_date"] = resolution_date
    data.update({"actual_up": up, "prob_by_perspective": dict(probs),
                 "brier_by_perspective": {k: (p - y) ** 2 for k, p in probs.items()}})
    return data


THREE = [
    record("r1", "2026-07-03", True, {"a": 0.8, "b": 0.6, "c": 0.2}),
    record("r2", "2026-07-10", False, {"a": 0.4, "b": 0.6, "c": 0.7}),
    record("r3", "2026-07-17", True, {"a": 0.6, "b": 0.5, "c": 0.9}),
]


def probs_of(result):
    return [case["prob"] for case in result["cases"]]


# --- perspectives ---------------------------------------------------------------------------------

def earnings_records():
    names = PERSPECTIVES
    out = []
    for i, (when, up) in enumerate([("2026-02-01", True), ("2026-02-01", False),
                                    ("2026-05-01", True), ("2026-08-01", False)]):
        out.append(record(f"e{i}", when, up,
                          {n: round(0.1 + 0.8 * ((i + 3 * j) % 7) / 6, 3)
                           for j, n in enumerate(names)}))
    return out


@pytest.mark.parametrize("rule", blend.RULES)
def test_no_perspectives_means_the_seven(rule):
    records = earnings_records()
    plain = blend.replay(records, rule)
    assert blend.replay(records, rule, perspectives=None) == plain
    assert blend.replay(records, rule, perspectives=list(PERSPECTIVES)) == plain
    assert blend.agreement(records, perspectives=None) == blend.agreement(records)


def test_agreement_over_given_perspectives():
    result = blend.agreement(THREE, perspectives=ABC)
    assert result["n"] == 3
    assert list(result["perspectives"]) == ABC
    a, b, c = (result["perspectives"][n] for n in ABC)
    assert a["mean_prob"] == pytest.approx(0.6)
    assert a["sd_prob"] == pytest.approx((0.08 / 3) ** 0.5)
    assert a["brier"] == pytest.approx(0.12)
    assert (a["calls"], a["hits"]) == (3, 3)
    assert (b["calls"], b["hits"]) == (2, 1)
    assert (c["calls"], c["hits"]) == (3, 1)
    assert [(p["a"], p["b"]) for p in result["pairs"]] == [("a", "b"), ("a", "c"), ("b", "c")]
    series = {n: [r["prob_by_perspective"][n] for r in THREE] for n in ABC}
    for pair in result["pairs"]:
        assert pair["correlation"] == pytest.approx(
            statistics.correlation(series[pair["a"]], series[pair["b"]]))
    found = [p["correlation"] for p in result["pairs"]]
    assert result["mean_correlation"] == pytest.approx(statistics.fmean(found))
    assert result["min_correlation"] == pytest.approx(min(found))


def test_agreement_over_the_macro_perspectives():
    macro = targets.MACRO_PERSPECTIVES
    records = [record(f"m/{k}", "2026-07-03", k % 2 == 0,
                      {n: round(0.05 + 0.09 * ((k + j * j) % 11), 2) for j, n in enumerate(macro)})
               for k in range(5)]
    result = blend.agreement(records, perspectives=list(macro))
    assert list(result["perspectives"]) == macro
    assert len(result["pairs"]) == 45
    assert [(p["a"], p["b"]) for p in result["pairs"]] == list(combinations(macro, 2))


def test_uniform_median_and_leader_over_given_perspectives():
    assert probs_of(blend.replay(THREE, "uniform", perspectives=ABC)) == pytest.approx(
        [1.6 / 3, 1.7 / 3, 2.0 / 3])
    assert probs_of(blend.replay(THREE, "median", perspectives=ABC)) == pytest.approx(
        [0.6, 0.6, 0.6])
    # after r1 the Brier scores are a 0.04, b 0.16, c 0.64; after r2 a 0.20, b 0.52, c 1.13
    assert probs_of(blend.replay(THREE, "leader", perspectives=ABC)) == pytest.approx(
        [1.6 / 3, 0.4, 0.6])


def test_pool_over_given_perspectives():
    expected = [min(max(pool_log_odds({n: r["prob_by_perspective"][n] for n in ABC}), 0.01), 0.99)
                for r in THREE]
    assert probs_of(blend.replay(THREE, "pool", perspectives=ABC)) == pytest.approx(expected)


def test_hedge_over_given_perspectives():
    weights = {n: 1 / 3 for n in ABC}
    expected = []
    for r in THREE:
        probs = {n: r["prob_by_perspective"][n] for n in ABC}
        expected.append(combine_probabilities(probs, weights))
        y = 1.0 if r["actual_up"] else 0.0
        weights = hedge_update(weights, {n: (p - y) ** 2 for n, p in probs.items()}, 0.5)
    result = blend.replay(THREE, "hedge", eta=0.5, perspectives=ABC)
    assert probs_of(result) == pytest.approx(expected)
    assert result["eta"] == 0.5 and result["n"] == 3


def test_leader_ties_go_to_the_first_given():
    records = [record("t1", "2026-07-03", True, {"a": 0.7, "b": 0.7}),
               record("t2", "2026-07-10", True, {"a": 0.2, "b": 0.9})]
    assert probs_of(blend.replay(records, "leader", perspectives=["b", "a"]))[1] == 0.9
    assert probs_of(blend.replay(records, "leader", perspectives=["a", "b"]))[1] == 0.2


def test_other_probabilities_are_ignored():
    extra = [dict(r, prob_by_perspective={**r["prob_by_perspective"], "z": 0.99}) for r in THREE]
    assert blend.replay(extra, "uniform", perspectives=ABC) == blend.replay(
        THREE, "uniform", perspectives=ABC)


def test_a_missing_perspective_is_named():
    with pytest.raises(ValueError) as exc:
        blend.replay(THREE, "uniform", perspectives=["a", "b", "c", "d"])
    assert "r1" in str(exc.value) and "d" in str(exc.value)
    with pytest.raises(ValueError):
        blend.agreement(THREE, perspectives=["a", "q"])
    with pytest.raises(ValueError):
        blend.replay(THREE)  # the seven earnings perspectives are not there


@pytest.mark.parametrize("bad", [[], ["a", "a"], "abc", [1, 2], [None]])
def test_bad_perspectives_are_refused_before_any_record_is_read(bad):
    def records():
        raise AssertionError("a record was read")
        yield  # pragma: no cover

    with pytest.raises(ValueError):
        blend.replay(records(), "uniform", perspectives=bad)
    with pytest.raises(ValueError):
        blend.agreement(records(), perspectives=bad)


# --- resolution dates -----------------------------------------------------------------------------

AB = ["a", "b"]
WALK = [
    record("A", "2026-07-03", True, {"a": 0.9, "b": 0.1}, "2026-07-10"),
    record("A1m", "2026-07-03", True, {"a": 0.0, "b": 1.0}, "2026-08-03"),
    record("B", "2026-07-10", False, {"a": 0.3, "b": 0.7}, "2026-07-17"),
    record("C", "2026-07-17", True, {"a": 0.6, "b": 0.2}, "2026-07-24"),
    record("D", "2026-07-24", True, {"a": 0.4, "b": 0.8}, "2026-07-31"),
]


def strip(records):
    return [{k: v for k, v in r.items() if k != "resolution_date"} for r in records]


def test_a_record_is_learned_from_only_once_it_has_resolved():
    # B: nothing has resolved before 2026-07-10 (A resolves that day), so the uniform mean.
    # C: A has resolved, A1m has not: a leads.  D: A and B have resolved: a still leads.
    result = blend.replay(WALK, "leader", perspectives=AB)
    assert probs_of(result) == pytest.approx([0.5, 0.5, 0.5, 0.6, 0.4])


def test_without_resolution_dates_the_old_rule_holds():
    # B is forecast after A and A1m are learned (A1m makes b the leader).
    assert probs_of(blend.replay(strip(WALK), "leader", perspectives=AB)) == pytest.approx(
        [0.5, 0.5, 0.7, 0.6, 0.4])


def test_resolving_on_the_event_date_is_the_old_rule():
    same = [dict(r, resolution_date=r["event_date"]) for r in WALK]
    for rule in blend.RULES:
        assert probs_of(blend.replay(same, rule, perspectives=AB)) == probs_of(
            blend.replay(strip(WALK), rule, perspectives=AB))


def test_a_replayed_case_carries_its_resolution_date():
    result = blend.replay(WALK, "leader", perspectives=AB)
    first = result["cases"][0]
    assert list(first) == ["case_id", "event_date", "resolution_date", "actual_up", "prob",
                           "brier_blend"]
    assert first["resolution_date"] == "2026-07-10"
    assert [c["case_id"] for c in result["cases"]] == ["A", "A1m", "B", "C", "D"]
    old = blend.replay(strip(WALK), "leader", perspectives=AB)
    assert list(old["cases"][0]) == ["case_id", "event_date", "actual_up", "prob", "brier_blend"]


def test_a_padded_resolution_date_is_written_canonically():
    padded = [dict(WALK[0], resolution_date=" 2026-07-10 ")] + WALK[1:]
    try:
        result = blend.replay(padded, "uniform", perspectives=AB)
    except ValueError:
        return  # parse_iso_date may refuse it; then it is refused
    assert result["cases"][0]["resolution_date"] == "2026-07-10"


def test_mixed_records_with_and_without_dates():
    mixed = [WALK[0], strip(WALK[1:2])[0], *WALK[2:]]
    # A1m now resolves on its event date: it is learned after the first batch, with A at
    # 2026-07-10. B is forecast from A1m alone and C from A1m and A: b leads both; D from
    # A1m, A and B: a leads.
    result = blend.replay(mixed, "leader", perspectives=AB)
    assert probs_of(result) == pytest.approx([0.5, 0.5, 0.7, 0.2, 0.4])
    assert "resolution_date" not in result["cases"][1]


@pytest.mark.parametrize("value", ["2026-07-01", None, "07/10/2026", 20260710, ""])
def test_a_bad_resolution_date_is_refused(value):
    bad = [dict(WALK[2], resolution_date=value)]
    with pytest.raises(ValueError) as exc:
        blend.replay(WALK[:2] + bad, "uniform", perspectives=AB)
    assert "B" in str(exc.value).replace("Brier", "")


def test_agreement_takes_no_account_of_dates():
    assert blend.agreement(WALK, perspectives=AB) == blend.agreement(strip(WALK), perspectives=AB)


def test_the_last_records_are_never_needed():
    result = blend.replay(WALK, "hedge", perspectives=AB)
    assert result["n"] == 5
    assert result["brier_blend"] == pytest.approx(
        sum(c["brier_blend"] for c in result["cases"]) / 5)


# --- the command line -----------------------------------------------------------------------------

def macro_records():
    macro = targets.MACRO_PERSPECTIVES
    return [record(f"macro-2026-07-{d:02d}/DGS10_1w", f"2026-07-{d:02d}", d % 2 == 1,
                   {n: round(0.1 + 0.08 * ((d + j) % 10), 2) for j, n in enumerate(macro)},
                   f"2026-07-{d + 7:02d}")
            for d in (3, 10, 17)]


def run(argv, capsys):
    code = main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


def test_blend_with_the_macro_perspectives(tmp_path, capsys):
    path = tmp_path / "score.json"
    path.write_text(json.dumps({"cases": macro_records(), "n": 3}), encoding="utf-8")
    code, out, err = run(["blend", path, "--rule", "leader", "--perspectives", "macro"], capsys)
    assert code == 0
    assert json.loads(out) == json.loads(json.dumps(blend.replay(
        macro_records(), "leader", eta=0.5, perspectives=targets.MACRO_PERSPECTIVES)))
    code, out, err = run(["blend", path], capsys)
    assert code == 1 and "error" in err


def test_agreement_with_the_macro_perspectives(tmp_path, capsys):
    path = tmp_path / "score.json"
    path.write_text(json.dumps({"cases": macro_records()}), encoding="utf-8")
    code, out, err = run(["agreement", path, "--perspectives", "macro"], capsys)
    assert code == 0
    assert json.loads(out) == json.loads(json.dumps(blend.agreement(
        macro_records(), perspectives=targets.MACRO_PERSPECTIVES)))
    assert len(json.loads(out)["pairs"]) == 45
    code, out, err = run(["agreement", path], capsys)
    assert code == 1


def test_earnings_by_name_is_the_default(tmp_path, capsys):
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"cases": earnings_records()}), encoding="utf-8")
    for command in (["blend", path, "--rule", "hedge"], ["agreement", path]):
        code, plain, _ = run(command, capsys)
        code2, named, _ = run(command + ["--perspectives", "earnings"], capsys)
        assert (code, code2) == (0, 0) and plain == named


def test_perspectives_not_passed_unless_given(tmp_path, capsys, monkeypatch):
    import researchos.__main__ as cli

    seen = []
    real_replay, real_agreement = blend.replay, blend.agreement

    def spy_replay(records, rule="hedge", **kwargs):
        seen.append(("replay", sorted(kwargs)))
        return real_replay(records, rule, **kwargs)

    def spy_agreement(records, **kwargs):
        seen.append(("agreement", sorted(kwargs)))
        return real_agreement(records, **kwargs)

    for module in (cli, blend):
        monkeypatch.setattr(module, "replay", spy_replay, raising=False)
        monkeypatch.setattr(module, "agreement", spy_agreement, raising=False)
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"cases": earnings_records()}), encoding="utf-8")
    assert run(["blend", path], capsys)[0] == 0
    assert run(["agreement", path], capsys)[0] == 0
    assert seen == [("replay", ["eta"]), ("agreement", [])]


def test_an_unknown_set_is_a_bad_argument(tmp_path, capsys):
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"cases": earnings_records()}), encoding="utf-8")
    assert run(["blend", path, "--perspectives", "credit"], capsys)[0] == 2
    assert run(["agreement", path, "--perspectives", "credit"], capsys)[0] == 2
