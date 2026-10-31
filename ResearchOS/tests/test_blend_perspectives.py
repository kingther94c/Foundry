"""Blend and agreement over the macro perspectives (SPEC.md v29): the perspectives that
replay and agreement read, and replaying by the date each record resolved."""

import copy
import datetime
import json
import math
import statistics
from itertools import combinations

import pytest
from tools.stats import combine_probabilities, hedge_update

import researchos.__main__ as main_mod
from researchos.__main__ import main
from researchos.blend import RULES, agreement, replay
from researchos.perspectives import PERSPECTIVES
from researchos.targets import MACRO_PERSPECTIVES, perspective_set

MACRO = list(MACRO_PERSPECTIVES)
CASE_KEYS = ["case_id", "event_date", "actual_up", "prob", "brier_blend"]
DATED_KEYS = ["case_id", "event_date", "resolution_date", "actual_up", "prob", "brier_blend"]


def mps(**named):
    """The ten macro perspectives' probabilities: 0.5 everywhere but the named ones."""
    out = {name: 0.5 for name in MACRO}
    out.update(named)
    return out


def eps(*values):
    """The seven earnings perspectives' probabilities, in PERSPECTIVES order."""
    assert len(values) == len(PERSPECTIVES)
    return dict(zip(PERSPECTIVES, values))


def rec(case_id, event_date, up, probs, resolution_date=None):
    """A scored record; a resolution_date goes after event_date, as score_targets puts it."""
    y = 1.0 if up else 0.0
    record = {"case_id": case_id, "event_date": event_date}
    if resolution_date is not None:
        record["resolution_date"] = resolution_date
    record.update({"actual_up": up, "brier_weighted": 0.2, "brier_judge": 0.2,
                   "brier_by_perspective": {name: (p - y) ** 2 for name, p in probs.items()},
                   "prob_by_perspective": dict(probs)})
    return record


def undated(record):
    return {k: v for k, v in record.items() if k != "resolution_date"}


def ab(record):
    return {name: record["prob_by_perspective"][name] for name in ("a", "b")}


def losses(record):
    y = 1.0 if record["actual_up"] else 0.0
    return {name: (p - y) ** 2 for name, p in ab(record).items()}


def weighted(probs, wa, wb):
    return (probs["a"] * wa + probs["b"] * wb) / (wa + wb)


# Macro records as score_targets writes them, one per target key, each resolving at the end
# of its horizon. Only growth, inflation and labor move; the other seven stay at 0.5.
M1 = rec("s1/ust10y:1m", "2026-01-02", True, mps(growth=0.9), "2026-02-02")
M2 = rec("s2/ust10y:1w", "2026-01-09", True, mps(inflation=0.8), "2026-01-16")
M3 = rec("s3/ust10y:1w", "2026-01-16", False, mps(growth=0.6, labor=0.3), "2026-01-21")
M4 = rec("s4/ust10y:1w", "2026-01-23", True, mps(growth=0.95, inflation=0.7, labor=0.2),
         "2026-01-30")
M5 = rec("s5/ust10y:1w", "2026-02-02", True, mps(growth=0.65, inflation=0.55, labor=0.45),
         "2026-02-09")
# A record without a resolution_date resolves on its event date.
M6 = rec("s6/spx:1d", "2026-02-06", False, mps(growth=0.75, inflation=0.25))
MACRO_RECORDS = [M1, M2, M3, M4, M5, M6]
SHUFFLED = [M6, M3, M1, M5, M2, M4]
MACRO_LEADER_PROBS = (0.54, 0.53, 0.49, 0.7, 0.55, 0.75)

EARNINGS_RECORDS = [rec("e1", "2026-01-10", True, eps(0.8, 0.7, 0.6, 0.55, 0.5, 0.3, 0.2)),
                    rec("e2", "2026-02-10", False, eps(0.1, 0.2, 0.3, 0.4, 0.9, 0.9, 0.9)),
                    rec("e3", "2026-01-25", True, eps(0.6, 0.6, 0.6, 0.4, 0.4, 0.4, 0.5))]


