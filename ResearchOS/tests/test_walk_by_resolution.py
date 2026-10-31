"""Walk forward by resolution date (SPEC.md v30): playbook.learned_through and
playbook.update_weights_probs, the macro run of loop.train, and the train command."""

import builtins
import io
import json
import math
import re
from datetime import date
from pathlib import Path

import pytest
from tools.dates import horizon_end, parse_iso_date
from tools.stats import hedge_update

from conftest import FakeClient, FakeLLM, make_case, make_outcome, make_roles, text_response

from researchos import loop as loop_mod
from researchos.__main__ import main
from researchos.cases import (Document, Outcome, Target, TargetOutcome, load_outcome, macro_case,
                              resolution_manifest, save_case, save_outcome, save_resolution,
                              target_keys)
from researchos.jsonio import read_json
from researchos.llm import DEFAULT_MODEL
from researchos.loop import evaluate, forecast, load_prediction, train
from researchos.masking import MaskConfig
from researchos.perspectives import View
from researchos.playbook import (Playbook, learned_through, load_playbook, save_playbook,
                                 update_weights, update_weights_probs)
from researchos.targets import MACRO_PERSPECTIVES, score_targets

CUTOFF = "2026-06-30"
KEYS = ["DGS10_1w", "DGS10_1m", "SP500_1w"]

# Snapshots C and D share a date. A key resolves a week (1w) or a month (1m) after its
# snapshot's date: A's 1w keys on B's date, B's 1w keys before the date of C and D.
A, B, C, D = "s-1030", "s-1106", "s-1120a", "s-1120b"
DATES = {A: "2026-10-30", B: "2026-11-06", C: "2026-11-20", D: "2026-11-20"}
TAGS = {A: "amber", B: "birch", C: "cedar", D: "dune"}

# growth leans up and inflation down on every key; the other eight perspectives say 0.50.
LEANS = {"growth": 0.8, "inflation": 0.3}

# The walk of train(root, [B, D, A, C], ...), worked out by hand. By event date the
# snapshots are A, B, then the batch D, C (a tie keeps the order given).
# - A, 2026-10-30: nothing waits. A is sealed; its keys join (1w on 11-06, 1m on 11-30).
# - B, 2026-11-06: no key resolved before 11-06, so B is forecast having learned nothing.
#   B is sealed (1w on 11-13, 1m on 12-06); then A's 1w keys, resolved on 11-06, are
#   learned from, and A's 1w horizon is reflected on.
# - D and C, 2026-11-20: first B's 1w keys (11-13 < 11-20); then D and C are sealed with
#   the same playbook (1w on 11-27, 1m on 12-20). No key resolves on 11-20.
# - After the last batch, every key still waiting, by resolution date, ties in the order
#   they joined: the 1w keys of D and C (11-27), A's 1m (11-30), B's 1m (12-06), and the
#   1m keys of D and C (12-20).
LEARNED = [(A, "DGS10_1w", "2026-11-06"), (A, "SP500_1w", "2026-11-06"),
           (B, "DGS10_1w", "2026-11-13"), (B, "SP500_1w", "2026-11-13"),
           (D, "DGS10_1w", "2026-11-27"), (D, "SP500_1w", "2026-11-27"),
           (C, "DGS10_1w", "2026-11-27"), (C, "SP500_1w", "2026-11-27"),
           (A, "DGS10_1m", "2026-11-30"), (B, "DGS10_1m", "2026-12-06"),
           (D, "DGS10_1m", "2026-12-20"), (C, "DGS10_1m", "2026-12-20")]
# One reflection per case and horizon, once the last key of that horizon is learned from.
REFLECTIONS = [(A, ["DGS10_1w", "SP500_1w"]), (B, ["DGS10_1w", "SP500_1w"]),
               (D, ["DGS10_1w", "SP500_1w"]), (C, ["DGS10_1w", "SP500_1w"]),
               (A, ["DGS10_1m"]), (B, ["DGS10_1m"]), (D, ["DGS10_1m"]), (C, ["DGS10_1m"])]


