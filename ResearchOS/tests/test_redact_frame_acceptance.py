"""Acceptance tests for redacting what the masker sees (v15), written from SPEC.md independently
of the code (held back from the agent that built it).

Each case traces to the "Redacting what the masker sees (v15)" section of SPEC.md.
"""

from __future__ import annotations

import json
import random

from test_acceptance import Fake, make_case

from researchos import masking
from researchos.cases import Case, Document

# year_offset is fixed at -8 and the factor at 2.0 by the configs below.
RELEASE = ("Quillon Corp reported results. Since our inception in 2018 we have grown. "
           "Our partner Fordham Motors renewed. Revenue was $150 million. "
           "The program began in 2010 with a pilot. The card business grew 9%.")
TRANSCRIPT = "On the call: the card business and Fordham Motors again. Founded 1994 by Quill 1994 Holdings."


def case():
    return Case(case_id="q1", company="Quillon Corp", ticker="QLN", aliases=["Quill 1994 Holdings"],
                fiscal_period="Q2 2026", event_date="2026-07-30",
                documents=[Document("release", "Quillon Corp Q2 2026", RELEASE, "https://q.example"),
                           Document("transcript", "QLN call", TRANSCRIPT, "")])


def config(rounds=4):
    return masking.MaskConfig(max_rounds=rounds, factor_range=(2.0, 2.0), year_offset_range=(-8, -8))


def masker_rounds(*rounds):
    """Round n proposes rounds[n-1] (nothing after the last)."""
    def reply(n, *a):
        return {"redactions": [dict(term=t, replacement=r, reason="test") for t, r in
                               (rounds[n - 1] if n <= len(rounds) else [])]}
    return Fake(reply)


def guesser(identify_rounds):
    """Names the company in the first identify_rounds rounds, then fails."""
    def reply(n, *a):
        if n <= identify_rounds:
            return {"guesses": [{"company": "Quillon Corp", "ticker": "QLN", "confidence": 0.6}],
                    "clues": ["a clue"]}
        return {"guesses": [{"company": "Someone Else", "ticker": "", "confidence": 0.2}], "clues": []}
    return Fake(reply)


def body(masked):
    return "\n".join(d.title + "\n" + d.text for d in masked.documents)


def test_the_pre_masked_text_the_masker_sees():
    m = masker_rounds()
    masking.mask_case(case(), m, guesser(0), random.Random(1), config(1))
    shown = m.calls[0]["prompt"]
    assert "inception in 2010" in shown and "began in 2002" in shown and "$300 million" in shown


def test_a_term_with_a_shifted_year_is_redacted():
    g = guesser(0)
    masked = masking.mask_case(case(), masker_rounds([("inception in 2010", "inception")]), g,
                               random.Random(1), config(1))
    assert "inception in 2010" not in g.calls[0]["prompt"]
    assert "Since our inception we have grown" in body(masked)


def test_a_masker_year_never_redacts_the_original_year():
    # "2010" in the masked text is the original 2018; the original 2010 is shown as 2002.
    g = guesser(0)
    masked = masking.mask_case(case(), masker_rounds([("2010", "a year")]), g, random.Random(1), config(1))
    text = body(masked)
    assert "inception in a year" in text
    assert "began in 2002" in text
    assert "2010" not in g.calls[0]["prompt"]


def test_a_scaled_amount_is_redacted():
    g = guesser(0)
    masked = masking.mask_case(case(), masker_rounds([("$300 million", "an amount")]), g,
                               random.Random(1), config(1))
    assert "Revenue was an amount." in body(masked)
    assert "$300 million" not in g.calls[0]["prompt"]


def test_an_earlier_replacement_is_redacted_round_by_round(tmp_path):
    # v18: the round-2 term applies to the text round 1 left; replacements stay as given
    g = guesser(1)
    masked = masking.mask_case(
        case(),
        masker_rounds([("Fordham Motors", "an automotive partner")],
                      [("an automotive partner", "a partner")]),
        g, random.Random(1), config())
    assert masked.status == "masked" and masked.rounds == 2
    assert "an automotive partner" in g.calls[0]["prompt"]
    assert "automotive" not in g.calls[1]["prompt"]
    assert "Our partner a partner renewed" in body(masked)
    assert masked.mapping["Fordham Motors"] == "an automotive partner"
    assert masked.mapping["an automotive partner"] == "a partner"
    masking.save_masked(tmp_path, masked)
    key = json.loads((tmp_path / "q1" / "mask_key.json").read_text(encoding="utf-8"))
    assert key["mapping"]["Fordham Motors"] == "an automotive partner"


def test_a_replacement_containing_its_term_does_not_grow():
    g = guesser(4)
    masked = masking.mask_case(case(), masker_rounds([("card", "the card program")]), g,
                               random.Random(1), config())
    assert masked.rounds == 4 and masked.status == "identifiable"
    text = body(masked)
    assert text.count("the card program") == 2
    assert "card program program" not in text
    assert masked.mapping["card"] == "the card program"


def test_redactions_of_one_output_are_added_in_order():
    # v18: one output is one redact pass over the text before it; a later term in the same
    # output does not reach into an earlier term's replacement
    masked = masking.mask_case(
        case(),
        masker_rounds([("Fordham Motors", "an automotive partner"), ("automotive", "industrial")]),
        guesser(0), random.Random(1), config(1))
    assert list(masked.mapping)[-2:] == ["Fordham Motors", "automotive"]
    assert masked.mapping["Fordham Motors"] == "an automotive partner"
    assert "Our partner an automotive partner renewed" in body(masked)


def test_an_existing_term_keeps_its_place():
    masked = masking.mask_case(
        case(),
        masker_rounds([("Fordham Motors", "a partner")], [("FORDHAM MOTORS", "someone")]),
        guesser(1), random.Random(1), config())
    assert masked.mapping["Fordham Motors"] == "a partner"
    assert "FORDHAM MOTORS" not in masked.mapping


def test_a_name_containing_a_year_is_still_caught():
    masked = masking.mask_case(case(), masker_rounds(), guesser(0), random.Random(1), config(1))
    text = body(masked)
    assert "Holdings" not in text and "Quill" not in text
    assert "Founded 1986 by the Company." in text
    assert all(d.source == "" for d in masked.documents)


def test_abstraction_mode_redacts_the_briefs_as_before():
    briefs = Fake(lambda n, s, prompt, *a: {"brief": "A partner, an automotive partner, since 2010."})
    g = guesser(0)
    masked = masking.mask_case(case(), masker_rounds([("since 2010", "for years")]), g, random.Random(1),
                               masking.MaskConfig(max_rounds=1, factor_range=(2.0, 2.0),
                                                  year_offset_range=(-8, -8), abstract=True),
                               abstractor=briefs)
    assert [d.text for d in masked.documents] == ["A partner, an automotive partner, for years."] * 2


def test_the_shared_fixture_still_masks():
    # the earlier acceptance case keeps working end to end
    masked = masking.mask_case(make_case(), masker_rounds([("Helix Engine", "the product")]), guesser(0),
                               random.Random(3))
    assert masked.status == "masked"
