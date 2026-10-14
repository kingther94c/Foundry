"""Acceptance tests for ResearchOS, written from SPEC.md independently of the code.

They were held back from the agent that built the package: it saw only SPEC.md
and FOUNDRY.md, and these decided whether its attempt was accepted. Each case
traces to SPEC.md. Agents are fakes; nothing touches the network.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import random
import types
from dataclasses import asdict
from pathlib import Path

import pytest

from researchos import cases, llm, loop, masking, perspectives, playbook, reflect
from researchos.cases import Case, Document, Outcome
from tools import stats as tstats

IDENTITY = ["Zephyrex Corporation", "ZPX", "Zephyrex", "Aurora Cloud", "Dana Whitfield"]
RELEASE = ("Zephyrex Corporation (NASDAQ: ZPX) reported fiscal 2026 fourth-quarter revenue of "
           "$90.0 billion, up 18%. Aurora Cloud grew 40%. Helix Engine shipments doubled. "
           "CEO Dana Whitfield said Zephyrex's backlog reached $1,200 million.")
TRANSCRIPT = ("Dana Whitfield: Thanks. In FY26 we invested in Helix Engine. "
              "Analyst: Zephyrex guided to fiscal 2027 margins of 45%.")


def make_case(case_id="c1"):
    return Case(
        case_id=case_id, company="Zephyrex Corporation", ticker="ZPX",
        aliases=["Zephyrex", "Aurora Cloud", "Dana Whitfield", "zephyrex"],
        fiscal_period="Q4 FY2026", event_date="2026-07-30",
        documents=[Document("release", "Zephyrex Corporation Q4 FY2026 results", RELEASE,
                            "https://zephyrex.example.com/ir"),
                   Document("transcript", "ZPX call", TRANSCRIPT, "https://x.example/t")],
    )


OUTCOME = Outcome(abnormal_return_1d=0.0423, stock_return_1d=0.05, market_return_1d=0.0077,
                  notes="rose on cloud", sources=["https://news.example/z"])


class Fake:
    """An agent with the json() method the spec defines; replies come from a function."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def json(self, system, prompt, schema, *, prefix=None):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema, "prefix": prefix})
        return self.reply(len(self.calls), system, prompt, schema, prefix)


def everything_sent(fake):
    return " ".join(f"{c['system']} {c['prompt']} {c['prefix'] or ''}" for c in fake.calls)


# --- cases ---------------------------------------------------------------------

def test_case_round_trip_and_layout(tmp_path):
    case = make_case()
    cases.save_case(tmp_path, case, OUTCOME)
    assert (tmp_path / "c1" / "case.json").is_file()
    assert (tmp_path / "c1" / "outcome.json").is_file()
    assert asdict(cases.load_case(tmp_path, "c1")) == asdict(case)
    assert asdict(cases.load_outcome(tmp_path, "c1")) == asdict(OUTCOME)
    assert "abnormal_return_1d" not in (tmp_path / "c1" / "case.json").read_text(encoding="utf-8")


def test_load_case_never_reads_the_outcome(tmp_path):
    cases.save_case(tmp_path, make_case(), OUTCOME)
    (tmp_path / "c1" / "outcome.json").write_text("not json at all", encoding="utf-8")
    assert cases.load_case(tmp_path, "c1").company == "Zephyrex Corporation"
    (tmp_path / "c1" / "outcome.json").unlink()
    assert cases.load_case(tmp_path, "c1").ticker == "ZPX"


def test_list_cases(tmp_path):
    for cid in ["b", "a", "c"]:
        cases.save_case(tmp_path, make_case(cid), OUTCOME)
    (tmp_path / "not-a-case").mkdir()
    assert cases.list_cases(tmp_path) == ["a", "b", "c"]


def test_identity_terms_order_and_dedup():
    assert cases.identity_terms(make_case()) == IDENTITY


# --- masking: deterministic part ----------------------------------------------