# --- the perspectives read ----------------------------------------------------------

def _never_read():
    raise AssertionError("a record was read")
    yield


@pytest.mark.parametrize("bad", ["growth", ("growth", "labor"), [], ["growth", "growth"],
                                 ["growth", 1], [None], {"growth": 1.0}, 7],
                         ids=["string", "tuple", "empty", "repeated", "not-a-string",
                              "none-item", "dict", "number"])
def test_bad_perspectives_raise_before_any_record_is_read(bad):
    for run in (lambda: replay(_never_read(), perspectives=bad),
                lambda: replay(_never_read(), "leader", perspectives=bad),
                lambda: agreement(_never_read(), perspectives=bad)):
        with pytest.raises(ValueError) as info:
            run()
        assert "perspectives" in str(info.value)


def test_none_and_the_earnings_set_are_the_default_exactly():
    for names in (list(PERSPECTIVES), perspective_set("earnings")):
        for rule in RULES:
            assert replay(EARNINGS_RECORDS, rule, perspectives=names) == \
                replay(EARNINGS_RECORDS, rule)
            assert replay(EARNINGS_RECORDS, rule, perspectives=None) == \
                replay(EARNINGS_RECORDS, rule)
        assert agreement(EARNINGS_RECORDS, perspectives=names) == agreement(EARNINGS_RECORDS)
    assert list(replay(EARNINGS_RECORDS)["cases"][0]) == CASE_KEYS


def test_macro_records_need_the_macro_perspectives():
    for run in (lambda: replay(SHUFFLED), lambda: agreement(SHUFFLED)):
        with pytest.raises(ValueError) as info:
            run()
        message = str(info.value)
        # The first scored record given is checked first; growth is in both sets.
        assert "s6/spx:1d" in message
        for name in PERSPECTIVES[1:]:
            assert name in message
    assert replay(SHUFFLED, perspectives=MACRO)["n"] == 6
    probs = {k: v for k, v in M3["prob_by_perspective"].items() if k != "global_fx"}
    for run in (replay, agreement):
        with pytest.raises(ValueError) as info:
            run([M1, dict(M3, prob_by_perspective=probs)], perspectives=MACRO)
        assert "s3/ust10y:1w" in str(info.value) and "global_fx" in str(info.value)


def test_other_probabilities_in_a_record_are_ignored():
    r1 = rec("r1", "2026-03-02", True, {"a": 0.2, "b": 0.9, "c": 0.4, "d": 0.0})
    r2 = rec("r2", "2026-03-03", False, {"d": 1.0, "b": 0.3, "a": 0.6})  # no c: not read
    out = replay([r1, r2], "uniform", perspectives=["a", "b"])
    assert [c["prob"] for c in out["cases"]] == [pytest.approx(0.55), pytest.approx(0.45)]
    # The median of a, b and c; d takes no part.
    assert replay([r1], "median", perspectives=["a", "b", "c"])["cases"][0]["prob"] == \
        pytest.approx(0.4)
    # pool: the log-odds of a and b added, ln 4 + ln 4, is 16/17; c and d take no part.
    r3 = rec("r3", "2026-03-04", True, {"a": 0.8, "b": 0.8, "c": 0.1, "d": 0.0})
    assert replay([r3], "pool", perspectives=["a", "b"])["cases"][0]["prob"] == \
        pytest.approx(16 / 17)


def test_hedge_weights_start_at_one_over_n():
    h = rec("h", "2026-06-01", True, {"a": 0.9, "b": 0.6, "c": 0.0, "d": 0.7})
    # 1/3 each over a, b and c: the plain mean of the three.
    assert replay([h], "hedge", perspectives=["a", "b", "c"])["cases"][0]["prob"] == \
        pytest.approx(0.5)


