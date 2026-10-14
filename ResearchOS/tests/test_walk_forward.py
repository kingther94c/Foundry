"""v10: walk-forward training without look-ahead (SPEC.md "Walk-forward without look-ahead")."""

import itertools
import json
from datetime import date

import pytest

from conftest import FakeLLM, make_case, make_outcome, make_roles, schema_client

from researchos import cases as cases_mod
from researchos import loop as loop_mod
from researchos.__main__ import main
from researchos.cases import save_case
from researchos.loop import evaluate, train
from researchos.perspectives import PERSPECTIVES
from researchos.playbook import Playbook, learned_through, load_playbook, save_playbook

HIT = {"guesses": [{"company": "Acme Widgets", "ticker": "ACMW", "confidence": 1.0}],
       "clues": ["obvious"]}
ROLE_NAMES = ("masker", "guesser", "analyst", "judge", "reflector")


def add_case(root, case_id, event_date, ar=0.02):
    case = make_case(case_id)
    case.event_date = event_date
    save_case(root, case, make_outcome(ar=ar))


def numbered_reflector():
    """A reflector whose k-th call teaches the lesson "Lesson number k."."""
    counter = itertools.count(1)

    def fn(system, prompt, schema, prefix):
        k = next(counter)
        return {"why_market_moved": "m", "where_we_were_wrong": "w",
                "lessons": [{"lesson": f"Lesson number {k}.", "applies_when": "always",
                             "perspective": "general"}]}

    return FakeLLM(fn)


def no_calls(roles):
    return all(not getattr(roles, name).calls for name in ROLE_NAMES)


def files_under(path):
    return sorted((p.relative_to(path).as_posix(), p.read_bytes())
                  for p in path.rglob("*") if p.is_file())


# ---- learned_through ----------------------------------------------------------------

def test_learned_through_is_the_latest_event_date_learned_from(tmp_path):
    pb = Playbook.initial()
    assert learned_through(pb) is None
    pb.history = [
        {"case_id": "a", "split": "train", "event_date": "2026-03-05", "brier_judge": 0.1},
        {"case_id": "b", "split": "train", "event_date": "2026-05-01",
         "skipped": "identifiable"},
        {"case_id": "c", "split": "train", "brier_judge": 0.2},  # trained before v10
        {"case_id": "d", "split": "train", "event_date": "2026-01-15", "brier_judge": 0.3},
    ]
    through = learned_through(pb)
    assert through == date(2026, 3, 5) and type(through) is date
    save_playbook(tmp_path / "pb.json", pb)
    assert learned_through(load_playbook(tmp_path / "pb.json")) == date(2026, 3, 5)


def test_learned_through_is_none_without_a_dated_record_learned_from():
    pb = Playbook.initial()
    pb.history = [
        {"case_id": "b", "split": "train", "event_date": "2026-05-01",
         "skipped": "identifiable"},
        {"case_id": "c", "split": "train", "brier_judge": 0.2},
    ]
    assert learned_through(pb) is None


def test_learned_through_does_not_guess_past_a_malformed_date():
    pb = Playbook.initial()
    pb.history = [{"case_id": "a", "split": "train", "event_date": "last spring",
                   "brier_judge": 0.1}]
    with pytest.raises(ValueError, match="last spring"):
        learned_through(pb)


# ---- train: event-date order and same-date batches ----------------------------------

def test_train_records_carry_the_event_date(tmp_path):
    root = tmp_path / "cases"
    add_case(root, "jan", "2026-01-10")
    add_case(root, "feb", "2026-02-01")
    # "feb" was masked before and stayed identifiable: its saved mask is reused, so it is
    # skipped below
    train(root, ["feb"], tmp_path / "other.json", make_roles(guesser=FakeLLM(responses=[HIT])))
    pb_path = tmp_path / "pb.json"
    results = train(root, ["feb", "jan"], pb_path, make_roles())
    assert [(r["case_id"], r["event_date"]) for r in results] == [("jan", "2026-01-10")]
    pb = load_playbook(pb_path)
    assert pb.history == [
        results[0],
        {"case_id": "feb", "split": "train", "event_date": "2026-02-01",
         "skipped": "identifiable"},
    ]
    # the skipped case taught the playbook nothing
    assert learned_through(pb) == date(2026, 1, 10)


