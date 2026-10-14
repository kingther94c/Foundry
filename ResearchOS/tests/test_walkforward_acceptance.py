"""Acceptance tests for walk-forward training without look-ahead (v10), written from SPEC.md
independently of the code (held back from the agent that built it).

Each case traces to the "Walk-forward without look-ahead (v10)" section of SPEC.md.
"""

from __future__ import annotations

import builtins
import io
import json
import os
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from test_acceptance import Fake, ScriptedClient, make_case, view_reply

from researchos import cases, loop, masking, perspectives, playbook
from researchos.cases import Outcome

N = len(perspectives.PERSPECTIVES)


def put(root, case_id, event_date, up=True):
    cases.save_case(root, replace(make_case(case_id), case_id=case_id, event_date=event_date),
                    Outcome(abnormal_return_1d=0.03 if up else -0.03))


def numbered_roles(identified=False):
    """Roles whose n-th reflection teaches "Lesson n", so a prompt shows which lessons it got."""
    return loop.Roles(
        masker=Fake(lambda *a: {"redactions": []}),
        guesser=Fake(lambda *a: {"guesses": [{"company": "Zephyrex" if identified else "Nobody",
                                               "ticker": "", "confidence": 0.5}], "clues": []}),
        analyst=Fake(view_reply({"growth": 0.9, "risks": 0.1})),
        judge=Fake(lambda *a: {"prob_up": 0.7, "thesis": "x"}),
        reflector=Fake(lambda n, *a: {"why_market_moved": "m", "where_we_were_wrong": "w",
                                      "lessons": [{"lesson": f"Lesson {n}", "applies_when": "always",
                                                   "perspective": "growth"}]}),
    )


def lessons_seen(r):
    """For each analyzed case in turn, the lessons its analysts were shown."""
    out = []
    for i in range(0, len(r.analyst.calls), N):
        sent = " ".join(f"{c['system']} {c['prompt']} {c['prefix'] or ''}" for c in r.analyst.calls[i:i + N])
        out.append(sorted(w for w in ("Lesson 1", "Lesson 2", "Lesson 3") if w in sent))
    return out


def test_train_follows_event_order(tmp_path):
    put(tmp_path, "late", "2026-07-20")
    put(tmp_path, "early", "2026-07-10")
    r = numbered_roles()
    book = tmp_path / "pb.json"
    records = loop.train(tmp_path, ["late", "early"], book, r)
    assert [x["case_id"] for x in records] == ["early", "late"]
    assert [x["event_date"] for x in records] == ["2026-07-10", "2026-07-20"]
    assert lessons_seen(r) == [[], ["Lesson 1"]]
    history = playbook.load_playbook(book).history
    assert [(h["case_id"], h["event_date"]) for h in history] == [("early", "2026-07-10"), ("late", "2026-07-20")]
    assert playbook.learned_through(playbook.load_playbook(book)) == date(2026, 7, 20)


def test_a_batch_of_one_event_date_is_forecast_before_any_outcome_is_opened(tmp_path, monkeypatch):
    put(tmp_path, "a", "2026-07-10")
    put(tmp_path, "b", "2026-07-10")
    put(tmp_path, "c", "2026-07-11")
    reads = []
    real_open = io.open

    def spy(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, os.PathLike)):
            path = Path(file)
            if path.name == "outcome.json" and "w" not in mode and "a" not in mode:
                sealed = {cid: (tmp_path / cid / "prediction.json").is_file() for cid in "abc"}
                reads.append((path.parent.name, sealed))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", spy)
    monkeypatch.setattr(builtins, "open", spy)
    r = numbered_roles()
    records = loop.train(tmp_path, ["a", "b", "c"], tmp_path / "pb.json", r)
    monkeypatch.undo()
    assert [cid for cid, _ in reads] == ["a", "b", "c"]
    assert reads[0][1] == {"a": True, "b": True, "c": False}   # b sealed before a's outcome
    assert reads[2][1]["c"] is True
    assert lessons_seen(r) == [[], [], ["Lesson 1", "Lesson 2"]]
    # a and b were forecast with the same (initial) weights; c with the weights a and b taught.
    assert records[0]["brier_weighted"] == pytest.approx(records[1]["brier_weighted"])
    assert records[2]["brier_weighted"] != pytest.approx(records[0]["brier_weighted"])


def test_ties_keep_the_order_given(tmp_path):
    for cid in ("x", "y", "z"):
        put(tmp_path, cid, "2026-07-10")
    records = loop.train(tmp_path, ["z", "x", "y"], tmp_path / "pb.json", numbered_roles())
    assert [r["case_id"] for r in records] == ["z", "x", "y"]