def test_pre_mask_draw_order_and_text():
    config = masking.MaskConfig()
    docs, mapping, factor, offset = masking.pre_mask(make_case(), random.Random(1), config)
    ref = random.Random(1)
    assert factor == ref.uniform(*config.factor_range)
    assert offset == ref.randint(*config.year_offset_range)
    assert mapping["Zephyrex Corporation"] == "the Company"
    assert mapping["Zephyrex"] == "the Company"
    assert mapping["ZPX"] == "TICKER"
    release = docs[0].text
    for term in IDENTITY:
        assert term.lower() not in release.lower()
    assert "the Company (NASDAQ: TICKER)" in release
    assert f"fiscal {2026 + offset} fourth-quarter" in release
    assert "$" + format(90.0 * factor, ".1f") + " billion" in release
    assert "$" + format(1200 * factor, ",.0f") + " million" in release
    assert "up 18%" in release
    assert docs[0].source == "" and docs[1].source == ""
    assert docs[0].title == f"the Company Q4 FY{2026 + offset} results"
    assert [d.kind for d in docs] == ["release", "transcript"]


@pytest.mark.parametrize("raw,expected", [
    ("The Zephyrex Corporation, Inc.", "zephyrex"),
    ("Zephyrex Corp.", "zephyrex"),
    ("AT&T Inc.", "at t"),
    ("  Acme   Holdings Group plc ", "acme"),
])
def test_normalize_name(raw, expected):
    assert masking.normalize_name(raw) == expected


def test_is_identified():
    case = make_case()
    g = lambda company, ticker="": {"company": company, "ticker": ticker, "confidence": 0.5}
    assert masking.is_identified([g("Zephyrex Corp")], case, 3)
    assert masking.is_identified([g("Other Co", " zpx ")], case, 3)
    assert masking.is_identified([g("aurora cloud")], case, 3)                 # an alias
    assert not masking.is_identified([g("A"), g("B"), g("C"), g("Zephyrex")], case, 3)
    assert not masking.is_identified([g("Corporation")], case, 3)             # normalizes to ""
    assert not masking.is_identified([], case, 3)


def test_identifying_guess():
    case = make_case()
    g = lambda company, ticker="", confidence=0.5: {"company": company, "ticker": ticker, "confidence": confidence}
    guesses = [g("Other Co", confidence=0.9), g("Zephyrex Corp", confidence=0.4), g("x", " zpx ", 0.3)]
    assert masking.identifying_guess(guesses, make_case(), 3) is guesses[1]
    assert masking.identifying_guess(guesses, case, 1) is None
    assert masking.identifying_guess([g("A"), g("B"), g("C"), g("Zephyrex")], case, 3) is None
    assert masking.identifying_guess([], case, 3) is None


def confident_guesser(confidence, right=True):
    def reply(n, *a):
        return {"guesses": [{"company": "Other Co", "ticker": "", "confidence": 0.99},
                            {"company": "Zephyrex" if right else "Nobody", "ticker": "", "confidence": confidence}],
                "clues": [f"clue {n}"]}
    return Fake(reply)


@pytest.mark.parametrize("confidence,give_up,rounds", [
    (0.95, 0.9, 1),       # sure after one round: give up
    (0.9, 0.9, 1),        # at the threshold counts
    (0.5, 0.9, 4),        # identified, but not that sure: keep masking
    (0.95, None, 4),      # not configured: the old behaviour
    ("high", 0.9, 4),     # a confidence that is not a number never gives up
])
def test_masking_gives_up_early_when_the_guesser_is_sure(confidence, give_up, rounds):
    masker = Fake(lambda n, *a: {"redactions": []})
    guesser = confident_guesser(confidence)
    masked = masking.mask_case(make_case(), masker, guesser, random.Random(3),
                               masking.MaskConfig(give_up_confidence=give_up))
    assert masked.status == "identifiable" and masked.rounds == rounds
    assert len(masker.calls) == rounds and len(guesser.calls) == rounds
    assert masked.final_guesses[1]["confidence"] == confidence


def test_the_other_guesses_confidence_does_not_count():
    # The top guess is wrong at 0.99; the identifying guess is at 0.5.
    masked = masking.mask_case(make_case(), Fake(lambda n, *a: {"redactions": []}),
                               confident_guesser(0.5), random.Random(3),
                               masking.MaskConfig(max_rounds=2, give_up_confidence=0.9))
    assert masked.rounds == 2


