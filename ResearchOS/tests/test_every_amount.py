"""Every amount, whatever the layout (v17): masking scales with scale_numbers."""

import random

import pytest
from tools.textmask import redact, replace_years_before, scale_money, scale_numbers, shift_years

from conftest import FakeLLM, make_case

from researchos.cases import Document
from researchos.masking import EARLIER_YEAR, MaskConfig, mask_case, pre_mask

FACTOR = 2.0
OFFSET = -8
NO_REDACTIONS = {"redactions": []}
GUESS_MISS = {"guesses": [{"company": "Globex", "ticker": "GBX", "confidence": 0.3}],
              "clues": []}

HISTORY = "Founded in 1987 with 1,500 staff."
# A release that printed every table cell on a line of its own.
CELLS = ("Three months ended\n2025\n2024\nRevenue\n$\n1,234\n950\n"
         "Gross margin\n41.0%\n39.5%")
# A table row that splits a negative as "(22" and ")" across two cells.
SPLIT = "Net loss | (22 | ) | (1,322) | 15"
# A slide deck flattened into a long line that keeps its figures inline.
SLIDE = ("Q4 highlights: 1,234 units shipped, 12,500 stores, revenue $1,200.0 million "
         "(up 12% from 2024), 2,000,000 members")
# Real figures of the case above that must not survive masking.
REAL = ("1,234", "12,500", "2,000,000", "1,322", "(22 |", "\n950\n", "1,500", "1,200.0")


def config(**kwargs):
    return MaskConfig(factor_range=(FACTOR, FACTOR), year_offset_range=(OFFSET, OFFSET),
                      **kwargs)


def amount_case():
    case = make_case()
    case.aliases = [*case.aliases, "Studio 1,234"]
    release = Document(kind="release", title="Studio 1,234 results: 12,500 stores",
                       text="\n".join([HISTORY, CELLS, SPLIT, SLIDE, "Studio 1,234 sales rose."]),
                       source="https://acme.example.com/ir")
    case.documents = [release, *case.documents[1:]]
    return case


def one_doc_case(text):
    case = make_case()
    case.documents = [Document(kind="release", title="Results", text=text)]
    return case


def reference(text, mapping, cutoff=None):
    """v16's order with v17's last step: names, years long past, money, year shift, amounts."""
    text = redact(text, mapping)
    if cutoff is not None:
        text = replace_years_before(text, cutoff, EARLIER_YEAR)
    return scale_numbers(shift_years(scale_money(text, FACTOR), OFFSET), FACTOR)


def test_pre_mask_scales_every_amount_whatever_the_layout():
    case = amount_case()
    docs, mapping, factor, offset = pre_mask(case, random.Random(0), config())
    assert (factor, offset) == (FACTOR, OFFSET)
    for masked, orig in zip(docs, case.documents):
        assert masked.text == reference(orig.text, mapping)
        assert masked.title == reference(orig.title, mapping)
        assert masked.source == ""
    release = docs[0].text
    # every cell on a line of its own: figures scaled after the year shift (950 * 2 is not
    # taken for a year and shifted), years heading the columns shifted and then left alone,
    # percentages left alone
    assert ("Three months ended\n2017\n2016\nRevenue\n$\n2,468\n1900\n"
            "Gross margin\n41.0%\n39.5%") in release
    assert "1892" not in release
    # a negative split across two cells
    assert "Net loss | (44 | ) | (2,644) | 30" in release
    # figures inline in a long line; money is scaled once, percentages stay, years shift
    assert ("Q4 highlights: 2,468 units shipped, 25,000 stores, revenue $2,400.0 million "
            "(up 12% from 2016), 4,000,000 members") in release
    assert "Founded in 1979 with 3,000 staff." in release
    # a name that contains an amount is redacted before any number is touched
    assert "Studio" not in release and "the Company sales rose." in release
    assert docs[0].title == "the Company results: 25,000 stores"
    for real in REAL:
        assert real not in release


def test_money_is_scaled_once():
    case = one_doc_case("Revenue was $1,200.0 million and $ 1,200 | $950")
    docs, _, _, _ = pre_mask(case, random.Random(0), config())
    assert docs[0].text == "Revenue was $2,400.0 million and $ 2,400 | $1900"


@pytest.mark.parametrize("history_years, expected", [
    (None, "Founded in 1979 with 3,000 staff."),
    (20, "Founded in an earlier year with 3,000 staff."),
])
def test_years_long_past_and_amounts(history_years, expected):
    case = one_doc_case(HISTORY)
    docs, mapping, _, _ = pre_mask(case, random.Random(0), config(history_years=history_years))
    assert docs[0].text == expected
    cutoff = None if history_years is None else 2026 - history_years
    assert docs[0].text == reference(HISTORY, mapping, cutoff)


def test_redaction_rounds_scale_every_amount_before_the_round_redaction():
    case = amount_case()
    masker = FakeLLM(responses=[{"redactions": [
        {"term": "25,000 stores", "replacement": "a store count", "reason": "a public figure"},
        {"term": "12,500", "replacement": "an original figure", "reason": "never shown"}]}])
    guesser = FakeLLM(responses=[GUESS_MISS])
    m = mask_case(case, masker, guesser, random.Random(0), config())
    assert m.status == "masked" and m.rounds == 1
    _, base, _, _ = pre_mask(case, random.Random(0), config())
    for masked, orig in zip(m.documents, case.documents):
        assert masked.text == redact(reference(orig.text, base), m.mapping)
        assert masked.title == redact(reference(orig.title, base), m.mapping)
        assert masked.source == ""
    release = m.documents[0].text
    # the masker's term, written as it saw the scaled prose, is matched ...
    assert "25,000 stores" in masker.calls[0]["prompt"]
    assert "2,468 units shipped, a store count, revenue" in release
    assert m.documents[0].title == "the Company results: a store count"
    # ... while a term naming an original figure matches nothing: the round redacts the
    # scaled text only
    assert "an original figure" not in release
    guessed = guesser.calls[0]["prompt"]
    assert "Net loss | (44 | ) | (2,644) | 30" in guessed
    assert "Revenue\n$\n2,468\n1900\n" in guessed
    for call in masker.calls + guesser.calls:
        for real in (*REAL, "Studio"):
            assert real not in call["prompt"]


def test_abstractor_receives_every_amount_scaled():
    case = amount_case()
    abstractor = FakeLLM(responses=[{"brief": "Revenue grew 12% from the prior year."}])
    m = mask_case(case, FakeLLM(responses=[NO_REDACTIONS]), FakeLLM(responses=[GUESS_MISS]),
                  random.Random(0), config(abstract=True), abstractor=abstractor)
    assert m.abstracted
    docs, _, _, _ = pre_mask(case, random.Random(0), config(abstract=True))
    assert len(abstractor.calls) == len(case.documents)
    for call, doc in zip(abstractor.calls, docs):
        assert doc.text in call["prompt"]
        for real in REAL:
            assert real not in call["prompt"]
    assert "Net loss | (44 | ) | (2,644) | 30" in abstractor.calls[0]["prompt"]
    assert "2,468 units shipped, 25,000 stores" in abstractor.calls[0]["prompt"]