def snapshot(case_id, as_of=None):
    as_of = as_of or DATES[case_id]
    targets = [Target(id="DGS10", name="10-year Treasury yield", kind="bp",
                      horizons=["1w", "1m"], as_of_date=as_of, as_of_value=4.12),
               Target(id="SP500", name="S&P 500", kind="return", horizons=["1w"],
                      as_of_date=as_of, as_of_value=6012.5)]
    note = f"Desk note: {TAGS.get(case_id, 'plain')}. Yields rose on the week."
    return macro_case(case_id, as_of, targets, [Document("markets", "Desk notes", note)])


def outcome_of(case, skip=()):
    """Every key of the case resolved but those in skip: the yield up, the S&P 500 down."""
    start = parse_iso_date(case.event_date)
    found = {}
    for target in case.targets:
        up = target.id == "DGS10"
        for horizon in target.horizons:
            key = f"{target.id}_{horizon}"
            if key in skip:
                continue
            found[key] = TargetOutcome(
                target.id, horizon, target.kind, case.event_date, target.as_of_value,
                horizon_end(start, horizon).isoformat(),
                target.as_of_value + (0.05 if up else -60.0), 5.0 if up else -0.01, up)
    return Outcome(targets=found)


def walk_root(tmp_path, cases):
    """Each case with its outcome and a manifest marking every key resolved."""
    root = tmp_path / "cases"
    for case in cases:
        save_case(root, case)
        save_outcome(root, case.case_id, outcome_of(case))
        save_resolution(root, case.case_id, resolution_manifest(case, resolved=target_keys(case)))
    return root


def _forecasts(schema, p):
    targets = schema["properties"]["forecasts"]["properties"]
    return {target: {name: p for name in props["properties"]} for target, props in targets.items()}


def analyst_fn(system, prompt, schema, prefix):
    name = prompt.splitlines()[0].split(": ", 1)[1]
    return {"forecasts": _forecasts(schema, LEANS.get(name, 0.5)), "key_points": [f"{name} point"]}


def judge_fn(system, prompt, schema, prefix):
    return {"forecasts": _forecasts(schema, 0.6), "thesis": "Rates drift up."}


def walk_roles():
    """The macro agents; the reflector's n-th call teaches the lesson "Lesson n"."""
    reflector = FakeLLM()

    def reflect_fn(system, prompt, schema, prefix):
        lesson = {"lesson": f"Lesson {len(reflector.calls)}", "applies_when": "always",
                  "perspective": "growth"}
        return {"why_market_moved": "", "where_we_were_wrong": "", "lessons": [lesson]}

    reflector.fn = reflect_fn
    analyst = FakeLLM(analyst_fn)
    analyst.model = "fake-model"
    return make_roles(analyst=analyst, judge=FakeLLM(judge_fn), reflector=reflector)


def agents(roles):
    return (roles.masker, roles.guesser, roles.analyst, roles.judge, roles.reflector)


def assert_nothing_done(root, roles, pb_path, case_ids):
    assert not any(agent.calls for agent in agents(roles))
    assert not Path(pb_path).exists()
    for case_id in case_ids:
        assert not (root / case_id / "prediction.json").exists()
        assert not (root / case_id / "masked.json").exists()


def history_entry(record):
    """A score_targets record as train appends it to the history."""
    return {"case_id": record["case_id"], "split": "train",
            **{name: value for name, value in record.items() if name != "case_id"}}


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


@pytest.fixture
def watch(monkeypatch):
    """Each forecast with the playbook it was made with, each outcome opened with the
    forecasts sealed by then, and the history's length at each save of the playbook."""
    seen = {"forecasts": [], "opens": [], "saves": []}
    real_analyze = loop_mod.analyze_targets
    real_open = loop_mod.load_outcome
    real_save = loop_mod.save_playbook

    def analyze(masked, case, playbook, analyst, judge, **options):
        seen["forecasts"].append({"case": case.case_id, "status": masked.status,
                                  "options": options,
                                  "learned": [r["case_id"] for r in playbook.history],
                                  "lessons": [lesson.text for lesson in playbook.lessons],
                                  "weights": dict(playbook.weights)})
        return real_analyze(masked, case, playbook, analyst, judge, **options)

    def open_outcome(root, case_id):
        sealed = sorted(path.parent.name for path in Path(root).glob("*/prediction.json"))
        seen["opens"].append((case_id, sealed))
        return real_open(root, case_id)

    def save(path, playbook):
        seen["saves"].append(len(playbook.history))
        real_save(path, playbook)

    monkeypatch.setattr(loop_mod, "analyze_targets", analyze)
    monkeypatch.setattr(loop_mod, "load_outcome", open_outcome)
    monkeypatch.setattr(loop_mod, "save_playbook", save)
    return seen