def test_train_walks_forward_in_event_date_order(tmp_path):
    root = tmp_path / "cases"
    add_case(root, "mar", "2026-03-01")
    add_case(root, "feb-x", "2026-02-01")
    add_case(root, "jan", "2026-01-10", ar=-0.01)
    add_case(root, "feb-y", "2026-02-01")
    pb_path = tmp_path / "pb.json"
    results = train(root, ["mar", "feb-y", "jan", "feb-x"], pb_path, make_roles())
    order = ["jan", "feb-y", "feb-x", "mar"]  # by date; the two Feb 1 cases keep the order given
    assert [r["case_id"] for r in results] == order
    pb = load_playbook(pb_path)
    assert pb.history == results
    assert [l.case_id for l in pb.lessons] == order
    assert learned_through(pb) == date(2026, 3, 1)


def test_a_same_date_batch_is_forecast_with_the_playbook_from_before_it(tmp_path, monkeypatch):
    root = tmp_path / "cases"
    add_case(root, "jan", "2026-01-10", ar=-0.02)
    add_case(root, "feb-1", "2026-02-01", ar=0.03)
    add_case(root, "feb-2", "2026-02-01", ar=-0.01)
    add_case(root, "mar", "2026-03-01")
    sealed_when_opened = []
    real = cases_mod.load_outcome

    def guarded(r, cid):
        sealed = sorted(p.parent.name for p in root.glob("*/prediction.json"))
        sealed_when_opened.append((cid, sealed))
        return real(r, cid)

    monkeypatch.setattr(loop_mod, "load_outcome", guarded)
    roles = make_roles(reflector=numbered_reflector())
    results = train(root, ["mar", "feb-1", "feb-2", "jan"], tmp_path / "pb.json", roles)
    order = ["jan", "feb-1", "feb-2", "mar"]
    assert [r["case_id"] for r in results] == order

    # both Feb 1 forecasts are sealed before either Feb 1 outcome is opened; March waits
    assert sealed_when_opened == [
        ("jan", ["jan"]),
        ("feb-1", ["feb-1", "feb-2", "jan"]),
        ("feb-2", ["feb-1", "feb-2", "jan"]),
        ("mar", ["feb-1", "feb-2", "jan", "mar"]),
    ]

    # lessons: the k-th reflection (in the order above) taught "Lesson number k."
    n = len(PERSPECTIVES)
    texts = [c["prompt"] + (c["prefix"] or "") for c in roles.analyst.calls]
    assert len(texts) == len(order) * n
    shown = {cid: "\n".join(texts[i * n:(i + 1) * n]) for i, cid in enumerate(order)}

    def lessons_seen(cid):
        return [k for k in range(1, 5) if f"Lesson number {k}." in shown[cid]]

    assert lessons_seen("jan") == []
    assert lessons_seen("feb-1") == [1]
    assert lessons_seen("feb-2") == [1]  # not feb-1's lesson: they reacted the same day
    assert lessons_seen("mar") == [1, 2, 3]

    # the weights too: both Feb 1 cases were weighted with the playbook jan left behind
    def sealed(cid):
        return json.loads((root / cid / "prediction.json").read_text(encoding="utf-8"))

    assert sealed("feb-1")["prob_weighted"] == sealed("feb-2")["prob_weighted"]
    assert sealed("feb-1")["prob_weighted"] != sealed("jan")["prob_weighted"]


# ---- refusing a look-ahead ----------------------------------------------------------