def test_leader_ties_go_to_the_first_perspective_in_the_order_given():
    l1 = rec("l1", "2026-06-01", True, {"a": 0.8, "b": 0.8})  # a and b tie at 0.04
    l2 = rec("l2", "2026-06-02", True, {"a": 0.3, "b": 0.6})
    out = replay([l1, l2], "leader", perspectives=["a", "b"])
    assert [c["prob"] for c in out["cases"]] == [pytest.approx(0.8), pytest.approx(0.3)]
    out = replay([l1, l2], "leader", perspectives=["b", "a"])
    assert [c["prob"] for c in out["cases"]] == [pytest.approx(0.8), pytest.approx(0.6)]


# --- learning by resolution date ----------------------------------------------------

def test_macro_leader_by_hand():
    out = replay(SHUFFLED, "leader", perspectives=MACRO)
    assert (out["rule"], out["eta"], out["n"]) == ("leader", None, 6)
    assert [c["case_id"] for c in out["cases"]] == [r["case_id"] for r in MACRO_RECORDS]
    # s1, s2 and s3 are forecast before anything has resolved: the plain mean of the ten.
    # s2 resolves on s3's date, so only after s3 is forecast; s3 resolves on 01-21, before
    # s4's batch. Mean Brier then: growth (0.25 + 0.36) / 2, inflation (0.04 + 0.25) / 2,
    # labor (0.25 + 0.09) / 2, the rest 0.25: inflation leads s4. s4 resolves on 01-30,
    # before s5's batch: growth 0.6125 / 3, inflation 0.38 / 3, labor 0.98 / 3: inflation
    # leads s5. s1 resolves on s5's date, after s5 is forecast: growth 0.6225 / 4 against
    # inflation 0.63 / 4, so growth leads s6. s5 resolves after the last batch.
    assert [c["prob"] for c in out["cases"]] == [pytest.approx(p) for p in MACRO_LEADER_PROBS]
    assert [c["brier_blend"] for c in out["cases"]] == [
        pytest.approx(b) for b in (0.2116, 0.2209, 0.2401, 0.09, 0.2025, 0.5625)]
    assert out["brier_blend"] == pytest.approx(1.5276 / 6)
    # Learning from each record right after its own batch would let s2, forecast on 01-09,
    # use the outcome of s1, known only on 02-02: growth would lead it.
    v11 = replay([undated(r) for r in SHUFFLED], "leader", perspectives=MACRO)
    assert [c["prob"] for c in v11["cases"]][:2] == [pytest.approx(0.54), pytest.approx(0.5)]


H1 = rec("h1", "2026-03-02", True, {"a": 0.9, "b": 0.3, "c": 0.0}, "2026-03-20")
H2 = rec("h2", "2026-03-02", False, {"a": 0.6, "b": 0.2, "c": 1.0}, "2026-03-04")
H3 = rec("h3", "2026-03-05", True, {"a": 0.8, "b": 0.5})
H4 = rec("h4", "2026-03-20", False, {"a": 0.7, "b": 0.1, "c": 0.5}, "2026-03-27")
H5 = rec("h5", "2026-03-25", True, {"b": 0.6, "a": 0.2}, "2026-04-25")