# ---- researchos.playbook ------------------------------------------------------------------

def test_learned_through_takes_a_records_resolution_date_when_it_has_one():
    pb = Playbook.initial(MACRO_PERSPECTIVES)
    pb.history = [{"case_id": "s/DGS10_1m", "event_date": "2026-10-30",
                   "resolution_date": "2026-11-30"},
                  {"case_id": "t/DGS10_1w", "event_date": "2026-11-20",
                   "resolution_date": "2026-11-27"}]
    assert learned_through(pb) == date(2026, 11, 30)
    # a resolution_date of None: the event date counts
    pb.history.append({"case_id": "u", "event_date": "2026-12-01", "resolution_date": None})
    assert learned_through(pb) == date(2026, 12, 1)
    pb.history.append({"case_id": "v", "resolution_date": "2026-12-24"})  # no event date
    assert learned_through(pb) == date(2026, 12, 24)
    # skipped records and records with neither date do not count
    pb.history += [{"case_id": "w", "event_date": "2027-01-04", "resolution_date": "2027-02-04",
                    "skipped": "identifiable"},
                   {"case_id": "x", "resolution_date": None}, {"case_id": "y"}]
    assert learned_through(pb) == date(2026, 12, 24)


def test_learned_through_of_earnings_records_is_unchanged():
    pb = Playbook.initial()
    pb.history = [{"case_id": "a", "split": "train", "event_date": "2026-02-01"},
                  {"case_id": "b", "split": "train", "event_date": "2025-11-01"}]
    assert learned_through(pb) == date(2026, 2, 1)
    assert learned_through(Playbook.initial()) is None


@pytest.mark.parametrize("record, field", [
    ({"case_id": "s", "event_date": "2026-10-30", "resolution_date": "end of November"},
     "resolution_date"),
    ({"case_id": "s", "event_date": "soon", "resolution_date": None}, "event_date"),
])
def test_learned_through_refuses_a_date_that_is_not_an_iso_date(record, field):
    pb = Playbook.initial(MACRO_PERSPECTIVES)
    pb.history = [record]
    with pytest.raises(ValueError, match="not an ISO date") as err:
        learned_through(pb)
    assert "'s'" in str(err.value) and field in str(err.value)


def test_update_weights_probs_is_the_hedge_update_from_bare_probabilities():
    pb = Playbook(weights={"a": 0.5, "b": 0.5})
    update_weights_probs(pb, {"a": 0.9, "b": 0.3}, True)
    # losses (0.9 - 1) ** 2 = 0.01 and (0.3 - 1) ** 2 = 0.49; each weight times
    # exp(-0.5 * loss), then normalized
    wa, wb = math.exp(-0.5 * 0.01), math.exp(-0.5 * 0.49)
    assert pb.weights == pytest.approx({"a": wa / (wa + wb), "b": wb / (wa + wb)})
    assert pb.weights["a"] == pytest.approx(0.5597, abs=1e-4)
    assert pb.lessons == [] and pb.history == []
    # update_weights on views holding the same probabilities gives the same weights
    by_probs = Playbook(weights={"a": 0.2, "b": 0.8})
    by_views = Playbook(weights={"a": 0.2, "b": 0.8})
    update_weights_probs(by_probs, {"a": 0.9, "b": 0.3}, False, eta=2.0)
    update_weights(by_views, {"a": View("bullish", 0.9, "high", []),
                              "b": View("bearish", 0.3, "low", [])}, False, eta=2.0)
    assert by_probs.weights == by_views.weights
    assert by_probs.weights != {"a": 0.2, "b": 0.8}


# ---- the walk ------------------------------------------------------------------------------