def test_train_refuses_a_case_on_or_before_what_the_playbook_learned(tmp_path):
    root = tmp_path / "cases"
    add_case(root, "jan", "2026-01-10")
    add_case(root, "feb", "2026-02-01")
    add_case(root, "feb-again", "2026-02-01")
    add_case(root, "feb-02", "2026-02-02")
    pb_path = tmp_path / "pb.json"
    train(root, ["feb"], pb_path, make_roles())
    assert learned_through(load_playbook(pb_path)) == date(2026, 2, 1)
    before = files_under(tmp_path)
    for ids, named in ((["jan"], "jan"), (["feb-again"], "feb-again"), (["feb"], "feb"),
                       (["feb-02", "jan"], "jan")):
        roles = make_roles()
        with pytest.raises(ValueError, match=rf"\b{named}\b") as excinfo:
            train(root, ids, pb_path, roles)
        if "feb-02" in ids:
            assert "feb-02" not in str(excinfo.value)
        assert no_calls(roles)
        assert files_under(tmp_path) == before  # no mask, no prediction, playbook untouched
    # the next day is after what the playbook learned
    results = train(root, ["feb-02"], pb_path, make_roles())
    assert [r["case_id"] for r in results] == ["feb-02"]
    assert learned_through(load_playbook(pb_path)) == date(2026, 2, 2)


def test_evaluate_refuses_a_held_out_case_the_playbook_learned_past(tmp_path):
    root = tmp_path / "cases"
    add_case(root, "jan", "2026-01-10")
    add_case(root, "feb", "2026-02-01")
    add_case(root, "mar", "2026-03-01")
    pb_path = tmp_path / "pb.json"
    train(root, ["feb"], pb_path, make_roles())
    before = files_under(tmp_path)
    # "mar" comes first and is fine, but nothing runs: every case is checked first
    for ids, named in ((["feb"], "feb"), (["mar", "jan"], "jan")):
        roles = make_roles()
        with pytest.raises(ValueError, match=rf"\b{named}\b"):
            evaluate(root, ids, pb_path, roles)
        assert no_calls(roles)
        assert files_under(tmp_path) == before
    report = evaluate(root, ["mar"], pb_path, make_roles())
    assert report["n"] == 1 and [c["case_id"] for c in report["cases"]] == ["mar"]


def test_only_dated_cases_the_playbook_learned_from_count(tmp_path):
    root = tmp_path / "cases"
    add_case(root, "feb", "2026-02-01")
    pb_path = tmp_path / "pb.json"
    pb = Playbook.initial()
    pb.history = [
        {"case_id": "old", "split": "train", "brier_judge": 0.1},  # trained before v10
        {"case_id": "later", "split": "train", "event_date": "2026-06-01",
         "skipped": "identifiable"},
    ]
    save_playbook(pb_path, pb)
    assert evaluate(root, ["feb"], pb_path, make_roles())["n"] == 1
    assert [r["case_id"] for r in train(root, ["feb"], pb_path, make_roles())] == ["feb"]


@pytest.mark.parametrize("bad", ["", "soon", "2026-02-30", "02/01/2026"])
def test_a_case_without_an_iso_event_date_is_refused(tmp_path, bad):
    root = tmp_path / "cases"
    add_case(root, "dated", "2026-01-10")
    add_case(root, "undated", bad)
    pb_path = tmp_path / "pb.json"
    before = files_under(tmp_path)
    for run in (train, evaluate):
        roles = make_roles()
        with pytest.raises(ValueError, match="undated"):
            run(root, ["dated", "undated"], pb_path, roles)
        assert no_calls(roles)
        assert files_under(tmp_path) == before
    assert not pb_path.exists()


# ---- CLI ----------------------------------------------------------------------------

def test_cli_refusal_exits_1_with_the_message_on_stderr(tmp_path, capsys):
    root = tmp_path / "cases"
    add_case(root, "jan", "2026-01-10")
    add_case(root, "feb", "2026-02-01")
    add_case(root, "undated", "soon")
    pb = tmp_path / "pb.json"
    assert main(["train", "--root", str(root), "--playbook", str(pb), "--ids", "feb"],
                client_factory=schema_client) == 0
    capsys.readouterr()
    before = pb.read_bytes()
    client = schema_client()
    for cmd in ("train", "evaluate"):
        for case_id in ("jan", "undated"):
            code = main([cmd, "--root", str(root), "--playbook", str(pb), "--ids", case_id],
                        client_factory=lambda: client)
            captured = capsys.readouterr()
            assert code == 1
            assert captured.out == ""
            assert captured.err.startswith("error:") and case_id in captured.err
    assert client.requests == []
    assert pb.read_bytes() == before
