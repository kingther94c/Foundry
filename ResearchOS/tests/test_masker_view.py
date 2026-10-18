"""Redacting what the masker sees (SPEC v15), with the rounds applied in order (SPEC v18).

v18 replaced v15's rewriting of earlier replacements: the tests that pinned it now check
that each round is applied, in order, to the text the earlier rounds left, and that every
replacement stays exactly as the masker gave it.
"""

import json
import random

from tools.textmask import find_terms, redact, scale_money, shift_years

from conftest import FakeLLM

from researchos.masking import MaskConfig, load_masked, mask_case, pre_mask, save_masked

SEED = 1
GUESS_MISS = {"guesses": [{"company": "Globex", "ticker": "GBX", "confidence": 0.3}],
              "clues": []}
GUESS_HIT = {"guesses": [{"company": "Acme Widgets", "ticker": "", "confidence": 0.9}],
             "clues": ["the product line"]}


def redactions(*pairs):
    return {"redactions": [{"term": t, "replacement": r, "reason": "test"} for t, r in pairs]}


def pre(case):
    """pre_mask's mapping, factor and offset for the rng mask_case is given."""
    _, base, factor, offset = pre_mask(case, random.Random(SEED), MaskConfig())
    return base, factor, offset


def round_text(original, base, rounds, factor, offset):
    """v15's masking with base, then each round's redactions in order (v18)."""
    text = shift_years(scale_money(redact(original, base), factor), offset)
    for redactions in rounds:
        text = redact(text, redactions)
    return text


def run(case, masker_outputs, guesses, **config):
    masker = FakeLLM(responses=masker_outputs)
    guesser = FakeLLM(responses=guesses)
    m = mask_case(case, masker, guesser, random.Random(SEED), MaskConfig(**config))
    return m, masker


def all_text(docs):
    return "\n".join(d.title + "\n" + d.text for d in docs)


def test_round_documents_are_redacted_after_scaling_and_shifting(case):
    base, factor, offset = pre(case)
    m, _ = run(case, [redactions(("Zephyr", "flagship"))], [GUESS_MISS])
    assert (m.factor, m.year_offset) == (factor, offset)
    assert [d.kind for d in m.documents] == [d.kind for d in case.documents]
    assert m.redaction_rounds == [{"Zephyr": "flagship"}]
    for masked, orig in zip(m.documents, case.documents):
        assert masked.title == round_text(orig.title, base, m.redaction_rounds, factor, offset)
        assert masked.text == round_text(orig.text, base, m.redaction_rounds, factor, offset)
        assert masked.source == ""
    assert "demand for the flagship line was strong" in m.documents[0].text


def test_terms_with_scaled_amounts_and_shifted_years_match(case):
    _, factor, offset = pre(case)
    amount = scale_money("$1,200.0 million", factor)
    period = f"fourth quarter of {2025 + offset}"
    m, masker = run(case, [redactions((amount, "an amount"), (period, "quarter"))],
                    [GUESS_MISS])
    # the masker proposed them exactly as it was shown them
    assert amount in masker.calls[0]["prompt"] and period in masker.calls[0]["prompt"]
    release = m.documents[0].text
    assert amount not in release and period not in release
    assert "reported revenue of an amount for the quarter, up 12%" in release


def test_a_shifted_year_term_redacts_only_the_year_the_masker_saw(case):
    _, _, offset = pre(case)
    seen = str(2025 + offset)  # how the original 2025 reads to the masker
    # An original year that reads like `seen` before shifting; shifting moves it elsewhere.
    case.documents[2].text += f" The plant opened in {seen}."
    m, masker = run(case, [redactions((seen, "that year"))], [GUESS_MISS])
    assert f"fourth quarter of {seen}" in masker.calls[0]["prompt"]
    assert "for the fourth quarter of that year" in m.documents[0].text
    assert f"The plant opened in {2025 + 2 * offset}." in m.documents[2].text


def test_identity_names_holding_a_year_are_redacted_before_shifting(case):
    case.aliases.append("Vision 2025")
    case.documents[1].text += " Vision 2025 is on track."
    m, _ = run(case, [redactions(("Zephyr", "flagship"))], [GUESS_MISS])
    assert "Vision" not in all_text(m.documents)
    assert "the Company is on track." in m.documents[1].text


INNER_ROUNDS = [
    redactions(("Zephyr", "Orion product"), ("a record quarter", "an Orionis quarter")),
    redactions(("orion", "flagship")),
]