def test_hedge_learns_from_a_record_once_it_has_resolved():
    out = replay([H5, H1, H4, H2, H3], "hedge", eta=1.0, perspectives=["a", "b"])
    assert [c["case_id"] for c in out["cases"]] == ["h1", "h2", "h3", "h4", "h5"]
    probs = [c["prob"] for c in out["cases"]]
    # h1 and h2 share the first batch: the starting weights, 1/2 each.
    assert probs[:2] == [pytest.approx(0.6), pytest.approx(0.4)]
    # h2 resolved on 03-04, before h3's batch: learned first. With eta 1 the weights are
    # proportional to exp(-loss): a lost 0.36, b 0.04. h1 resolves only on 03-20.
    assert probs[2] == pytest.approx(weighted(ab(H3), math.exp(-0.36), math.exp(-0.04)))
    # h3 resolves on its event date: learned right after its batch (a 0.04, b 0.25). h1
    # resolves on h4's date, so h4 is forecast without it.
    assert probs[3] == pytest.approx(weighted(ab(H4), math.exp(-0.40), math.exp(-0.29)))
    # After h4's batch h1 has resolved (a 0.01, b 0.49); h4 resolves after h5's date.
    assert probs[4] == pytest.approx(weighted(ab(H5), math.exp(-0.41), math.exp(-0.78)))
    # The same steps with the shared helpers, exactly; h4 and h5 are never learned from.
    w0 = {"a": 0.5, "b": 0.5}
    w1 = hedge_update(w0, losses(H2), 1.0)
    w2 = hedge_update(w1, losses(H3), 1.0)
    w3 = hedge_update(w2, losses(H1), 1.0)
    assert probs == [combine_probabilities(ab(H1), w0), combine_probabilities(ab(H2), w0),
                     combine_probabilities(ab(H3), w1), combine_probabilities(ab(H4), w2),
                     combine_probabilities(ab(H5), w3)]
    ys = [1.0, 0.0, 1.0, 0.0, 1.0]
    assert out["brier_blend"] == pytest.approx(statistics.fmean(
        (p - y) ** 2 for p, y in zip(probs, ys)))
    # uniform takes no account of dates or of c.
    assert [c["prob"] for c in replay([H5, H1, H4, H2, H3], "uniform",
                                      perspectives=["a", "b"])["cases"]] == \
        [pytest.approx(p) for p in (0.6, 0.4, 0.65, 0.4, 0.4)]


def test_learning_goes_by_resolution_date_ties_in_replay_order():
    t1 = rec("t1", "2026-05-01", True, {"a": 0.9, "b": 0.4}, "2026-05-12")
    t2 = rec("t2", "2026-05-04", False, {"a": 0.3, "b": 0.7}, "2026-05-08")
    t3 = rec("t3", "2026-05-06", True, {"a": 0.6, "b": 0.45}, "2026-05-12")
    t4 = rec("t4", "2026-05-15", True, {"a": 0.55, "b": 0.35})
    out = replay([t4, t3, t2, t1], "hedge", perspectives=["a", "b"])
    probs = [c["prob"] for c in out["cases"]]
    # Nothing resolves before 05-08: t1, t2 and t3 are forecast with the starting weights.
    assert probs[:3] == [pytest.approx(0.65), pytest.approx(0.5), pytest.approx(0.525)]
    # Before t4's batch all three have resolved: t2 (05-08) first, then t1 and t3, which
    # resolve on the same day, in replay order.
    w = {"a": 0.5, "b": 0.5}
    for r in (t2, t1, t3):
        w = hedge_update(w, losses(r), 0.5)
    assert probs[3] == combine_probabilities(ab(t4), w)


@pytest.mark.parametrize("rule", RULES)
def test_resolving_on_the_event_date_is_the_v11_rule_exactly(rule):
    before = replay([undated(r) for r in SHUFFLED], rule, perspectives=MACRO)
    same_day = [dict(r, resolution_date=r["event_date"]) for r in SHUFFLED]
    out = replay(same_day, rule, perspectives=MACRO)
    assert [undated(c) for c in out["cases"]] == before["cases"]
    assert out["brier_blend"] == before["brier_blend"]
    assert all(c["resolution_date"] == c["event_date"] for c in out["cases"])


def test_a_replayed_case_carries_its_resolution_date():
    out = replay(SHUFFLED, "uniform", perspectives=MACRO)
    assert list(out) == ["rule", "eta", "n", "brier_blend", "cases"]
    assert [list(c) for c in out["cases"]] == [DATED_KEYS] * 5 + [CASE_KEYS]
    assert [c.get("resolution_date") for c in out["cases"]] == \
        ["2026-02-02", "2026-01-16", "2026-01-21", "2026-01-30", "2026-02-09", None]
    # Read as parse_iso_date reads it, given back as YYYY-MM-DD.
    spaced = dict(M1, resolution_date=" 2026-02-02 ")
    assert replay([spaced], perspectives=MACRO)["cases"][0]["resolution_date"] == "2026-02-02"