# --- masking: the agent loop ----------------------------------------------------

def guesser_sequence(*identified):
    def reply(n, system, prompt, schema, prefix):
        right = identified[n - 1] if n <= len(identified) else False
        company = "Zephyrex Corp" if right else "Somebody Else"
        return {"guesses": [{"company": company, "ticker": "", "confidence": 0.7}],
                "clues": [f"clue-round-{n}: Helix Engine"]}
    return Fake(reply)


def test_mask_case_loops_until_the_guesser_fails():
    masker = Fake(lambda n, *a: {"redactions": [
        {"term": "Helix Engine", "replacement": "Product B", "reason": "product"}]})
    guesser = guesser_sequence(True, False)
    masked = masking.mask_case(make_case(), masker, guesser, random.Random(3))
    assert masked.status == "masked" and masked.rounds == 2
    assert len(masker.calls) == 2 and len(guesser.calls) == 2
    assert "clue-round-1" in masker.calls[1]["prompt"]
    assert masked.mapping["Helix Engine"] == "Product B"
    assert all("Helix Engine" not in d.text for d in masked.documents)
    assert masked.final_guesses[0]["company"] == "Somebody Else"
    sent = everything_sent(guesser).lower()
    for term in IDENTITY:
        assert term.lower() not in sent


def test_mask_case_gives_up_after_max_rounds():
    masker = Fake(lambda n, *a: {"redactions": []})
    guesser = guesser_sequence(True, True, True, True, True)
    masked = masking.mask_case(make_case(), masker, guesser, random.Random(3),
                               masking.MaskConfig(max_rounds=2))
    assert masked.status == "identifiable" and masked.rounds == 2
    assert len(masker.calls) == 2


def test_first_replacement_wins_and_empty_terms_are_ignored():
    def reply(n, *a):
        return {"redactions": [{"term": "Helix Engine", "replacement": f"P{n}", "reason": ""},
                               {"term": "", "replacement": "boom", "reason": ""}]}
    masked = masking.mask_case(make_case(), Fake(reply), guesser_sequence(True, False),
                               random.Random(3))
    assert masked.mapping["Helix Engine"] == "P1"
    assert "" not in masked.mapping


def test_the_guard_removes_identity_terms_whatever_the_agents_did():
    # The masker writes the company name back in through a replacement.
    masker = Fake(lambda n, *a: {"redactions": [
        {"term": "Helix Engine", "replacement": "Zephyrex flagship", "reason": ""}]})
    masked = masking.mask_case(make_case(), masker, guesser_sequence(False), random.Random(3))
    for doc in masked.documents:
        for term in IDENTITY:
            assert term.lower() not in doc.text.lower()


def test_save_and_load_masked_keep_the_key_apart(tmp_path):
    masked = masking.mask_case(make_case(), Fake(lambda n, *a: {"redactions": []}),
                               guesser_sequence(False), random.Random(3))
    masking.save_masked(tmp_path, masked)
    public = (tmp_path / "c1" / "masked.json").read_text(encoding="utf-8")
    assert (tmp_path / "c1" / "mask_key.json").is_file()
    assert "Zephyrex" not in public and "factor" not in public and "year_offset" not in public
    loaded = masking.load_masked(tmp_path, "c1")
    assert asdict(loaded) == asdict(masked)


# --- llm.ClaudeJSON -------------------------------------------------------------

class FakeClient:
    def __init__(self, response):
        self.response = response
        self.kwargs = []
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        return self.response


def response(blocks, stop="end_turn", details=None, usage=None):
    usage = usage or types.SimpleNamespace(input_tokens=10, output_tokens=5,
                                           cache_read_input_tokens=None,
                                           cache_creation_input_tokens=7)
    return types.SimpleNamespace(content=blocks, stop_reason=stop, stop_details=details, usage=usage)


def text(t):
    return types.SimpleNamespace(type="text", text=t)


THINKING = types.SimpleNamespace(type="thinking", thinking="", signature="sig")
SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"],
          "additionalProperties": False}


