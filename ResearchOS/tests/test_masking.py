import json
import random

import pytest
from tools.textmask import find_terms, redact, scale_money, shift_years

from conftest import FakeLLM, make_case

from researchos.cases import identity_terms
from researchos.masking import (MaskConfig, is_identified, load_masked, mask_case,
                                normalize_name, pre_mask, save_masked)

GUESS_MISS = {"guesses": [{"company": "Globex", "ticker": "GBX", "confidence": 0.3}],
              "clues": []}
GUESS_HIT = {"guesses": [{"company": "ACME WIDGETS", "ticker": "", "confidence": 0.9}],
             "clues": ["mentions the Zephyr line"]}
NO_REDACTIONS = {"redactions": []}


def all_text(docs):
    return "\n".join(d.title + "\n" + d.text + "\n" + d.source for d in docs)


def test_pre_mask_draws_factor_then_offset_and_composes(case):
    config = MaskConfig()
    docs, mapping, factor, offset = pre_mask(case, random.Random(7), config)
    ref = random.Random(7)
    assert factor == ref.uniform(0.3, 3.0)
    assert offset == ref.randint(-9, -3)
    assert mapping == {"Acme Widgets Inc.": "the Company", "Acme": "the Company",
                       "Jane Roe": "the Company", "WidgetPro": "the Company",
                       "ACMW": "TICKER"}
    assert [d.kind for d in docs] == [d.kind for d in case.documents]
    for masked, orig in zip(docs, case.documents):
        assert masked.text == shift_years(scale_money(redact(orig.text, mapping), factor), offset)
        assert masked.title == shift_years(scale_money(redact(orig.title, mapping), factor),
                                           offset)
        assert masked.source == ""
    assert not find_terms(all_text(docs), identity_terms(case))
    assert "2025" not in docs[0].text and "$1,200.0" not in docs[0].text


def test_pre_mask_is_deterministic_per_rng(case):
    a = pre_mask(case, random.Random("0:x"), MaskConfig())
    b = pre_mask(case, random.Random("0:x"), MaskConfig())
    assert a == b


@pytest.mark.parametrize("raw, expected", [
    ("Apple Inc.", "apple"),
    ("The Coca-Cola Company", "coca cola"),
    ("  AT&T   Corp ", "at t"),
    ("Acme Holdings Group PLC", "acme"),
    ("The Company", ""),
    ("3M Co.", "3m"),
])
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def test_is_identified(case):
    assert is_identified([{"company": "acme widgets, inc", "ticker": "", "confidence": 1}],
                         case, 3)
    assert is_identified([{"company": "X", "ticker": " acmw ", "confidence": 1}], case, 3)
    assert is_identified([{"company": "WIDGETPRO", "ticker": "", "confidence": 1}], case, 3)
    late = [{"company": "A", "ticker": "A"}, {"company": "B", "ticker": "B"},
            {"company": "Acme", "ticker": ""}]
    assert is_identified(late, case, 3)
    assert not is_identified(late, case, 2)
    assert not is_identified([{"company": "The Company Inc", "ticker": ""}], case, 3)
    assert not is_identified([{"company": "", "ticker": ""}], case, 3)
    assert not is_identified([], case, 3)


def test_empty_ticker_guess_does_not_match_empty_ticker(case):
    case.ticker = ""
    assert not is_identified([{"company": "Globex", "ticker": ""}], case, 3)