def test_train_walks_macro_snapshots_forward_by_resolution_date(tmp_path, watch):
    cases = {case_id: snapshot(case_id) for case_id in (A, B, C, D)}
    root = walk_root(tmp_path, cases.values())
    pb_path = tmp_path / "pb.json"
    roles = walk_roles()
    results = train(root, [B, D, A, C], pb_path, roles, knowledge_cutoff=CUTOFF)

    # Each forecast learned from exactly the keys resolved before its date.
    early = [f"{A}/DGS10_1w", f"{A}/SP500_1w", f"{B}/DGS10_1w", f"{B}/SP500_1w"]
    assert [(f["case"], f["learned"]) for f in watch["forecasts"]] == [
        (A, []), (B, []), (D, early), (C, early)]
    assert [f["lessons"] for f in watch["forecasts"]] == [
        [], [], ["Lesson 1", "Lesson 2"], ["Lesson 1", "Lesson 2"]]
    initial = {name: 0.1 for name in MACRO_PERSPECTIVES}
    assert watch["forecasts"][1]["weights"] == pytest.approx(initial)
    assert watch["forecasts"][2]["weights"] == watch["forecasts"][3]["weights"]
    assert all(f["status"] == "unmasked"
               and f["options"] == {"perspectives": MACRO_PERSPECTIVES, "mode": "holistic"}
               for f in watch["forecasts"])

    # The history: one record per key, in the order learned, as score_targets scores it,
    # "split" after "case_id"; the playbook saved after each key.
    assert [(r["case"], r["key"], r["resolution_date"]) for r in results] == LEARNED
    scored = {case_id: {r["key"]: r for r in score_targets(load_prediction(root, case_id),
                                                           load_outcome(root, case_id), case)}
              for case_id, case in cases.items()}
    expected = [history_entry(scored[case_id][key]) for case_id, key, _ in LEARNED]
    assert results == expected
    assert [list(r) for r in results] == [list(e) for e in expected]
    assert list(results[0])[:4] == ["case_id", "split", "case", "key"]
    pb = load_playbook(pb_path)
    assert pb.history == results
    assert watch["saves"] == list(range(1, 13))
    assert learned_through(pb) == date(2026, 12, 20)

    # The weights: one Hedge update per key, in that order.
    weights, before = dict(initial), []
    for record in results:
        before.append(weights)
        y = 1.0 if record["actual_up"] else 0.0
        losses = {name: (p - y) ** 2 for name, p in record["prob_by_perspective"].items()}
        weights = hedge_update(weights, losses, 0.5)
    assert pb.weights == pytest.approx(weights)
    assert watch["forecasts"][2]["weights"] == pytest.approx(before[4])

    # One reflection per case and horizon, with that horizon's keys.
    tag_case = {tag: case_id for case_id, tag in TAGS.items()}
    assert [(tag_case[re.search(r"Desk note: (\w+)", call["prompt"]).group(1)],
             re.findall(r"^### (\S+) \(", call["prompt"], re.M))
            for call in roles.reflector.calls] == REFLECTIONS
    assert [lesson.text for lesson in pb.lessons] == [f"Lesson {n}" for n in range(1, 9)]
    assert [lesson.case_id for lesson in pb.lessons] == [A, B, D, C, A, B, D, C]

    # Each outcome is opened once, when its first key is learned from, after it is sealed:
    # A's only once B is sealed too, B's before D and C are forecast.
    assert watch["opens"] == [(A, [A, B]), (B, [A, B]), (D, [A, B, C, D]), (C, [A, B, C, D])]
    assert not roles.masker.calls and not roles.guesser.calls
    assert not any((root / case_id / "masked.json").exists() for case_id in cases)


@pytest.mark.parametrize("mode, model, recorded", [("holistic", "fake-model", "fake-model"),
                                                   ("isolated", 5, "")])
def test_a_macro_run_seals_each_forecast_as_forecast_does(tmp_path, opened, mode, model,
                                                          recorded):
    case = snapshot(A)
    root = walk_root(tmp_path, [case])
    roles = walk_roles()
    roles.analyst.model = model
    opened.clear()
    # seed and mask_config play no part in a macro run: nothing is masked
    train(root, [A], tmp_path / "pb.json", roles, seed=7, mask_config=MaskConfig(max_rounds=1),
          knowledge_cutoff=CUTOFF, perspective_mode=mode)
    assert "masked.json" not in opened and "mask_key.json" not in opened
    assert not (root / A / "masked.json").exists() and not (root / A / "mask_key.json").exists()
    assert not roles.masker.calls and not roles.guesser.calls
    assert len(roles.analyst.calls) == len(MACRO_PERSPECTIVES) and len(roles.judge.calls) == 1
    sealed = read_json(root / A / "prediction.json")
    assert sealed["case_id"] == A and sealed["mode"] == mode and sealed["keys"] == KEYS
    assert sealed["model"] == recorded and sealed["knowledge_cutoff"] == CUTOFF
    # A, the first snapshot, was forecast with the initial playbook: what forecast seals
    other = tmp_path / "other"
    save_case(other, case)
    again = walk_roles()
    again.analyst.model = model
    forecast(other, [A], tmp_path / "pb2.json", again, knowledge_cutoff=CUTOFF,
             perspective_mode=mode)
    assert read_json(other / A / "prediction.json") == sealed
    assert [call["prompt"] for call in again.analyst.calls] == [
        call["prompt"] for call in roles.analyst.calls]


