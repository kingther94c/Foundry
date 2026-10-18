"""Redaction rounds in order (SPEC v18)."""

import json
import random

from tools.textmask import redact, scale_money, scale_numbers, shift_years

from conftest import FakeLLM

from researchos.cases import Case, Document
from researchos.masking import MaskConfig, MaskedCase, load_masked, mask_case, save_masked
from researchos.jsonio import write_json

FACTOR = 2.0
OFFSET = -8
RELEASE = ("Pathway Inc. reported results. ClearPath Forward revenue grew 20%. "
           "ClearPath margins improved. Revenue was $150 million in 2025.")
MISS = {"guesses": [{"company": "Someone Else", "ticker": "", "confidence": 0.2}], "clues": []}
HIT = {"guesses": [{"company": "Pathway Inc.", "ticker": "PTH", "confidence": 0.6}],
       "clues": ["a clue"]}


def redactions(*pairs):
    return {"redactions": [{"term": t, "replacement": r, "reason": "test"} for t, r in pairs]}


def make_case():
    return Case(case_id="p1", company="Pathway Inc.", ticker="PTH", aliases=[],
                fiscal_period="Q2 2026", event_date="2026-07-30",
                documents=[Document("release", "Pathway Inc. ClearPath results", RELEASE, "u"),
                           Document("transcript", "PTH call", "ClearPath Forward again.", "")])


def config(**kwargs):
    return MaskConfig(factor_range=(FACTOR, FACTOR), year_offset_range=(OFFSET, OFFSET), **kwargs)


def run(masker_outputs, guesses, **kwargs):
    masker = FakeLLM(responses=masker_outputs)
    guesser = FakeLLM(responses=guesses)
    m = mask_case(make_case(), masker, guesser, random.Random(0), config(**kwargs))
    return m, masker, guesser


def masked_with_base(text):
    base = {"Pathway Inc.": "the Company", "PTH": "TICKER"}
    return scale_numbers(shift_years(scale_money(redact(text, base), FACTOR), OFFSET), FACTOR)


def test_a_term_wrapping_an_earlier_replacement_is_redacted():
    m, masker, guesser = run(
        [redactions(("ClearPath", "the platform")),
         redactions(("the platform Forward revenue", "revenue from the newest offering"))],
        [HIT, MISS])
    assert m.status == "masked" and m.rounds == 2
    # the masker proposed the round-2 term as it read the text round 1 left
    assert "the platform Forward revenue grew 20%" in masker.calls[1]["prompt"]
    release = m.documents[0].text
    assert "revenue from the newest offering grew 20%" in release
    assert "Forward" not in release
    assert "Forward revenue" not in guesser.calls[1]["prompt"]
    assert "the platform margins improved" in release
    # the transcript's "the platform Forward again." is not covered by the term
    assert m.documents[1].text == "the platform Forward again."
    # "the platform Forward revenue" is nowhere in the text before round 1 is applied
    assert m.redaction_rounds == [{"ClearPath": "the platform"},
                                  {"the platform Forward revenue":
                                   "revenue from the newest offering"}]


def test_round_documents_apply_each_round_in_order():
    rounds = [redactions(("ClearPath", "the platform")),
              redactions(("the platform Forward", "the new offering"), ("margins", "profitability")),
              redactions(("new offering", "latest product"))]
    m, _, _ = run(rounds, [HIT, HIT, MISS])
    assert m.rounds == 3
    R1, R2, R3 = m.redaction_rounds
    case = make_case()
    for masked, orig in zip(m.documents, case.documents):
        for got, text in ((masked.title, orig.title), (masked.text, orig.text)):
            assert got == redact(redact(redact(masked_with_base(text), R1), R2), R3)
    assert m.documents[0].text.startswith("the Company reported results. the latest product "
                                          "revenue grew 20%. the platform profitability improved.")
    assert "Revenue was $300 million in 2017." in m.documents[0].text
    assert m.documents[1].text == "the latest product again."