def test_a_term_inside_an_earlier_replacement_is_redacted_by_its_round(case):
    base, factor, offset = pre(case)
    m, masker = run(case, INNER_ROUNDS, [GUESS_HIT, GUESS_MISS])
    assert m.status == "masked" and m.rounds == 2
    # the masker was shown the earlier replacement and named a term inside it
    assert "demand for the Orion product line" in masker.calls[1]["prompt"]
    # every replacement is kept exactly as given (v18: no rewriting)
    assert m.mapping == {**base, "Zephyr": "Orion product",
                         "a record quarter": "an Orionis quarter", "orion": "flagship"}
    assert list(m.mapping) == [*base, "Zephyr", "a record quarter", "orion"]
    assert m.redaction_rounds == [{"Zephyr": "Orion product",
                                   "a record quarter": "an Orionis quarter"},
                                  {"orion": "flagship"}]
    assert "demand for the flagship product line was strong" in m.documents[0].text
    assert "delivered an Orionis quarter" in m.documents[1].text  # whole words only
    assert not find_terms(all_text(m.documents), ["Orion"])
    # the rounds applied in order give the final text
    for masked, orig in zip(m.documents, case.documents):
        assert masked.text == round_text(orig.text, base, m.redaction_rounds, factor, offset)


def test_own_replacement_is_kept_and_does_not_grow(case):
    m, masker = run(case, [redactions(("Zephyr", "Zephyr-like"))], [GUESS_HIT], max_rounds=3)
    assert m.rounds == 3 and len(masker.calls) == 3
    assert m.mapping["Zephyr"] == "Zephyr-like"
    # later rounds propose the same term again; it is already in the mapping
    assert m.redaction_rounds == [{"Zephyr": "Zephyr-like"}, {}, {}]
    assert "demand for the Zephyr-like line was strong" in m.documents[0].text


def test_redactions_of_one_output_are_added_in_order(case):
    m, _ = run(case, [redactions(("Zephyr", "Nova product"), ("Nova", "flagship"),
                                 ("NOVA", "ignored"))], [GUESS_MISS])
    assert m.mapping["Zephyr"] == "Nova product"
    assert m.mapping["Nova"] == "flagship" and "NOVA" not in m.mapping
    assert list(m.redaction_rounds[0].items()) == [("Zephyr", "Nova product"),
                                                   ("Nova", "flagship")]
    # one round is one redact pass: "Nova" was not in the text the masker saw
    assert "demand for the Nova product line was strong" in m.documents[0].text


def test_an_existing_term_is_skipped_and_its_replacement_kept(case):
    base, _, _ = pre(case)
    m, _ = run(case, [redactions(("Zephyr", "Nova product")),
                      redactions(("zephyr", "other"), ("Nova", "flagship"))],
               [GUESS_HIT, GUESS_MISS])
    assert list(m.mapping) == [*base, "Zephyr", "Nova"]
    assert m.mapping["Zephyr"] == "Nova product"
    assert m.redaction_rounds == [{"Zephyr": "Nova product"}, {"Nova": "flagship"}]
    assert "demand for the flagship product line was strong" in m.documents[0].text


def test_a_round_reaches_the_pre_mask_replacements_in_the_text(case):
    m, _ = run(case, [redactions(("Company", "business"))], [GUESS_MISS])
    # pre_mask's replacements stay as they were ...
    assert m.mapping["Acme"] == "the Company"
    assert m.mapping["Acme Widgets Inc."] == "the Company"
    assert m.mapping["ACMW"] == "TICKER"
    # ... while the round redacts the text they put in
    assert "the business (NASDAQ: TICKER) reported" in m.documents[0].text
    assert "Company" not in all_text(m.documents)


def test_mask_key_holds_the_mapping_as_given_and_the_rounds(tmp_path, case):
    m, _ = run(case, INNER_ROUNDS, [GUESS_HIT, GUESS_MISS])
    save_masked(tmp_path, m)
    key = json.loads((tmp_path / case.case_id / "mask_key.json").read_text(encoding="utf-8"))
    assert key["mapping"]["Zephyr"] == "Orion product"
    assert key["redaction_rounds"] == [{"Zephyr": "Orion product",
                                        "a record quarter": "an Orionis quarter"},
                                       {"orion": "flagship"}]
    loaded = load_masked(tmp_path, case.case_id)
    assert loaded.mapping == m.mapping
    assert loaded.redaction_rounds == m.redaction_rounds


def test_abstraction_mode_applies_the_rounds_to_the_briefs_in_order(case):
    brief = "Demand for Zephyr was strong; revenue grew 12% to $40 million in 2019."
    masker = FakeLLM(responses=[redactions(("Zephyr", "Orion")), redactions(("Orion", "flagship"))])
    m = mask_case(case, masker, FakeLLM(responses=[GUESS_HIT, GUESS_MISS]), random.Random(SEED),
                  MaskConfig(abstract=True), abstractor=FakeLLM(responses=[{"brief": brief}]))
    assert m.abstracted and m.status == "masked" and m.rounds == 2
    assert m.mapping["Zephyr"] == "Orion" and m.mapping["Orion"] == "flagship"
    assert m.redaction_rounds == [{"Zephyr": "Orion"}, {"Orion": "flagship"}]
    for doc in m.documents:
        # briefs are not scaled or shifted again
        assert doc.text == redact(redact(brief, {"Zephyr": "Orion"}), {"Orion": "flagship"})
        assert "Demand for flagship was strong" in doc.text
        assert "$40 million in 2019" in doc.text