def test_a_later_run_takes_only_snapshots_after_the_latest_resolution_date(tmp_path):
    E, F = "s-1130", "s-1204"
    root = walk_root(tmp_path, [snapshot(A), snapshot(C), snapshot(E, "2026-11-30"),
                                snapshot(F, "2026-12-04")])
    pb_path = tmp_path / "pb.json"
    first = train(root, [A], pb_path, walk_roles(), knowledge_cutoff=CUTOFF)
    assert [r["case_id"] for r in first] == [f"{A}/DGS10_1w", f"{A}/SP500_1w", f"{A}/DGS10_1m"]
    # every key is learned from, however late: through A's 1m key, a month after A's date
    assert learned_through(load_playbook(pb_path)) == date(2026, 11, 30)
    before = pb_path.read_text(encoding="utf-8")
    roles = walk_roles()
    with pytest.raises(ValueError) as err:
        train(root, [C, E, F], pb_path, roles, knowledge_cutoff=CUTOFF)
    message = str(err.value)
    assert message.startswith("train refused: ") and "2026-11-30" in message
    assert f"case {C}" in message and f"case {E}" in message and f"case {F}" not in message
    assert pb_path.read_text(encoding="utf-8") == before and not roles.analyst.calls
    later = train(root, [F], pb_path, roles, knowledge_cutoff=CUTOFF)
    assert [r["case_id"] for r in later] == [f"{F}/DGS10_1w", f"{F}/SP500_1w", f"{F}/DGS10_1m"]
    pb = load_playbook(pb_path)
    assert pb.history == first + later  # train returns only the records it appended
    assert learned_through(pb) == date(2027, 1, 4)
    # F was forecast with everything A taught
    assert all("Lesson 2" in call["prompt"] for call in roles.analyst.calls)


def test_an_outcome_that_disagrees_with_the_manifest_stops_the_walk_at_that_key(tmp_path):
    case = snapshot(A)
    root = walk_root(tmp_path, [case])
    save_outcome(root, A, outcome_of(case, skip=["SP500_1w"]))
    pb_path = tmp_path / "pb.json"
    roles = walk_roles()
    with pytest.raises(ValueError) as err:
        train(root, [A], pb_path, roles, knowledge_cutoff=CUTOFF)
    assert A in str(err.value) and "SP500_1w" in str(err.value)
    # DGS10_1w, resolved the same day and joined first, was learned from and saved
    assert [r["case_id"] for r in load_playbook(pb_path).history] == [f"{A}/DGS10_1w"]
    assert not roles.reflector.calls  # the 1w horizon never had all its keys learned
    assert (root / A / "prediction.json").is_file()


# ---- the checks of a macro run, before anything is called or written -----------------------

@pytest.mark.parametrize("bad", [{"perspective_mode": "both"}, {"knowledge_cutoff": "soon"}])
def test_a_macro_run_checks_the_mode_and_the_cutoff_before_loading_a_case(tmp_path, bad):
    root = walk_root(tmp_path, [snapshot(A)])
    pb_path = tmp_path / "pb.json"
    roles = walk_roles()
    with pytest.raises(ValueError):  # not the OSError of the missing case
        train(root, [A, "zzz"], pb_path, roles, **{"knowledge_cutoff": CUTOFF, **bad})
    assert_nothing_done(root, roles, pb_path, [A])