def test_claude_json_request_shape_with_prefix():
    client = FakeClient(response([THINKING, text('{"a": 1}')]))
    agent = llm.ClaudeJSON(client, effort="medium")
    assert agent.json("SYS", "the question", SCHEMA, prefix="the documents") == {"a": 1}
    kw = client.kwargs[0]
    assert kw["model"] == "claude-opus-5-5" and kw["max_tokens"] == 16000
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert kw["output_config"] == {"effort": "medium",
                                   "format": {"type": "json_schema", "schema": SCHEMA}}
    assert kw["system"] == "SYS"
    assert kw["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": "the documents", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "the question"}]}]
    for forbidden in ("thinking", "temperature", "top_p", "top_k", "tool_choice"):
        assert forbidden not in kw


def test_claude_json_without_prefix_and_usage_totals():
    client = FakeClient(response([text('{"a": 2}')]))
    agent = llm.ClaudeJSON(client)
    agent.json("S", "q", SCHEMA)
    agent.json("S", "q", SCHEMA)
    assert client.kwargs[0]["messages"] == [{"role": "user", "content": [{"type": "text", "text": "q"}]}]
    assert agent.usage == {"input_tokens": 20, "output_tokens": 10,
                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 14}


@pytest.mark.parametrize("resp", [
    response([], stop="refusal", details=types.SimpleNamespace(category="cyber", explanation="x")),
    response([], stop="refusal", details=None),
    response([text('{"a": 1')], stop="max_tokens"),
    response([text("not json")]),
    response([THINKING]),
])
def test_claude_json_errors(resp):
    with pytest.raises(llm.LLMError):
        llm.ClaudeJSON(FakeClient(resp)).json("S", "q", SCHEMA)


def test_refusal_message_names_the_category():
    resp = response([], stop="refusal", details=types.SimpleNamespace(category="cyber", explanation=""))
    with pytest.raises(llm.LLMError, match="cyber"):
        llm.ClaudeJSON(FakeClient(resp)).json("S", "q", SCHEMA)


# --- perspectives -----------------------------------------------------------------

def masked_case():
    return masking.MaskedCase(
        case_id="c1",
        documents=[Document("release", "the Company Q4", "Revenue of $45.0 billion rose 18%."),
                   Document("transcript", "call", "We invested in Product B.")],
        status="masked", rounds=1, mapping={"Zephyrex": "the Company"}, factor=0.5,
        year_offset=-5, final_guesses=[])


def view_reply(probs):
    def reply(n, system, prompt, schema, prefix):
        first = prompt.splitlines()[0]
        assert first.startswith("Perspective:"), first
        name = first.removeprefix("Perspective:").strip()
        assert name in perspectives.PERSPECTIVES, name
        return {"stance": "bullish", "prob_up": probs.get(name, 0.5), "conviction": "high",
                "key_points": [f"point from {name}"]}
    return reply


def test_analyze_one_call_per_perspective_sharing_the_prefix():
    probs = {"growth": 0.8, "profitability": 1.5, "guidance": -0.2}
    analyst = Fake(view_reply(probs))
    judge = Fake(lambda *a: {"prob_up": 0.62, "thesis": "growth wins"})
    book = playbook.Playbook.initial()
    a = perspectives.analyze(masked_case(), book, analyst, judge)
    assert len(analyst.calls) == len(perspectives.PERSPECTIVES)
    asked = sorted(c["prompt"].splitlines()[0] for c in analyst.calls)
    assert asked == sorted(f"Perspective: {p}" for p in perspectives.PERSPECTIVES)
    assert all(playbook.render_for_prompt(book) in c["prompt"] for c in analyst.calls)
    prefixes = {c["prefix"] for c in analyst.calls}
    assert len(prefixes) == 1
    (prefix,) = prefixes
    assert "Revenue of $45.0 billion rose 18%." in prefix and "Product B" in prefix
    assert list(a.views) == perspectives.PERSPECTIVES
    assert a.views["profitability"].prob_up == 0.99 and a.views["guidance"].prob_up == 0.01
    expected = tstats.combine_probabilities({k: v.prob_up for k, v in a.views.items()}, book.weights)
    assert a.prob_weighted == pytest.approx(expected)
    assert a.prob_judge == 0.62 and a.thesis == "growth wins"
    assert len(judge.calls) == 1 and "point from growth" in judge.calls[0]["prompt"]
    assert judge.calls[0]["prefix"] == prefix


