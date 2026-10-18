"""Acceptance tests for redaction rounds in order (v18), written from SPEC.md independently of the
code (held back from the agent that built it)."""

from __future__ import annotations

import json
import random

from test_acceptance import Fake

from researchos import masking
from researchos.cases import Case, Document

RELEASE = ("Quillon Corp reported results. ClearPath Forward revenue rose 9%. "
           "Our partner Fordham Motors renewed, and Fordham Motors grew.")
TRANSCRIPT = "The ClearPath Forward line and Fordham Motors came up on the call."


def case():
    return Case(case_id="q1", company="Quillon Corp", ticker="QLN", aliases=[], fiscal_period="Q2 2026",
                event_date="2026-07-30",
                documents=[Document("release", "Quillon Corp Q2 2026", RELEASE, ""),
                           Document("transcript", "QLN call", TRANSCRIPT, "")])


def config(rounds=4, **kw):
    return masking.MaskConfig(max_rounds=rounds, factor_range=(2.0, 2.0), year_offset_range=(-8, -8), **kw)


def masker_rounds(*rounds):
    def reply(n, *a):
        return {"redactions": [dict(term=t, replacement=r, reason="test") for t, r in
                               (rounds[n - 1] if n <= len(rounds) else [])]}
    return Fake(reply)


def guesser(identify_rounds):
    def reply(n, *a):
        if n <= identify_rounds:
            return {"guesses": [{"company": "Quillon Corp", "ticker": "QLN", "confidence": 0.6}],
                    "clues": ["a clue"]}
        return {"guesses": [{"company": "Someone Else", "ticker": "", "confidence": 0.2}], "clues": []}
    return Fake(reply)


def body(masked):
    return "\n".join(d.title + "\n" + d.text for d in masked.documents)


def test_a_term_around_an_earlier_replacement_applies():
    m = masker_rounds([("ClearPath", "the platform")], [("the platform Forward revenue", "core revenue")])
    g = guesser(1)
    masked = masking.mask_case(case(), m, g, random.Random(1), config())
    assert "the platform Forward revenue" in m.calls[1]["prompt"]
    assert "core revenue rose 9%" in body(masked)
    assert "Forward" not in g.calls[1]["prompt"].split("TICKER call")[0]   # release redacted
    assert "The the platform Forward line" in masked.documents[1].text     # no such phrase there


def test_redaction_rounds_are_recorded_and_saved(tmp_path):
    m = masker_rounds([("ClearPath", "the platform"), ("Fordham Motors", "a partner")],
                      [], [("the platform Forward", "the product"), ("clearpath", "x")])
    masked = masking.mask_case(case(), m, guesser(2), random.Random(1), config())
    assert masked.rounds == 3 and masked.status == "masked"
    assert masked.redaction_rounds == [{"ClearPath": "the platform", "Fordham Motors": "a partner"}, {},
                                       {"the platform Forward": "the product"}]
    assert masked.mapping["ClearPath"] == "the platform"
    assert "clearpath" not in masked.mapping and "x" not in masked.mapping.values()
    assert "the product revenue rose 9%" in masked.documents[0].text
    masking.save_masked(tmp_path, masked)
    key = json.loads((tmp_path / "q1" / "mask_key.json").read_text(encoding="utf-8"))
    assert key["redaction_rounds"] == masked.redaction_rounds
    back = masking.load_masked(tmp_path, "q1")
    assert back.redaction_rounds == masked.redaction_rounds
    assert "redaction_rounds" not in (tmp_path / "q1" / "masked.json").read_text(encoding="utf-8")


def test_old_keys_load_with_no_rounds(tmp_path):
    masked = masking.mask_case(case(), masker_rounds([("ClearPath", "x")]), guesser(0), random.Random(1),
                               config(1))
    masking.save_masked(tmp_path, masked)
    path = tmp_path / "q1" / "mask_key.json"
    key = json.loads(path.read_text(encoding="utf-8"))
    del key["redaction_rounds"]
    path.write_text(json.dumps(key), encoding="utf-8")
    assert masking.load_masked(tmp_path, "q1").redaction_rounds == []


def test_a_term_inside_an_earlier_replacement_still_applies_and_replacements_stay():
    m = masker_rounds([("Fordham Motors", "an automotive partner")], [("automotive", "industrial")])
    masked = masking.mask_case(case(), m, guesser(1), random.Random(1), config())
    assert "Our partner an industrial partner renewed, and an industrial partner grew." in body(masked)
    assert masked.mapping["Fordham Motors"] == "an automotive partner"


def test_rounds_apply_in_order_not_longest_first():
    # round 1 takes "Fordham"; round 2's longer "Fordham Motors" no longer occurs
    m = masker_rounds([("Fordham", "Acme")], [("Fordham Motors", "a partner"), ("Acme Motors", "a firm")])
    masked = masking.mask_case(case(), m, guesser(1), random.Random(1), config())
    assert "Our partner a firm renewed" in body(masked)


def test_abstraction_mode_applies_rounds_in_order_to_the_briefs():
    brief = "ClearPath Forward revenue grew; Fordham Motors renewed."
    m = masker_rounds([("ClearPath", "the platform")], [("the platform Forward revenue", "core revenue")])
    masked = masking.mask_case(case(), m, guesser(1), random.Random(1), config(abstract=True),
                               abstractor=Fake(lambda n, *a: {"brief": brief}))
    assert [d.text for d in masked.documents] == ["core revenue grew; Fordham Motors renewed."] * 2
    assert masked.redaction_rounds == [{"ClearPath": "the platform"},
                                       {"the platform Forward revenue": "core revenue"}]