@pytest.mark.parametrize("value", [None, 20260202, datetime.date(2026, 2, 2), ["2026-02-02"],
                                   "", "soon", "2026-02-30", "2026-01-01"],
                         ids=["null", "number", "date-object", "list", "empty", "text",
                              "no-such-day", "before-event-date"])
def test_a_bad_resolution_date_is_refused_before_anything_is_replayed(value):
    records = [M2, M3, dict(M1, resolution_date=value)]  # M1's event date is 2026-01-02
    for run in (lambda: replay(records, perspectives=MACRO),
                lambda: replay(records, "pool", perspectives=MACRO),
                lambda: agreement(records, perspectives=MACRO)):
        with pytest.raises(ValueError) as info:
            run()
        assert "s1/ust10y:1m" in str(info.value) and "resolution_date" in str(info.value)


def test_records_are_not_modified():
    records = copy.deepcopy(SHUFFLED)
    for rule in RULES:
        replay(records, rule, perspectives=MACRO)
    agreement(records, perspectives=MACRO)
    assert records == SHUFFLED


# --- agreement ----------------------------------------------------------------------

def test_agreement_over_the_macro_perspectives():
    out = agreement(SHUFFLED, perspectives=MACRO)
    assert out["n"] == 6
    assert list(out["perspectives"]) == MACRO
    assert len(out["pairs"]) == 45  # 10 * 9 / 2
    assert [(p["a"], p["b"]) for p in out["pairs"]] == list(combinations(MACRO, 2))
    growth = out["perspectives"]["growth"]
    assert growth["mean_prob"] == pytest.approx(4.35 / 6)
    assert growth["brier"] == pytest.approx(1.3075 / 6)
    # 0.5 on s2 is no call; s1, s4 and s5 went up as called, s3 and s6 did not.
    assert (growth["calls"], growth["hits"]) == (5, 3)
    assert out["perspectives"]["global_fx"] == {"mean_prob": pytest.approx(0.5), "sd_prob": 0.0,
                                                "brier": pytest.approx(0.25), "calls": 0,
                                                "hits": 0}
    corr = {(p["a"], p["b"]): p["correlation"] for p in out["pairs"]}
    assert [pair for pair, value in corr.items() if value is not None] == \
        [("growth", "inflation"), ("growth", "labor"), ("inflation", "labor")]
    series = {name: [r["prob_by_perspective"][name] for r in SHUFFLED]
              for name in ("growth", "inflation")}
    assert corr[("growth", "inflation")] == \
        pytest.approx(statistics.correlation(series["growth"], series["inflation"]))
    found = [v for v in corr.values() if v is not None]
    assert out["mean_correlation"] == pytest.approx(statistics.fmean(found))
    assert out["min_correlation"] == min(found)
    # Dates take no part.
    assert agreement([undated(r) for r in SHUFFLED], perspectives=MACRO) == out


def test_agreement_in_the_order_of_the_perspectives_given():
    r1 = rec("r1", "2026-03-02", True, {"a": 0.8, "b": 0.4, "c": 0.9})
    r2 = rec("r2", "2026-03-03", False, {"a": 0.3, "b": 0.6})
    out = agreement([r1, r2], perspectives=["b", "a"])
    assert list(out["perspectives"]) == ["b", "a"]
    assert [(p["a"], p["b"]) for p in out["pairs"]] == [("b", "a")]
    assert out["pairs"][0]["correlation"] == pytest.approx(-1.0)
    assert out["perspectives"]["a"]["mean_prob"] == pytest.approx(0.55)
    assert (out["perspectives"]["a"]["calls"], out["perspectives"]["a"]["hits"]) == (2, 2)
    assert (out["perspectives"]["b"]["calls"], out["perspectives"]["b"]["hits"]) == (2, 0)


# --- CLI ----------------------------------------------------------------------------

def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def as_json(data):
    return json.loads(json.dumps(data))