def test_analyze_falls_back_to_the_mean_when_weights_are_zero():
    analyst = Fake(view_reply({"growth": 0.9}))
    judge = Fake(lambda *a: {"prob_up": 3.0, "thesis": ""})
    book = playbook.Playbook.initial()
    book.weights = {k: 0.0 for k in book.weights}
    a = perspectives.analyze(masked_case(), book, analyst, judge)
    views = [v.prob_up for v in a.views.values()]
    assert a.prob_weighted == pytest.approx(sum(views) / len(views))
    assert a.prob_judge == 0.99


# --- playbook ------------------------------------------------------------------

def test_playbook_initial_update_and_round_trip(tmp_path):
    book = playbook.Playbook.initial()
    n = len(perspectives.PERSPECTIVES)
    assert book.weights == {p: pytest.approx(1 / n) for p in perspectives.PERSPECTIVES}
    assert book.lessons == [] and book.history == []
    views = {p: perspectives.View("bullish", 0.9 if p == "growth" else 0.2, "high", [])
             for p in perspectives.PERSPECTIVES}
    before = dict(book.weights)
    playbook.update_weights(book, views, actual_up=True, eta=0.5)
    expected = tstats.hedge_update(before, {p: (v.prob_up - 1.0) ** 2 for p, v in views.items()}, 0.5)
    assert book.weights == {k: pytest.approx(v) for k, v in expected.items()}
    assert book.weights["growth"] > book.weights["risks"]
    path = tmp_path / "pb.json"
    book.lessons.append(playbook.Lesson("t", "w", "growth", "c1"))
    playbook.save_playbook(path, book)
    assert asdict(playbook.load_playbook(path)) == asdict(book)


def test_add_lessons_rejects_identifying_lessons():
    book = playbook.Playbook.initial()
    lessons = [playbook.Lesson("Cloud acceleration beats margin worries", "when backlog grows", "growth", "c1"),
               playbook.Lesson("zephyrex always beats", "", "growth", "c1"),
               playbook.Lesson("fine text", "when ZPX reports", "guidance", "c1")]
    rejected = playbook.add_lessons(book, lessons, IDENTITY)
    assert rejected == 2
    assert [l.text for l in book.lessons] == ["Cloud acceleration beats margin worries"]


def test_render_for_prompt():
    book = playbook.Playbook.initial()
    empty = playbook.render_for_prompt(book)
    assert isinstance(empty, str) and empty
    for p in perspectives.PERSPECTIVES:
        assert p in empty
    book.lessons = [playbook.Lesson(f"lesson-{i}", "", "general", "c") for i in range(3)]
    text = playbook.render_for_prompt(book, max_lessons=2)
    assert "lesson-1" in text and "lesson-2" in text and "lesson-0" not in text


# --- reflect -------------------------------------------------------------------

def test_reflect_maps_unknown_perspectives_and_sees_the_outcome():
    analysis = perspectives.Analysis("c1", {}, 0.4, 0.45, "t")
    reflector = Fake(lambda *a: {"why_market_moved": "cloud", "where_we_were_wrong": "margins",
                                 "lessons": [{"lesson": "L1", "applies_when": "W", "perspective": "valuation"},
                                             {"lesson": "L2", "applies_when": "W", "perspective": "growth"}]})
    lessons = reflect.reflect(make_case(), masked_case(), analysis, OUTCOME, reflector)
    assert [(l.text, l.perspective, l.case_id) for l in lessons] == \
        [("L1", "general", "c1"), ("L2", "growth", "c1")]
    assert "0.0423" in reflector.calls[0]["prompt"] or "4.23" in reflector.calls[0]["prompt"]


# --- loop ----------------------------------------------------------------------

