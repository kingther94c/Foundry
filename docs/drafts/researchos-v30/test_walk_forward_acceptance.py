"""Acceptance tests for walking forward by resolution date (v30), written from SPEC.md
independently of the code (held back from the agent that built it).

Each case traces to the "Walk forward by resolution date (v30)" section of SPEC.md. The
fakes answer by the schema they are given. An analyst's probabilities depend on the
perspective, the key and the snapshot's date, so the weights move, and a reflector's lesson
names the snapshot and the keys it was shown, so a prompt shows what was learned before it.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.stats import hedge_update

from researchos import cases, loop, masking, playbook, targets
from researchos.__main__ import main
from researchos.cases import Case, Document, Outcome, Target, TargetOutcome
from researchos.masking import MaskConfig
from researchos.playbook import Lesson, Playbook, load_playbook, save_playbook

CUTOFF = "2026-06-30"
A, B, C = "2026-07-03", "2026-07-10", "2026-07-17"
MACRO = targets.MACRO_PERSPECTIVES
OFFSET = {"DGS10_1w": 0.0, "DGS10_1m": 0.1, "DGS10_3m": 0.15, "SP500_1w": 0.2}
DATE = re.compile(r"as of (\d{4}-\d\d-\d\d)")
UPS = {A: {"DGS10_1w": True, "DGS10_1m": False, "SP500_1w": True},
       B: {"DGS10_1w": False, "DGS10_1m": True, "SP500_1w": False},
       C: {"DGS10_1w": True, "DGS10_1m": True, "SP500_1w": False}}


def mid(as_of):
    return f"macro-{as_of}"


# --- fakes ----------------------------------------------------------------------------------------

def lesson_text(as_of, keys):
    return f"Lesson {as_of} {'+'.join(keys)}"


def answer(prompt, schema, context=""):
    props = schema["properties"]
    if "redactions" in props or "guesses" in props:
        raise AssertionError("a macro run masks nothing")
    if "lessons" in props:
        as_of = DATE.search(prompt).group(1)
        keys = re.findall(r"^### (\S+) \(", prompt, flags=re.M)
        return {"why_market_moved": "Yields fell.", "where_we_were_wrong": "Too hawkish.",
                "lessons": [{"lesson": lesson_text(as_of, keys), "applies_when": "always",
                             "perspective": "inflation"}]}
    first = prompt.splitlines()[0] if prompt else ""
    name = first.split(": ", 1)[1] if first.startswith("Perspective: ") else None
    found = DATE.search(context + "\n" + prompt)
    day = int(found.group(1)[-2:]) if found else 0
    forecasts = {}
    for target_id, sub in props["forecasts"]["properties"].items():
        forecasts[target_id] = {}
        for field in sub["properties"]:
            key = f"{target_id}_{field[len('prob_up_'):]}"
            if name:
                p = 0.2 + 0.05 * MACRO.index(name) + OFFSET[key] + 0.01 * (day % 5)
            else:
                p = 0.55
            forecasts[target_id][field] = round(p, 2)
    if name:
        return {"key_points": [f"{name} point"], "forecasts": forecasts}
    return {"forecasts": forecasts, "thesis": "Rates drift lower."}


class Fake:
    def __init__(self, **attrs):
        self.calls = []
        self.__dict__.update(attrs)

    def json(self, system, prompt, schema, *, prefix=None):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema,
                           "prefix": prefix})
        return answer(prompt, schema, prefix or "")


def make_roles(analyst=None):
    return loop.Roles(masker=Fake(), guesser=Fake(), analyst=analyst or Fake(), judge=Fake(),
                      reflector=Fake())


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
        blocks = kwargs["messages"][0]["content"]
        context = "\n".join(block.get("text", "") for block in blocks[:-1])
        text = json.dumps(answer(blocks[-1]["text"], schema, context))
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=None,
                                cache_creation_input_tokens=3)
        return SimpleNamespace(stop_reason=self.stop_reason, stop_details=None, usage=usage,
                               content=[SimpleNamespace(type="text", text=text)])


# --- cases ----------------------------------------------------------------------------------------

def days_before(as_of, n):
    return date.fromordinal(date(*map(int, as_of.split("-"))).toordinal() - n).isoformat()


def macro(as_of, case_id=None, dgs=("1w", "1m"), sp=("1w",)):
    return cases.macro_case(case_id or mid(as_of), as_of, [
        Target("DGS10", "10-year Treasury yield", "bp", list(dgs), days_before(as_of, 1), 4.0,
               {"1w": -2.0}),
        Target("SP500", "S&P 500", "return", list(sp), days_before(as_of, 1), 6800.0),
    ], [Document("dashboard", f"Markets as of {as_of}", "| Target | Level |")])


def target_outcome(key, up):
    target, horizon = key.split("_")
    if target == "DGS10":
        return TargetOutcome("DGS10", horizon, "bp", "2026-07-02", 4.0, "2026-08-01",
                             4.1 if up else 3.9, 10.0 if up else -10.0, up)
    return TargetOutcome("SP500", horizon, "return", "2026-07-02", 6800.0, "2026-08-01",
                         6868.0 if up else 6732.0, 0.01 if up else -0.01, up)


def save_snapshot(root, as_of, case_id=None, ups=None, resolved=None, outcome=True, **kw):
    """A macro case with its manifest (every key resolved unless `resolved` says otherwise)
    and its outcome (one TargetOutcome per key unless `ups` leaves one out)."""
    case = macro(as_of, case_id, **kw)
    keys = cases.target_keys(case)
    cases.save_case(root, case)
    cases.save_resolution(root, case.case_id, cases.resolution_manifest(
        case, keys if resolved is None else resolved))
    if outcome:
        ups = UPS.get(as_of, {k: True for k in keys}) if ups is None else ups
        cases.save_outcome(root, case.case_id, Outcome(targets={
            key: target_outcome(key, up) for key, up in ups.items() if key in keys}))
    return case


def save_three(root):
    return [save_snapshot(root, d) for d in (A, B, C)]


def earnings(case_id="acme-q4", event_date="2026-02-01"):
    return Case(case_id, "Acme Widgets Inc.", "ACMW", ["Acme"], "Q4 FY2025", event_date, [
        Document("release", "Acme Widgets Inc. reports Q4 results",
                 "Acme Widgets Inc. reported revenue of $1,200.0 million, up 12%.",
                 "https://acme.example/q4")])


def tree(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def sealed(root, case_id):
    return json.loads((Path(root) / case_id / "prediction.json").read_text(encoding="utf-8"))


def roundtrip(data):
    return json.loads(json.dumps(data))


def expected_analysis(case, book, mode="holistic"):
    analysis = targets.analyze_targets(masking.unmasked(case), case, book, Fake(), Fake(),
                                       perspectives=targets.perspective_set("macro"), mode=mode)
    analysis.model = ""
    analysis.knowledge_cutoff = CUTOFF
    return analysis


def records_of(root, case):
    analysis = targets.target_analysis_from_dict(sealed(root, case.case_id))
    outcome = cases.load_outcome(root, case.case_id)
    return {r["key"]: r for r in targets.score_targets(analysis, outcome, case)}


def entry_of(record):
    entry = {"case_id": record["case_id"], "split": "train"}
    entry.update((k, v) for k, v in record.items() if k != "case_id")
    return entry


def hedge(weights, record):
    y = 1.0 if record["actual_up"] else 0.0
    return hedge_update(weights, {n: (p - y) ** 2
                                  for n, p in record["prob_by_perspective"].items()}, 0.5)


def run(root, ids, roles=None, pb=None, **kw):
    roles = roles or make_roles()
    kw.setdefault("knowledge_cutoff", CUTOFF)
    results = loop.train(root, ids, pb or Path(root) / "pb.json", roles, **kw)
    return results, roles


def text_of(call):
    return "\n".join(str(call[k] or "") for k in ("system", "prefix", "prompt"))


def analyst_text(roles, as_of):
    texts = [text_of(c) for c in roles.analyst.calls
             if f"as of {as_of}" in (c["prefix"] or "") + c["prompt"]]
    assert len(texts) == len(MACRO)
    return "\n".join(texts)


@pytest.fixture
def opened(monkeypatch):
    events = []
    real_open = io.open

    def spy(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, os.PathLike)):
            path = Path(file)
            kind = "w" if any(c in mode for c in "wax+") else "r"
            events.append((f"{path.parent.name}/{path.name}", kind))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", spy)
    monkeypatch.setattr(builtins, "open", spy)
    return events


# --- playbook: update_weights_probs and learned_through -------------------------------------------

def test_update_weights_probs_is_the_hedge_update():
    book = Playbook.initial(["a", "b", "c"])
    probs = {"a": 0.9, "b": 0.4, "c": 0.1}
    assert playbook.update_weights_probs(book, probs, True) is None
    assert book.weights == pytest.approx(hedge_update(
        {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3}, {"a": 0.01, "b": 0.36, "c": 0.81}, 0.5))
    before = dict(book.weights)
    playbook.update_weights_probs(book, probs, False, eta=2.0)
    assert book.weights == pytest.approx(hedge_update(
        before, {"a": 0.81, "b": 0.16, "c": 0.01}, 2.0))
    assert (book.lessons, book.history) == ([], [])


def test_update_weights_probs_matches_update_weights():
    names = ["growth", "risks"]
    probs = {"growth": 0.7, "risks": 0.2}
    one, two = Playbook.initial(names), Playbook.initial(names)
    views = {n: SimpleNamespace(prob_up=p) for n, p in probs.items()}
    playbook.update_weights(one, views, False)
    playbook.update_weights_probs(two, probs, False)
    assert one.weights == pytest.approx(two.weights)


@pytest.mark.parametrize("history, expected", [
    ([], None),
    ([{"case_id": "x", "event_date": "2026-02-01"}, {"case_id": "y", "event_date": "2026-01-05"}],
     date(2026, 2, 1)),
    ([{"case_id": "x", "event_date": "2026-07-03", "resolution_date": "2026-08-03"}],
     date(2026, 8, 3)),
    ([{"case_id": "x", "event_date": "2026-09-01", "resolution_date": None}], date(2026, 9, 1)),
    ([{"case_id": "x", "resolution_date": "2026-07-10"}], date(2026, 7, 10)),
    ([{"case_id": "x", "event_date": "2026-09-01"},
      {"case_id": "y", "event_date": "2026-07-03", "resolution_date": "2026-08-03"}],
     date(2026, 9, 1)),
    ([{"case_id": "x", "event_date": "2026-07-03", "resolution_date": "2026-10-05"},
      {"case_id": "y", "event_date": "2026-09-01"}], date(2026, 10, 5)),
    ([{"case_id": "x", "event_date": "2026-07-03", "resolution_date": "2026-12-01",
       "skipped": "identifiable"}, {"case_id": "y", "event_date": "2026-07-03"}],
     date(2026, 7, 3)),
    ([{"case_id": "x"}, "not a record", {"case_id": "y", "resolution_date": None}], None),
])
def test_learned_through(history, expected):
    assert playbook.learned_through(Playbook({"a": 1.0}, [], history)) == expected


@pytest.mark.parametrize("record", [
    {"case_id": "x", "event_date": "2026-07-03", "resolution_date": "soon"},
    {"case_id": "x", "event_date": "2026-07-03", "resolution_date": "2026/08/03"},
    {"case_id": "x", "event_date": "July 3"},
])
def test_learned_through_refuses_a_date_it_cannot_read(record):
    with pytest.raises(ValueError):
        playbook.learned_through(Playbook({"a": 1.0}, [], [record]))


# --- train: the walk forward ----------------------------------------------------------------------

KEYS = ["DGS10_1w", "DGS10_1m", "SP500_1w"]
ORDER = [(A, "DGS10_1w"), (A, "SP500_1w"), (B, "DGS10_1w"), (B, "SP500_1w"), (C, "DGS10_1w"),
         (C, "SP500_1w"), (A, "DGS10_1m"), (B, "DGS10_1m"), (C, "DGS10_1m")]
REFLECTIONS = [(A, ["DGS10_1w", "SP500_1w"]), (B, ["DGS10_1w", "SP500_1w"]),
               (C, ["DGS10_1w", "SP500_1w"]), (A, ["DGS10_1m"]), (B, ["DGS10_1m"]),
               (C, ["DGS10_1m"])]


def test_train_learns_from_each_key_in_the_order_it_resolved(tmp_path):
    snaps = save_three(tmp_path)
    results, roles = run(tmp_path, [mid(C), mid(A), mid(B)])
    records = {case.event_date: records_of(tmp_path, case) for case in snaps}
    assert results == [entry_of(records[d][k]) for d, k in ORDER]
    assert [r["case_id"] for r in results] == [f"{mid(d)}/{k}" for d, k in ORDER]


def test_an_entry_is_the_record_with_its_split(tmp_path):
    snaps = save_three(tmp_path)
    results, _ = run(tmp_path, [mid(A), mid(B), mid(C)])
    record = records_of(tmp_path, snaps[0])["DGS10_1w"]
    entry = results[0]
    assert list(entry) == ["case_id", "split"] + [k for k in record if k != "case_id"]
    assert entry["split"] == "train"
    assert (entry["event_date"], entry["resolution_date"]) == (A, "2026-07-10")
    assert results[6]["resolution_date"] == "2026-08-03"


def test_the_playbook_after_training(tmp_path):
    snaps = save_three(tmp_path)
    results, _ = run(tmp_path, [mid(A), mid(B), mid(C)])
    records = {case.event_date: records_of(tmp_path, case) for case in snaps}
    weights = {n: 1 / len(MACRO) for n in MACRO}
    for d, k in ORDER:
        weights = hedge(weights, records[d][k])
    book = load_playbook(tmp_path / "pb.json")
    assert book.weights == pytest.approx(weights)
    assert list(book.weights) == MACRO
    assert book.history == roundtrip(results)
    assert [(lesson.text, lesson.perspective, lesson.case_id) for lesson in book.lessons] == [
        (lesson_text(d, keys), "inflation", mid(d)) for d, keys in REFLECTIONS]
    assert playbook.learned_through(book) == date(2026, 8, 17)


def test_each_reflection_is_reflect_targets_on_one_horizon(tmp_path):
    snaps = {case.event_date: case for case in save_three(tmp_path)}
    _, roles = run(tmp_path, [mid(A), mid(B), mid(C)])
    assert len(roles.reflector.calls) == len(REFLECTIONS)
    for call, (d, keys) in zip(roles.reflector.calls, REFLECTIONS):
        case = snaps[d]
        recorder = Fake()
        targets.reflect_targets(case, targets.target_analysis_from_dict(
            sealed(tmp_path, case.case_id)), cases.load_outcome(tmp_path, case.case_id),
            recorder, keys=keys)
        assert call == recorder.calls[0]


def test_a_forecast_sees_only_what_resolved_before_its_date(tmp_path):
    save_three(tmp_path)
    _, roles = run(tmp_path, [mid(A), mid(B), mid(C)])
    for d in (A, B):  # A's 1w resolves on B's date: it is learned after B is sealed
        assert "Lesson " not in analyst_text(roles, d)
    seen = set(re.findall(r"Lesson \S+ \S+", analyst_text(roles, C)))
    assert seen == {lesson_text(A, ["DGS10_1w", "SP500_1w"])}


def test_a_key_that_resolved_before_a_batch_is_learned_before_it(tmp_path):
    save_snapshot(tmp_path, A)
    save_snapshot(tmp_path, "2026-07-13")  # a Monday, after A's 1w resolved on 2026-07-10
    results, roles = run(tmp_path, [mid(A), mid("2026-07-13")])
    assert lesson_text(A, ["DGS10_1w", "SP500_1w"]) in analyst_text(roles, "2026-07-13")
    assert [r["case_id"] for r in results[:2]] == [f"{mid(A)}/DGS10_1w", f"{mid(A)}/SP500_1w"]


def test_the_sealed_forecasts_use_the_playbook_as_it_stood(tmp_path):
    snaps = save_three(tmp_path)
    run(tmp_path, [mid(A), mid(B), mid(C)])
    initial = Playbook.initial(targets.perspective_set("macro"))
    for case in snaps[:2]:
        assert sealed(tmp_path, case.case_id) == roundtrip(asdict(expected_analysis(case,
                                                                                    initial)))
    first = records_of(tmp_path, snaps[0])
    book = Playbook.initial(targets.perspective_set("macro"))
    book.weights = hedge(hedge(book.weights, first["DGS10_1w"]), first["SP500_1w"])
    book.lessons = [Lesson(lesson_text(A, ["DGS10_1w", "SP500_1w"]), "always", "inflation",
                           mid(A))]
    assert sealed(tmp_path, mid(C)) == roundtrip(asdict(expected_analysis(snaps[2], book)))


def test_an_outcome_is_opened_only_after_its_forecast_is_sealed(tmp_path, opened):
    save_three(tmp_path)
    opened.clear()
    run(tmp_path, [mid(A), mid(B), mid(C)])

    def first(name, kind):
        return next(i for i, event in enumerate(opened) if event == (name, kind))

    seal = {d: first(f"{mid(d)}/prediction.json", "w") for d in (A, B, C)}
    out = {d: first(f"{mid(d)}/outcome.json", "r") for d in (A, B, C)}
    assert seal[A] < seal[B] < out[A] < seal[C] < out[B]
    assert seal[C] < out[C]
    for d in (A, B, C):
        reads = [i for i, e in enumerate(opened) if e == (f"{mid(d)}/outcome.json", "r")]
        assert min(reads) > seal[d]
    assert not any(name.endswith("masked.json") for name, _ in opened)


def test_cases_of_one_date_are_one_batch(tmp_path):
    q = save_snapshot(tmp_path, A, case_id="snap-q")
    p = save_snapshot(tmp_path, A, case_id="snap-p")
    results, roles = run(tmp_path, ["snap-q", "snap-p"])
    assert [r["case_id"] for r in results] == [
        "snap-q/DGS10_1w", "snap-q/SP500_1w", "snap-p/DGS10_1w", "snap-p/SP500_1w",
        "snap-q/DGS10_1m", "snap-p/DGS10_1m"]
    initial = Playbook.initial(targets.perspective_set("macro"))
    for case in (q, p):
        assert sealed(tmp_path, case.case_id) == roundtrip(asdict(expected_analysis(case,
                                                                                    initial)))
    keys = [re.findall(r"^### (\S+) \(", c["prompt"], flags=re.M) for c in roles.reflector.calls]
    assert keys == [["DGS10_1w", "SP500_1w"]] * 2 + [["DGS10_1m"]] * 2
    assert [lesson.case_id for lesson in load_playbook(tmp_path / "pb.json").lessons] == [
        "snap-q", "snap-p", "snap-q", "snap-p"]


def test_keys_that_resolve_on_one_day_keep_the_order_they_joined(tmp_path):
    save_snapshot(tmp_path, A, dgs=("1m",), sp=("1w",))  # DGS10_1m resolves 2026-08-03
    save_snapshot(tmp_path, "2026-07-27", dgs=("1w",), sp=("1w",))  # both resolve 2026-08-03
    results, _ = run(tmp_path, [mid("2026-07-27"), mid(A)])
    assert [r["case_id"] for r in results] == [
        f"{mid(A)}/SP500_1w", f"{mid(A)}/DGS10_1m", f"{mid('2026-07-27')}/DGS10_1w",
        f"{mid('2026-07-27')}/SP500_1w"]


def test_one_snapshot(tmp_path):
    save_snapshot(tmp_path, A)
    results, roles = run(tmp_path, [mid(A)])
    assert [r["key"] for r in results] == ["DGS10_1w", "SP500_1w", "DGS10_1m"]
    assert len(roles.reflector.calls) == 2
    assert (len(roles.analyst.calls), len(roles.judge.calls)) == (len(MACRO), 1)


def test_masking_plays_no_part(tmp_path, opened):
    save_three(tmp_path)
    plain, plain_roles = run(tmp_path, [mid(A), mid(B), mid(C)])
    assert (plain_roles.masker.calls, plain_roles.guesser.calls) == ([], [])
    assert not any(name.endswith("masked.json") for name, _ in opened)
    other = tmp_path / "other"
    other.mkdir()
    save_three(other)
    again, _ = run(other, [mid(A), mid(B), mid(C)], seed=7,
                   mask_config=MaskConfig(give_up_confidence=0.9, abstract=True))
    assert again == plain


def test_the_perspective_mode_and_the_model(tmp_path):
    snap = save_snapshot(tmp_path, A)
    _, roles = run(tmp_path, [mid(A)], roles=make_roles(analyst=Fake(model="m-1")),
                   perspective_mode="isolated", knowledge_cutoff=date(2026, 6, 30))
    data = sealed(tmp_path, mid(A))
    assert (data["mode"], data["model"], data["knowledge_cutoff"]) == ("isolated", "m-1", CUTOFF)
    expected = expected_analysis(snap, Playbook.initial(MACRO), mode="isolated")
    expected.model = "m-1"
    assert data == roundtrip(asdict(expected))
    save_snapshot(tmp_path, B)
    run(tmp_path, [mid(B)], roles=make_roles(analyst=Fake(model=5)), pb=tmp_path / "pb2.json")
    assert sealed(tmp_path, mid(B))["model"] == ""


def test_training_continues_a_macro_playbook(tmp_path):
    weights = {n: (i + 1) / 55 for i, n in enumerate(MACRO)}
    old = {"case_id": "macro-2026-05-01/DGS10_1w", "split": "train",
           "event_date": "2026-05-01", "resolution_date": "2026-05-08"}
    lesson = Lesson("Watch the curve.", "always", "growth", "macro-2026-05-01")
    save_playbook(tmp_path / "pb.json", Playbook(dict(weights), [lesson], [old]))
    snap = save_snapshot(tmp_path, A)
    results, _ = run(tmp_path, [mid(A)])
    assert sealed(tmp_path, mid(A)) == roundtrip(asdict(expected_analysis(
        snap, Playbook(dict(weights), [lesson], [old]))))
    book = load_playbook(tmp_path / "pb.json")
    assert book.history == [old] + roundtrip(results)
    assert book.lessons[0] == lesson and len(book.lessons) == 3


def test_a_failure_part_way_leaves_what_was_learned_saved(tmp_path):
    save_snapshot(tmp_path, A)
    save_snapshot(tmp_path, B, ups={"DGS10_1w": False, "DGS10_1m": True})  # no SP500_1w
    save_snapshot(tmp_path, C)
    with pytest.raises(ValueError) as exc:
        run(tmp_path, [mid(A), mid(B), mid(C)])
    assert mid(B) in str(exc.value) and "SP500_1w" in str(exc.value)
    book = load_playbook(tmp_path / "pb.json")
    assert [r["case_id"] for r in book.history] == [
        f"{mid(A)}/DGS10_1w", f"{mid(A)}/SP500_1w", f"{mid(B)}/DGS10_1w"]
    assert [lesson.text for lesson in book.lessons] == [lesson_text(A, ["DGS10_1w",
                                                                        "SP500_1w"])]
    assert all((tmp_path / mid(d) / "prediction.json").is_file() for d in (A, B, C))


# --- train: the refusals --------------------------------------------------------------------------

def refused(root, ids, error=ValueError, pb=None, **kw):
    before = tree(root)
    roles = make_roles()
    with pytest.raises(error) as exc:
        run(root, ids, roles=roles, pb=pb, **kw)
    assert n_calls(roles) == 0
    assert tree(root) == before
    return str(exc.value)


def test_a_snapshot_with_an_unresolved_key_is_refused(tmp_path, opened):
    save_snapshot(tmp_path, A)
    save_snapshot(tmp_path, B, resolved=["DGS10_1w", "SP500_1w"])
    before = tree(tmp_path)
    roles = make_roles()
    opened.clear()
    with pytest.raises(ValueError) as exc:
        run(tmp_path, [mid(A), mid(B)], roles=roles)
    assert not any(name.endswith("outcome.json") for name, _ in opened)
    assert n_calls(roles) == 0 and tree(tmp_path) == before
    assert mid(B) in str(exc.value) and "DGS10_1m" in str(exc.value)


def test_a_snapshot_without_a_manifest_is_refused(tmp_path):
    save_snapshot(tmp_path, A)
    (tmp_path / mid(A) / "resolution.json").unlink()
    assert mid(A) in refused(tmp_path, [mid(A)])


def test_a_sealed_snapshot_is_refused(tmp_path):
    save_snapshot(tmp_path, A)
    save_snapshot(tmp_path, B)
    (tmp_path / mid(B) / "prediction.json").write_text("{}", encoding="utf-8")
    assert mid(B) in refused(tmp_path, [mid(A), mid(B)])


@pytest.mark.parametrize("cutoff", [None, A, "2026-07-31"])
def test_a_snapshot_the_model_may_remember_is_refused(tmp_path, cutoff):
    save_snapshot(tmp_path, A)
    assert mid(A) in refused(tmp_path, [mid(A)], knowledge_cutoff=cutoff)


def test_a_snapshot_on_or_before_what_the_playbook_learned_is_refused(tmp_path):
    save_three(tmp_path)
    run(tmp_path, [mid(A), mid(B), mid(C)])
    save_snapshot(tmp_path, "2026-08-14")
    save_snapshot(tmp_path, "2026-08-17")
    message = refused(tmp_path, [mid("2026-08-14"), mid("2026-08-17")])
    assert message.startswith("train refused: ")
    assert mid("2026-08-14") in message and mid("2026-08-17") in message
    save_snapshot(tmp_path, "2026-08-21")
    results, _ = run(tmp_path, [mid("2026-08-21")])
    assert len(results) == 3


def test_forecast_takes_the_resolution_dates_too(tmp_path):
    save_three(tmp_path)
    run(tmp_path, [mid(A), mid(B), mid(C)])
    save_snapshot(tmp_path, "2026-08-14")
    with pytest.raises(ValueError) as exc:
        loop.forecast(tmp_path, [mid("2026-08-14")], tmp_path / "pb.json", make_roles(),
                      knowledge_cutoff=CUTOFF)
    assert mid("2026-08-14") in str(exc.value)
    save_snapshot(tmp_path, "2026-08-21")
    assert len(loop.forecast(tmp_path, [mid("2026-08-21")], tmp_path / "pb.json", make_roles(),
                             knowledge_cutoff=CUTOFF)) == 1


def test_an_undated_snapshot_is_refused(tmp_path):
    save_snapshot(tmp_path, A)
    path = tmp_path / mid(A) / "case.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["event_date"] = "2026-7-3"
    path.write_text(json.dumps(data), encoding="utf-8")
    message = refused(tmp_path, [mid(A)])
    assert message.startswith("train refused: ") and mid(A) in message


def test_an_id_given_twice_is_refused(tmp_path):
    save_snapshot(tmp_path, A)
    assert mid(A) in refused(tmp_path, [mid(A), mid(A)])


def test_a_macro_snapshot_and_an_earnings_case_are_refused(tmp_path):
    save_snapshot(tmp_path, A)
    cases.save_case(tmp_path, earnings(), Outcome(0.03, 0.04, 0.01, "Up.", []))
    message = refused(tmp_path, ["acme-q4", mid(A)])
    assert "macro" in message and "earnings" in message


def test_a_playbook_of_another_domain_is_refused(tmp_path):
    save_snapshot(tmp_path, A)
    save_playbook(tmp_path / "pb.json", Playbook.initial())
    assert "pb.json" in refused(tmp_path, [mid(A)])


def test_a_missing_snapshot_raises_os_error(tmp_path):
    save_snapshot(tmp_path, A)
    refused(tmp_path, [mid(A), "nope"], error=OSError)
    refused(tmp_path, ["nope", mid(A), mid(A)], error=OSError)


@pytest.mark.parametrize("kw", [{"perspective_mode": "both"}, {"knowledge_cutoff": "soon"}])
def test_the_mode_and_the_cutoff_are_checked_first(tmp_path, kw):
    save_snapshot(tmp_path, A)
    refused(tmp_path, [mid(A), "nope"], **kw)


# --- train: the order of the checks ---------------------------------------------------------------

def test_the_ids_are_checked_before_the_domains(tmp_path):
    save_snapshot(tmp_path, A)
    cases.save_case(tmp_path, earnings(), Outcome(0.03, 0.04, 0.01, "Up.", []))
    assert mid(A) in refused(tmp_path, [mid(A), "acme-q4", mid(A)])


def test_the_domains_are_checked_before_the_playbook(tmp_path):
    save_snapshot(tmp_path, A)
    cases.save_case(tmp_path, earnings(), Outcome(0.03, 0.04, 0.01, "Up.", []))
    save_playbook(tmp_path / "pb.json", Playbook.initial())
    message = refused(tmp_path, [mid(A), "acme-q4"])
    assert "pb.json" not in message and "earnings" in message


def test_the_playbook_is_checked_before_the_dates(tmp_path):
    save_snapshot(tmp_path, A)
    history = [{"case_id": "acme-q4", "event_date": "2026-09-01"}]
    save_playbook(tmp_path / "pb.json", Playbook(Playbook.initial().weights, [], history))
    message = refused(tmp_path, [mid(A)])
    assert "pb.json" in message and not message.startswith("train refused: ")


def macro_book(root, through):
    save_playbook(Path(root) / "pb.json", Playbook(
        Playbook.initial(MACRO).weights, [],
        [{"case_id": "old/DGS10_1w", "split": "train", "event_date": "2026-03-01",
          "resolution_date": through}]))


def test_the_dates_are_checked_before_the_cutoff(tmp_path):
    macro_book(tmp_path, "2026-05-01")
    save_snapshot(tmp_path, "2026-06-05", case_id="early")   # after the playbook, not the cutoff
    save_snapshot(tmp_path, "2026-04-24", case_id="behind")  # on or before the playbook
    message = refused(tmp_path, ["early", "behind"])
    assert message.startswith("train refused: ")
    assert "behind" in message and "early" not in message


def test_the_cutoff_is_checked_before_the_manifests(tmp_path):
    save_snapshot(tmp_path, A, case_id="unresolved", resolved=[])
    save_snapshot(tmp_path, "2026-06-26", case_id="remembered")
    message = refused(tmp_path, ["unresolved", "remembered"])
    assert "remembered" in message and "unresolved" not in message


def test_the_manifests_are_checked_before_the_seals(tmp_path):
    save_snapshot(tmp_path, A, case_id="sealed-one")
    (tmp_path / "sealed-one" / "prediction.json").write_text("{}", encoding="utf-8")
    save_snapshot(tmp_path, B, case_id="pending", resolved=["DGS10_1w"])
    message = refused(tmp_path, ["sealed-one", "pending"])
    assert "pending" in message and "sealed-one" not in message


# --- earnings, evaluate and the command line ------------------------------------------------------

class EarningsFake(Fake):
    def json(self, system, prompt, schema, *, prefix=None):
        self.calls.append(prompt)
        props = schema["properties"]
        if "redactions" in props:
            return {"redactions": [{"term": "Zephyr", "replacement": "the product line",
                                    "reason": "brand"}]}
        if "guesses" in props:
            return {"guesses": [{"company": "Globex", "ticker": "GBX", "confidence": 0.2}],
                    "clues": []}
        if "stance" in props:
            return {"stance": "bullish", "prob_up": 0.6, "conviction": "medium",
                    "key_points": ["p"]}
        if "thesis" in props:
            return {"prob_up": 0.65, "thesis": "Beat."}
        return {"why_market_moved": "", "where_we_were_wrong": "", "lessons": []}


def test_an_earnings_run_is_unchanged(tmp_path):
    cases.save_case(tmp_path, earnings(), Outcome(0.03, 0.04, 0.01, "Up.", []))
    roles = loop.Roles(masker=EarningsFake(), guesser=EarningsFake(), analyst=EarningsFake(),
                       judge=EarningsFake(), reflector=EarningsFake())
    results = loop.train(tmp_path, ["acme-q4"], tmp_path / "pb.json", roles)
    assert [r["case_id"] for r in results] == ["acme-q4"]
    assert results[0]["split"] == "train" and "resolution_date" not in results[0]
    assert set(load_playbook(tmp_path / "pb.json").weights) == set(
        Playbook.initial().weights)


def test_evaluate_still_refuses_a_macro_snapshot(tmp_path):
    save_snapshot(tmp_path, A)
    before = tree(tmp_path)
    roles = make_roles()
    with pytest.raises(ValueError) as exc:
        loop.evaluate(tmp_path, [mid(A)], tmp_path / "pb.json", roles, knowledge_cutoff=CUTOFF)
    assert mid(A) in str(exc.value)
    assert n_calls(roles) == 0 and tree(tmp_path) == before


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


def test_the_train_command(tmp_path, capsys):
    save_snapshot(tmp_path, A)
    save_snapshot(tmp_path, B)
    code, out, err, made = run_main(
        ["train", "--root", tmp_path, "--playbook", tmp_path / "pb.json", "--ids",
         f"{mid(B)},{mid(A)}", "--knowledge-cutoff", CUTOFF], capsys, FakeClient())
    assert code == 0, err
    printed = json.loads(out)
    assert printed == load_playbook(tmp_path / "pb.json").history
    assert [r["case_id"] for r in printed] == [
        f"{mid(A)}/DGS10_1w", f"{mid(A)}/SP500_1w", f"{mid(B)}/DGS10_1w", f"{mid(B)}/SP500_1w",
        f"{mid(A)}/DGS10_1m", f"{mid(B)}/DGS10_1m"]


def test_the_train_command_fails_cleanly(tmp_path, capsys):
    save_snapshot(tmp_path, A)
    base = ["train", "--root", tmp_path, "--playbook", tmp_path / "pb.json", "--ids", mid(A)]
    code, out, err, made = run_main(base, capsys, FakeClient())
    assert code == 1 and "error" in err and out == ""
    assert not (tmp_path / mid(A) / "prediction.json").exists()
    save_snapshot(tmp_path, B, resolved=[])
    code, out, err, made = run_main(
        ["train", "--root", tmp_path, "--playbook", tmp_path / "pb.json", "--ids",
         f"{mid(A)},{mid(B)}", "--knowledge-cutoff", CUTOFF], capsys, FakeClient())
    assert code == 1 and mid(B) in err and out == ""
    assert not (tmp_path / "pb.json").exists()