def test_mask_case_stops_when_guesser_fails(case):
    masker = FakeLLM(responses=[{"redactions": [
        {"term": "Zephyr", "replacement": "the product line", "reason": "brand"},
        {"term": "  ", "replacement": "x", "reason": "empty"},
        {"term": "Acme", "replacement": "SHOULD NOT WIN", "reason": "dup"},
    ]}])
    guesser = FakeLLM(responses=[GUESS_MISS])
    m = mask_case(case, masker, guesser, random.Random(1))
    assert m.status == "masked" and m.rounds == 1
    assert len(masker.calls) == 1 and len(guesser.calls) == 1
    assert m.mapping["Zephyr"] == "the product line"
    assert m.mapping["Acme"] == "the Company"
    assert "  " not in m.mapping and "" not in m.mapping
    assert "Zephyr" not in all_text(m.documents)
    assert "the product line" in m.documents[0].text
    assert m.final_guesses == GUESS_MISS["guesses"]
    # re-masked from the original documents with the full mapping
    for masked, orig in zip(m.documents, case.documents):
        assert masked.text == shift_years(scale_money(redact(orig.text, m.mapping), m.factor),
                                          m.year_offset)


def test_mask_case_identifiable_after_max_rounds_and_clues_flow(case):
    masker = FakeLLM(responses=[NO_REDACTIONS])
    guesser = FakeLLM(responses=[GUESS_HIT])
    m = mask_case(case, masker, guesser, random.Random(1), MaskConfig(max_rounds=3))
    assert m.status == "identifiable" and m.rounds == 3
    assert len(masker.calls) == 3 and len(guesser.calls) == 3
    assert "mentions the Zephyr line" not in masker.calls[0]["prompt"]
    assert "mentions the Zephyr line" in masker.calls[1]["prompt"]
    assert "mentions the Zephyr line" in masker.calls[2]["prompt"]
    assert m.final_guesses == GUESS_HIT["guesses"]


def test_mask_case_succeeds_in_second_round(case):
    masker = FakeLLM(responses=[NO_REDACTIONS])
    guesser = FakeLLM(responses=[GUESS_HIT, GUESS_MISS])
    m = mask_case(case, masker, guesser, random.Random(1))
    assert m.status == "masked" and m.rounds == 2


def test_guard_removes_identity_reintroduced_by_masker(case):
    masker = FakeLLM(responses=[{"redactions": [
        {"term": "Zephyr", "replacement": "the ACMW Acme flagship", "reason": "bad"}]}])
    guesser = FakeLLM(responses=[GUESS_MISS])
    m = mask_case(case, masker, guesser, random.Random(1))
    assert not find_terms(all_text(m.documents), identity_terms(case))
    for call in guesser.calls:
        assert not find_terms(call["prompt"] + call["system"], identity_terms(case))


def test_guesser_never_sees_identity(case):
    masker = FakeLLM(responses=[NO_REDACTIONS])
    guesser = FakeLLM(responses=[GUESS_HIT])
    mask_case(case, masker, guesser, random.Random(3))
    for call in guesser.calls:
        text = call["system"] + call["prompt"] + (call["prefix"] or "")
        assert not find_terms(text, identity_terms(case))
        assert "acme.example.com" not in text and case.case_id not in text


def test_zero_rounds_is_identifiable(case):
    masker, guesser = FakeLLM(responses=[NO_REDACTIONS]), FakeLLM(responses=[GUESS_MISS])
    m = mask_case(case, masker, guesser, random.Random(1), MaskConfig(max_rounds=0))
    assert m.status == "identifiable" and m.rounds == 0 and m.final_guesses == []
    assert not masker.calls and not guesser.calls
    assert not find_terms(all_text(m.documents), identity_terms(case))


def test_save_and_load_masked_keep_key_separate(tmp_path, case):
    masker = FakeLLM(responses=[{"redactions": [
        {"term": "Zephyr", "replacement": "the product line", "reason": "brand"}]}])
    m = mask_case(case, masker, FakeLLM(responses=[GUESS_MISS]), random.Random(1))
    save_masked(tmp_path, m)
    public = json.loads((tmp_path / case.case_id / "masked.json").read_text(encoding="utf-8"))
    key = json.loads((tmp_path / case.case_id / "mask_key.json").read_text(encoding="utf-8"))
    assert set(public) == {"documents", "status", "rounds", "final_guesses"}
    assert set(key) == {"mapping", "factor", "year_offset"}
    assert "Acme" not in json.dumps(public["documents"])
    assert load_masked(tmp_path, case.case_id) == m