def test_a_macro_run_checks_the_ids_the_domains_and_the_playbook_in_order(tmp_path):
    root = walk_root(tmp_path, [snapshot(A)])
    save_case(root, make_case("e"), make_outcome(ar=0.02))
    pb_path = tmp_path / "pb.json"
    roles = walk_roles()
    # every id is loaded first, then a repeated id is refused, then a run of two domains
    with pytest.raises(OSError):
        train(root, [A, A, "zzz"], pb_path, roles, knowledge_cutoff=CUTOFF)
    with pytest.raises(ValueError, match="more than once") as err:
        train(root, [A, "e", A], pb_path, roles, knowledge_cutoff=CUTOFF)
    assert A in str(err.value)
    with pytest.raises(ValueError, match="more than one domain"):
        train(root, ["e", A], pb_path, roles, knowledge_cutoff=CUTOFF)
    assert_nothing_done(root, roles, pb_path, [A, "e"])
    # a playbook weighing the earnings perspectives belongs to another domain, which is
    # refused before the dates (this one has learned through 2027)
    earnings = Playbook.initial()
    earnings.history = [{"case_id": "x", "split": "train", "event_date": "2027-01-04"}]
    save_playbook(pb_path, earnings)
    before = pb_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="train refused: the playbook") as err:
        train(root, [A], pb_path, roles, knowledge_cutoff=CUTOFF)
    assert str(pb_path) in str(err.value) and "macro" in str(err.value)
    assert pb_path.read_text(encoding="utf-8") == before
    assert not any(agent.calls for agent in agents(roles))
    assert not (root / A / "prediction.json").exists()


def test_a_macro_run_refuses_a_snapshot_on_or_before_what_the_playbook_learned(tmp_path):
    root = walk_root(tmp_path, [snapshot(A), snapshot(B), snapshot(C)])
    pb_path = tmp_path / "pb.json"
    pb = Playbook.initial(MACRO_PERSPECTIVES)
    pb.history = [{"case_id": "s-1023/DGS10_1w", "split": "train", "event_date": "2026-10-23",
                   "resolution_date": "2026-11-06"}]
    save_playbook(pb_path, pb)
    before = pb_path.read_text(encoding="utf-8")
    roles = walk_roles()
    # B is after the record's event date but on its resolution date. With no cutoff
    # either, the dates are refused first, every one of them.
    with pytest.raises(ValueError) as err:
        train(root, [C, A, B], pb_path, roles)
    message = str(err.value)
    assert message.startswith("train refused: ") and "2026-11-06" in message
    assert f"case {A}" in message and f"case {B}" in message and f"case {C}" not in message
    assert pb_path.read_text(encoding="utf-8") == before
    assert not any(agent.calls for agent in agents(roles))
    assert not any((root / case_id / "prediction.json").exists() for case_id in (A, B, C))


@pytest.mark.parametrize("cutoff, ids", [(None, [A, B]), ("2026-10-30", [B, A]),
                                         ("2026-11-05", [B, A])])
def test_a_macro_run_needs_every_date_after_the_knowledge_cutoff(tmp_path, cutoff, ids):
    root = walk_root(tmp_path, [snapshot(A), snapshot(B)])
    pb_path = tmp_path / "pb.json"
    roles = walk_roles()
    with pytest.raises(ValueError, match=A) as err:
        train(root, ids, pb_path, roles, knowledge_cutoff=cutoff)
    assert B not in str(err.value)
    assert_nothing_done(root, roles, pb_path, [A, B])


def test_a_macro_run_needs_every_key_resolved_and_opens_no_outcome(tmp_path, opened):
    cases = [snapshot(A), snapshot(B), snapshot(C), snapshot(D)]
    root = walk_root(tmp_path, cases)
    save_resolution(root, B, resolution_manifest(cases[1], resolved=["DGS10_1w", "SP500_1w"]))
    (root / C / "resolution.json").unlink()
    (root / D / "resolution.json").write_text("[]", encoding="utf-8")  # not a manifest
    pb_path = tmp_path / "pb.json"
    roles = walk_roles()
    opened.clear()
    with pytest.raises(ValueError) as err:
        train(root, [A, B, C, D], pb_path, roles, knowledge_cutoff=CUTOFF)
    message = str(err.value)
    assert message.startswith("train refused: ")
    parts = message[len("train refused: "):].split("; ")
    assert [part.split()[1].rstrip(":") for part in parts] == [B, C, D]
    assert "DGS10_1m" in parts[0] and "DGS10_1w" not in parts[0] and "SP500_1w" not in parts[0]
    assert all(key in part for part in parts[1:] for key in KEYS)
    assert "outcome.json" not in opened
    assert_nothing_done(root, roles, pb_path, [A, B, C, D])