def macro_files(tmp_path):
    first = write(tmp_path / "first.json", SHUFFLED[:3])
    rest = write(tmp_path / "rest.json",
                 {"result": {"n": 3, "brier_judge": 0.2, "cases": SHUFFLED[3:]}})
    return first, rest


def test_cli_blend_and_agreement_read_the_macro_perspectives(tmp_path, capsys):
    first, rest = macro_files(tmp_path)
    assert main(["blend", first, rest, "--rule", "leader", "--perspectives", "macro"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got == as_json(replay(SHUFFLED, "leader", perspectives=MACRO))
    assert [c["prob"] for c in got["cases"]] == [pytest.approx(p) for p in MACRO_LEADER_PROBS]
    assert got["cases"][0]["resolution_date"] == "2026-02-02"
    # The option before the files, and a file after it.
    assert main(["blend", "--perspectives", "macro", first, rest]) == 0
    assert json.loads(capsys.readouterr().out) == as_json(replay(SHUFFLED, perspectives=MACRO))
    assert main(["agreement", first, "--perspectives", "macro", rest]) == 0
    assert json.loads(capsys.readouterr().out) == \
        as_json(agreement(SHUFFLED, perspectives=MACRO))
    out = tmp_path / "agreement.json"
    assert main(["agreement", first, rest, "--perspectives", "macro", "--out", str(out)]) == 0
    assert capsys.readouterr().out == ""
    assert json.loads(out.read_text(encoding="utf-8"))["perspectives"].keys() == set(MACRO)


@pytest.mark.parametrize("command", ["blend", "agreement"])
def test_cli_a_macro_record_read_with_other_perspectives_exits_1(tmp_path, capsys, command):
    first, rest = macro_files(tmp_path)
    for extra in ([], ["--perspectives", "earnings"]):
        assert main([command, first, rest, *extra]) == 1
        captured = capsys.readouterr()
        assert captured.out == "" and "s6/spx:1d" in captured.err
    bad = write(tmp_path / "bad.json", [dict(M1, resolution_date="soon")])
    assert main([command, bad, "--perspectives", "macro"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "resolution_date" in captured.err


@pytest.mark.parametrize("command", ["blend", "agreement"])
@pytest.mark.parametrize("args", [["--perspectives", "rates"], ["--perspectives", "MACRO"],
                                  ["--perspectives", ""], ["--perspectives"]],
                         ids=["unknown", "upper-case", "empty", "no-value"])
def test_cli_bad_perspectives_exit_2(tmp_path, capsys, command, args):
    first, _ = macro_files(tmp_path)
    assert main([command, first, *args]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.strip()


def test_cli_passes_perspectives_on_only_when_given(tmp_path, capsys, monkeypatch):
    calls = []

    def fake_replay(records, rule, **kwargs):
        calls.append(("blend", rule, kwargs))
        return {"n": len(records)}

    def fake_agreement(records, **kwargs):
        calls.append(("agreement", kwargs))
        return {"n": len(records)}

    monkeypatch.setattr(main_mod, "replay", fake_replay)
    monkeypatch.setattr(main_mod, "agreement", fake_agreement)
    f = write(tmp_path / "earnings.json", EARNINGS_RECORDS)
    assert main(["blend", f]) == 0
    assert main(["blend", f, "--rule", "median", "--perspectives", "macro"]) == 0
    assert main(["agreement", f]) == 0
    assert main(["agreement", f, "--perspectives", "earnings"]) == 0
    capsys.readouterr()
    assert calls == [("blend", "hedge", {"eta": 0.5}),
                     ("blend", "median", {"eta": 0.5, "perspectives": MACRO}),
                     ("agreement", {}),
                     ("agreement", {"perspectives": list(PERSPECTIVES)})]


def test_cli_earnings_option_changes_nothing(tmp_path, capsys):
    f = write(tmp_path / "earnings.json", EARNINGS_RECORDS)
    for command in ("blend", "agreement"):
        assert main([command, f]) == 0
        without = capsys.readouterr().out
        assert main([command, f, "--perspectives", "earnings"]) == 0
        assert capsys.readouterr().out == without