def test_learned_through():
    book = playbook.Playbook.initial()
    assert playbook.learned_through(book) is None
    book.history = [{"case_id": "old"},                                   # before v10: no date
                    {"case_id": "s", "event_date": "2026-08-01", "skipped": "identifiable"},
                    {"case_id": "a", "event_date": "2026-07-15"},
                    {"case_id": "b", "event_date": "2026-07-03"}]
    assert playbook.learned_through(book) == date(2026, 7, 15)
    book.history = [{"case_id": "old"}, {"case_id": "s", "event_date": "2026-08-01", "skipped": "x"}]
    assert playbook.learned_through(book) is None


def test_skipped_cases_carry_the_date_but_teach_nothing(tmp_path):
    put(tmp_path, "s", "2026-07-10")
    put(tmp_path, "t", "2026-07-10")
    book = tmp_path / "pb.json"
    loop.train(tmp_path, ["s"], book, numbered_roles(identified=True),
               mask_config=masking.MaskConfig(max_rounds=1))
    history = playbook.load_playbook(book).history
    assert len(history) == 1
    assert history[0]["skipped"] == "identifiable" and history[0]["event_date"] == "2026-07-10"
    assert playbook.learned_through(playbook.load_playbook(book)) is None
    records = loop.train(tmp_path, ["t"], book, numbered_roles())   # same date: allowed
    assert [r["case_id"] for r in records] == ["t"]


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(Path(root).rglob("*")) if p.is_file()}


def test_train_refuses_a_case_the_playbook_has_already_seen_past(tmp_path):
    put(tmp_path, "alpha", "2026-07-10")
    put(tmp_path, "bravo", "2026-07-10")
    put(tmp_path, "charlie", "2026-07-11")
    put(tmp_path, "delta", "2026-07-09")
    book = tmp_path / "pb.json"
    loop.train(tmp_path, ["alpha"], book, numbered_roles())
    before = snapshot(tmp_path)
    for ids, culprit in ([["bravo"], "bravo"], [["charlie", "bravo"], "bravo"], [["delta", "charlie"], "delta"]):
        r = numbered_roles()
        with pytest.raises(ValueError) as info:
            loop.train(tmp_path, ids, book, r)
        assert culprit in str(info.value)
        assert all(not getattr(r, role).calls for role in ("masker", "guesser", "analyst", "judge", "reflector"))
        assert snapshot(tmp_path) == before        # nothing written, c not trained either
    records = loop.train(tmp_path, ["charlie"], book, numbered_roles())
    assert [x["case_id"] for x in records] == ["charlie"]


def test_evaluate_holds_out_only_later_cases(tmp_path):
    put(tmp_path, "alpha", "2026-07-10")
    put(tmp_path, "bravo", "2026-07-10")
    put(tmp_path, "charlie", "2026-07-11", up=False)
    book = tmp_path / "pb.json"
    loop.train(tmp_path, ["alpha"], book, numbered_roles())
    before = snapshot(tmp_path)
    r = numbered_roles()
    with pytest.raises(ValueError) as info:
        loop.evaluate(tmp_path, ["charlie", "bravo"], book, r)
    assert "bravo" in str(info.value)
    assert not r.analyst.calls and not r.masker.calls
    assert snapshot(tmp_path) == before
    report = loop.evaluate(tmp_path, ["charlie"], book, numbered_roles())
    assert report["n"] == 1
    # Without a playbook file there is nothing learned: any date is fine.
    assert loop.evaluate(tmp_path, ["bravo"], tmp_path / "none.json", numbered_roles())["n"] == 1


def test_cli_look_ahead_exits_1(tmp_path, capsys):
    from researchos.__main__ import main
    put(tmp_path, "alpha", "2026-07-10")
    put(tmp_path, "bravo", "2026-07-10")
    book = str(tmp_path / "pb.json")
    assert main(["train", "--root", str(tmp_path), "--playbook", book, "--ids", "alpha"],
                client_factory=ScriptedClient) == 0
    capsys.readouterr()
    for command in ("train", "evaluate"):
        assert main([command, "--root", str(tmp_path), "--playbook", book, "--ids", "bravo"],
                    client_factory=ScriptedClient) == 1
        captured = capsys.readouterr()
        assert captured.out == "" and "bravo" in captured.err


@pytest.mark.parametrize("bad", ["", "July 30", "2026-13-01"])
def test_a_case_without_an_iso_event_date_is_refused(tmp_path, bad):
    put(tmp_path, "fine", "2026-07-10")
    put(tmp_path, "undated", bad)
    for run in (loop.train, loop.evaluate):
        r = numbered_roles()
        with pytest.raises(ValueError) as info:
            run(tmp_path, ["fine", "undated"], tmp_path / "pb.json", r)
        assert "undated" in str(info.value)
        assert not r.masker.calls and not r.analyst.calls
        assert not (tmp_path / "pb.json").exists() and not (tmp_path / "fine" / "prediction.json").exists()