def test_mapping_holds_every_redaction_as_given_and_rounds_only_the_new_ones():
    m, _, _ = run([redactions(("pathway inc.", "skipped"), ("ClearPath", "the platform"),
                              ("CLEARPATH", "skipped too")),
                   redactions(("clearpath", "again"), ("platform", "product"))],
                  [HIT, MISS])
    # a term from pre_mask, an earlier round or earlier in the same output is not added again
    assert m.redaction_rounds == [{"ClearPath": "the platform"}, {"platform": "product"}]
    assert m.mapping == {"Pathway Inc.": "the Company", "PTH": "TICKER",
                         "ClearPath": "the platform", "platform": "product"}
    assert "the product margins improved" in m.documents[0].text


def test_a_round_that_adds_nothing_keeps_its_empty_dict():
    m, _, _ = run([redactions(), redactions(("ClearPath", "the platform")), redactions()],
                  [HIT, HIT, MISS])
    assert m.redaction_rounds == [{}, {"ClearPath": "the platform"}, {}]


def test_abstraction_mode_applies_the_rounds_to_the_briefs():
    brief = "Sales of ClearPath Forward rose 9% in 2017."
    abstractor = FakeLLM(responses=[{"brief": brief}])
    masker = FakeLLM(responses=[redactions(("ClearPath", "the platform")),
                                redactions(("the platform Forward", "the new offering"))])
    m = mask_case(make_case(), masker, FakeLLM(responses=[HIT, MISS]), random.Random(0),
                  config(abstract=True), abstractor=abstractor)
    assert m.abstracted and m.rounds == 2
    assert [d.text for d in m.documents] == ["Sales of the new offering rose 9% in 2017."] * 2


def test_redaction_rounds_saved_in_the_key_and_loaded(tmp_path):
    m, _, _ = run([redactions(("ClearPath", "the platform")), redactions()], [HIT, MISS])
    save_masked(tmp_path, m)
    key = json.loads((tmp_path / "p1" / "mask_key.json").read_text(encoding="utf-8"))
    assert key["redaction_rounds"] == [{"ClearPath": "the platform"}, {}]
    public = json.loads((tmp_path / "p1" / "masked.json").read_text(encoding="utf-8"))
    assert "redaction_rounds" not in public
    assert load_masked(tmp_path, "p1").redaction_rounds == [{"ClearPath": "the platform"}, {}]


def test_load_masked_without_redaction_rounds_gives_empty(tmp_path):
    m, _, _ = run([redactions(("ClearPath", "the platform"))], [MISS])
    save_masked(tmp_path, m)
    key_path = tmp_path / "p1" / "mask_key.json"
    key = json.loads(key_path.read_text(encoding="utf-8"))
    del key["redaction_rounds"]
    write_json(key_path, key)
    assert load_masked(tmp_path, "p1").redaction_rounds == []


def test_masked_case_defaults_to_no_rounds():
    m = MaskedCase(case_id="x", documents=[], status="masked", rounds=0, mapping={},
                   factor=1.0, year_offset=0)
    assert m.redaction_rounds == []
    assert MaskedCase(case_id="y", documents=[], status="masked", rounds=0, mapping={},
                      factor=1.0, year_offset=0).redaction_rounds is not m.redaction_rounds


def test_order_preserved_within_a_round():
    m, _, _ = run([redactions(("margins", "profitability"), ("ClearPath", "the platform"),
                              ("Forward", "Next"))], [MISS])
    assert list(m.redaction_rounds[0]) == ["margins", "ClearPath", "Forward"]
    assert list(m.mapping)[-3:] == ["margins", "ClearPath", "Forward"]
    # the identity guard's terms are in the mapping, not in a round
    assert all("Pathway" not in k for r in m.redaction_rounds for k in r)


def test_nested_and_flat_differ_only_where_a_term_wraps_a_replacement():
    # Applying the full mapping once would leave "the platform Forward" untouched.
    m, _, _ = run([redactions(("ClearPath", "the platform")),
                   redactions(("the platform Forward", "the new offering"))], [HIT, MISS])
    flat = redact(masked_with_base(RELEASE), m.mapping)
    assert "the platform Forward" in flat
    assert "the platform Forward" not in m.documents[0].text
    assert json.dumps(m.mapping)  # plain strings, serialisable