def test_score():
    views = {"growth": perspectives.View("bullish", 0.8, "high", [])}
    s = loop.score(perspectives.Analysis("c1", views, 0.7, 0.4, ""), OUTCOME)
    assert s["actual_up"] is True
    assert s["brier_weighted"] == pytest.approx(0.09) and s["brier_judge"] == pytest.approx(0.36)
    assert s["brier_by_perspective"]["growth"] == pytest.approx(0.04)
    assert s["correct_weighted"] is True and s["correct_judge"] is False


def roles(identified=False):
    return loop.Roles(
        masker=Fake(lambda *a: {"redactions": []}),
        guesser=Fake(lambda *a: {"guesses": [{"company": "Zephyrex" if identified else "Nobody",
                                               "ticker": "", "confidence": 0.5}], "clues": []}),
        analyst=Fake(view_reply({"growth": 0.9, "risks": 0.1})),
        judge=Fake(lambda *a: {"prob_up": 0.7, "thesis": "x"}),
        reflector=Fake(lambda *a: {"why_market_moved": "m", "where_we_were_wrong": "w",
                                   "lessons": [{"lesson": "General lesson", "applies_when": "always",
                                                "perspective": "growth"},
                                               {"lesson": "Zephyrex specific", "applies_when": "",
                                                "perspective": "growth"}]}),
    )


@pytest.fixture()
def two_cases(tmp_path):
    cases.save_case(tmp_path, make_case("c1"), OUTCOME)
    cases.save_case(tmp_path, make_case("c2"), Outcome(abnormal_return_1d=-0.03))
    return tmp_path


def test_train_seals_the_prediction_before_reading_the_outcome(two_cases, monkeypatch):
    # Every time an outcome.json is opened for reading, the case's prediction.json
    # must already be on disk -- however it was written.
    reads = []
    real_open = io.open

    def spy(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, os.PathLike)):
            path = Path(file)
            if path.name == "outcome.json" and "w" not in mode and "a" not in mode:
                reads.append((path.parent.name, (path.parent / "prediction.json").is_file()))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", spy)
    monkeypatch.setattr(builtins, "open", spy)
    book_path = two_cases / "playbook.json"
    scores = loop.train(two_cases, ["c1", "c2"], book_path, roles())
    monkeypatch.undo()
    assert {cid for cid, _ in reads} == {"c1", "c2"}, reads
    assert all(sealed for _, sealed in reads), reads
    assert len(scores) == 2
    book = playbook.load_playbook(book_path)
    assert [h["case_id"] for h in book.history] == ["c1", "c2"]
    assert all(h["split"] == "train" for h in book.history)
    assert [l.text for l in book.lessons] == ["General lesson", "General lesson"]
    assert book.weights != playbook.Playbook.initial().weights
    assert (two_cases / "c1" / "prediction.json").is_file()


def test_train_reuses_masks_and_skips_identifiable_cases(two_cases):
    r = roles(identified=True)
    loop.train(two_cases, ["c1"], two_cases / "pb.json", r,
               mask_config=masking.MaskConfig(max_rounds=1))
    book = playbook.load_playbook(two_cases / "pb.json")
    assert book.history[0]["skipped"] == "identifiable"
    assert r.analyst.calls == [] and book.lessons == []
    # A saved mask is reused: no masker or guesser calls the second time.
    r2 = roles()
    loop.train(two_cases, ["c1"], two_cases / "pb2.json", r2)
    assert r2.masker.calls == [] and r2.guesser.calls == []


def test_evaluate_never_changes_the_playbook(two_cases):
    book_path = two_cases / "playbook.json"
    loop.train(two_cases, ["c1"], book_path, roles())
    before = book_path.read_bytes()
    r = roles()
    report = loop.evaluate(two_cases, ["c2"], book_path, r)
    assert book_path.read_bytes() == before
    assert r.reflector.calls == []
    assert report["n"] == 1
    assert report["brier_coin"] == pytest.approx(0.25)
    assert report["brier_judge"] == pytest.approx(0.49)          # judge says 0.7, it fell
    assert report["accuracy_judge"] == 0.0
    probs = [0.9 if p == "growth" else 0.1 if p == "risks" else 0.5 for p in perspectives.PERSPECTIVES]
    assert report["brier_uniform"] == pytest.approx((sum(probs) / len(probs)) ** 2)
    assert set(report) >= {"cases", "n", "brier_weighted", "brier_judge", "brier_uniform",
                           "brier_coin", "accuracy_weighted", "accuracy_judge"}