def test_a_macro_run_never_replaces_a_sealed_forecast(tmp_path):
    root = walk_root(tmp_path, [snapshot(A), snapshot(B)])
    (root / B / "prediction.json").write_text("{}", encoding="utf-8")
    pb_path = tmp_path / "pb.json"
    roles = walk_roles()
    with pytest.raises(ValueError, match=B) as err:
        train(root, [A, B], pb_path, roles, knowledge_cutoff=CUTOFF)
    assert "sealed" in str(err.value) and f"case {A}" not in str(err.value)
    assert (root / B / "prediction.json").read_text(encoding="utf-8") == "{}"
    assert_nothing_done(root, roles, pb_path, [A])


def test_a_macro_run_makes_each_check_for_every_case_before_the_next(tmp_path):
    root = walk_root(tmp_path, [snapshot(A), snapshot(B)])
    (root / B / "resolution.json").unlink()
    pb_path = tmp_path / "pb.json"
    roles = walk_roles()
    # B, given first, has not resolved and A is on the cutoff: the cutoff is checked for
    # every case before any manifest
    with pytest.raises(ValueError, match=A) as err:
        train(root, [B, A], pb_path, roles, knowledge_cutoff="2026-10-30")
    assert B not in str(err.value)
    # A's forecast is sealed and B has not resolved: B's keys are refused first
    (root / A / "prediction.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match=B) as err:
        train(root, [A, B], pb_path, roles, knowledge_cutoff=CUTOFF)
    assert "sealed" not in str(err.value) and "DGS10_1m" in str(err.value)
    assert_nothing_done(root, roles, pb_path, [B])


def test_evaluate_still_refuses_a_macro_case(tmp_path):
    root = walk_root(tmp_path, [snapshot(A)])
    pb_path = tmp_path / "pb.json"
    roles = walk_roles()
    with pytest.raises(ValueError, match="macro case") as err:
        evaluate(root, [A], pb_path, roles, knowledge_cutoff=CUTOFF)
    assert A in str(err.value)
    assert_nothing_done(root, roles, pb_path, [A])


# ---- the train command ---------------------------------------------------------------------

def train_responder(kwargs):
    props = kwargs["output_config"]["format"]["schema"]["properties"]
    if "lessons" in props:
        reply = {"why_market_moved": "", "where_we_were_wrong": "",
                 "lessons": [{"lesson": "Trends persist.", "applies_when": "always",
                              "perspective": "general"}]}
    else:
        forecasts = {target: {name: 0.5 for name in schema["properties"]}
                     for target, schema in props["forecasts"]["properties"].items()}
        extra = {"thesis": "Flat."} if "thesis" in props else {"key_points": []}
        reply = {"forecasts": forecasts, **extra}
    return text_response(json.dumps(reply))


def test_cli_train_takes_macro_ids(tmp_path, capsys):
    root = walk_root(tmp_path, [snapshot(A), snapshot(B)])
    pb = tmp_path / "pb.json"
    base = ["train", "--root", str(root), "--playbook", str(pb), "--ids", f"{A},{B}"]
    code = main(base, client_factory=lambda: FakeClient(train_responder))
    captured = capsys.readouterr()
    assert code == 1 and A in captured.err and "knowledge cutoff" in captured.err
    assert captured.out == "" and not pb.exists() and not (root / A / "prediction.json").exists()
    code = main(base + ["--knowledge-cutoff", CUTOFF],
                client_factory=lambda: FakeClient(train_responder))
    captured = capsys.readouterr()
    assert code == 0, captured.err
    printed = json.loads(captured.out)
    assert printed == load_playbook(pb).history
    assert [r["case_id"] for r in printed] == [
        f"{A}/DGS10_1w", f"{A}/SP500_1w", f"{B}/DGS10_1w", f"{B}/SP500_1w",
        f"{A}/DGS10_1m", f"{B}/DGS10_1m"]
    assert read_json(root / A / "prediction.json")["model"] == DEFAULT_MODEL