def test_nothing_identifying_or_revealing_reaches_the_analyst_or_judge(two_cases):
    r = roles()
    loop.train(two_cases, ["c1"], two_cases / "pb.json", r)
    sent = (everything_sent(r.analyst) + " " + everything_sent(r.judge)).lower()
    for term in IDENTITY:
        assert term.lower() not in sent
    assert "0.0423" not in sent and "4.23%" not in sent and "rose on cloud" not in sent


def test_evaluate_with_no_scored_case(tmp_path):
    cases.save_case(tmp_path, make_case("c1"), OUTCOME)
    book_path = tmp_path / "pb.json"
    playbook.save_playbook(book_path, playbook.Playbook.initial())
    report = loop.evaluate(tmp_path, ["c1"], book_path, roles(identified=True),
                           mask_config=masking.MaskConfig(max_rounds=1))
    assert report["n"] == 0 and report["brier_judge"] is None


def test_identifiable_case_after_the_knowledge_cutoff_is_used(two_cases):
    r = roles(identified=True)
    scores = loop.train(two_cases, ["c1"], two_cases / "pb.json", r,
                        mask_config=masking.MaskConfig(max_rounds=1), knowledge_cutoff="2026-06-30")
    assert len(scores) == 1 and scores[0]["identifiable"] is True and "skipped" not in scores[0]
    assert len(r.analyst.calls) == len(perspectives.PERSPECTIVES) and len(r.reflector.calls) == 1
    book = playbook.load_playbook(two_cases / "pb.json")
    assert book.history[0]["identifiable"] is True and book.lessons


def test_identifiable_case_not_after_the_knowledge_cutoff_is_skipped(two_cases):
    r = roles(identified=True)
    loop.train(two_cases, ["c1"], two_cases / "pb.json", r,
               mask_config=masking.MaskConfig(max_rounds=1), knowledge_cutoff="2026-07-30")
    assert playbook.load_playbook(two_cases / "pb.json").history[0]["skipped"] == "identifiable"
    assert r.analyst.calls == []


def test_scored_records_say_whether_masking_held(two_cases):
    scores = loop.train(two_cases, ["c1"], two_cases / "pb.json", roles())
    assert scores[0]["identifiable"] is False


def test_evaluate_counts_identifiable_cases(two_cases):
    book_path = two_cases / "pb.json"
    playbook.save_playbook(book_path, playbook.Playbook.initial())
    report = loop.evaluate(two_cases, ["c1", "c2"], book_path, roles(identified=True),
                           mask_config=masking.MaskConfig(max_rounds=1), knowledge_cutoff="2026-01-01")
    assert report["n"] == 2 and report["n_identifiable"] == 2
    assert all(c["identifiable"] is True for c in report["cases"] if "skipped" not in c)
    # The saved masks are identifiable; without a cutoff both are skipped.
    report = loop.evaluate(two_cases, ["c1", "c2"], book_path, roles())
    assert report["n"] == 0 and report["n_identifiable"] == 0 and report["brier_judge"] is None


# --- cli -----------------------------------------------------------------------

def test_cli_bad_arguments():
    from researchos.__main__ import main
    assert main(["no-such-command"], client_factory=lambda: None) == 2


def role_of(kwargs):
    props = kwargs["output_config"]["format"]["schema"]["properties"]
    for key, role in [("redactions", "masker"), ("guesses", "guesser"), ("stance", "analyst"),
                      ("thesis", "judge"), ("lessons", "reflector")]:
        if key in props:
            return role
    raise AssertionError(f"unknown schema {sorted(props)}")


class ScriptedClient:
    """A client for every role at once: the reply is chosen by the requested schema."""

    def __init__(self):
        self.kwargs = []
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        role = role_of(kwargs)
        if role == "masker":
            body = {"redactions": []}
        elif role == "guesser":
            body = {"guesses": [{"company": "Nobody", "ticker": "", "confidence": 0.1}], "clues": []}
        elif role == "analyst":
            body = {"stance": "neutral", "prob_up": 0.6, "conviction": "low", "key_points": ["k"]}
        elif role == "judge":
            body = {"prob_up": 0.55, "thesis": "t"}
        else:
            body = {"why_market_moved": "m", "where_we_were_wrong": "w",
                    "lessons": [{"lesson": "L", "applies_when": "W", "perspective": "growth"}]}
        usage = types.SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                                      cache_creation_input_tokens=0)
        return types.SimpleNamespace(content=[text(json.dumps(body))], stop_reason="end_turn",
                                     stop_details=None, usage=usage)


def test_cli_train_then_evaluate(two_cases, capsys):
    from researchos.__main__ import main
    client = ScriptedClient()
    pb = two_cases / "pb.json"
    assert main(["train", "--root", str(two_cases), "--playbook", str(pb), "--ids", "c1"],
                client_factory=lambda: client) == 0
    trained = json.loads(capsys.readouterr().out)
    assert len(trained) == 1 and trained[0]["actual_up"] is True
    assert playbook.load_playbook(pb).lessons[0].text == "L"
    efforts = {}
    for kw in client.kwargs:
        efforts.setdefault(role_of(kw), set()).add(kw["output_config"]["effort"])
    assert efforts == {"masker": {"medium"}, "guesser": {"medium"}, "analyst": {"high"},
                       "judge": {"high"}, "reflector": {"high"}}
    before = pb.read_bytes()
    assert main(["evaluate", "--root", str(two_cases), "--playbook", str(pb), "--ids", "c2",
                 "--seed", "7"], client_factory=lambda: client) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["n"] == 1 and report["brier_coin"] == pytest.approx(0.25)
    assert pb.read_bytes() == before


def test_cli_missing_case_exits_1(tmp_path, capsys):
    from researchos.__main__ import main
    code = main(["train", "--root", str(tmp_path), "--playbook", str(tmp_path / "pb.json"),
                 "--ids", "nope"], client_factory=ScriptedClient)
    assert code == 1
    assert capsys.readouterr().err.strip()


class IdentifyingClient(ScriptedClient):
    """Like ScriptedClient, but the guesser always names the company."""

    def create(self, **kwargs):
        if role_of(kwargs) == "guesser":
            self.kwargs.append(kwargs)
            body = {"guesses": [{"company": "Zephyrex Corp", "ticker": "ZPX", "confidence": 0.9}],
                    "clues": ["the segments"]}
            usage = types.SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                                          cache_creation_input_tokens=0)
            return types.SimpleNamespace(content=[text(json.dumps(body))], stop_reason="end_turn",
                                         stop_details=None, usage=usage)
        return super().create(**kwargs)


def test_cli_knowledge_cutoff(two_cases, capsys):
    from researchos.__main__ import main
    args = ["train", "--root", str(two_cases), "--playbook", str(two_cases / "pb.json"), "--ids", "c1"]
    assert main(args + ["--knowledge-cutoff", "2026-06-30"], client_factory=IdentifyingClient) == 0
    trained = json.loads(capsys.readouterr().out)
    assert trained[0]["identifiable"] is True
    assert main(args + ["--knowledge-cutoff", "June 2026"], client_factory=IdentifyingClient) == 2


def test_cli_mask_give_up(two_cases, capsys):
    from researchos.__main__ import main
    client = IdentifyingClient()
    args = ["train", "--root", str(two_cases), "--playbook", str(two_cases / "pb.json"), "--ids", "c1",
            "--knowledge-cutoff", "2026-06-30"]
    assert main(args + ["--mask-give-up", "0.8"], client_factory=lambda: client) == 0
    guesser_calls = [kw for kw in client.kwargs if role_of(kw) == "guesser"]
    assert len(guesser_calls) == 1          # 0.9 >= 0.8 after the first round
    assert json.loads(capsys.readouterr().out)[0]["identifiable"] is True
    for bad in ["0", "1.5", "-0.2", "sure"]:
        assert main(args + ["--mask-give-up", bad], client_factory=IdentifyingClient) == 2
