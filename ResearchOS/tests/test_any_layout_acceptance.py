"""Acceptance tests for every amount, whatever the layout (v17), written from SPEC.md independently
of the code (held back from the agent that built it)."""

from __future__ import annotations

import random

from test_acceptance import Fake

from researchos import masking
from researchos.cases import Case, Document

RELEASE = ("Quillon Corp had over 1,300 tenants and a 1,000 percent rise in 2026.\n"
           "|\n63,296\n|\n(1.5)\n2026\n"
           "Provision | | | (29,800 | ) | | | (22 | )\n"
           "Net | 1,000 | | 30 | %\n"
           "Slide | July 23, 2026 PPNR $461 total 1,234.5 and 3,000 bps\n"
           "Other | 1000 | | 400\n"
           "Revenue was $150 million.")


def case():
    return Case(case_id="q1", company="Quillon Corp", ticker="QLN", aliases=[], fiscal_period="Q2 2026",
                event_date="2026-07-30",
                documents=[Document("release", "Quillon Corp Q2 2026", RELEASE, ""),
                           Document("transcript", "QLN call", "We have 2,000 employees since 2010.", "")])


def config(**kw):
    return masking.MaskConfig(factor_range=(2.0, 2.0), year_offset_range=(-8, -8), **kw)


def test_pre_mask_scales_amounts_in_every_layout():
    docs, *_ = masking.pre_mask(case(), random.Random(0), config())
    text = docs[0].text
    assert "over 2,600 tenants and a 1,000 percent rise in 2018." in text
    assert "|\n126,592\n|\n(3.0)\n2018\n" in text          # a lone year line is shifted, not scaled
    assert "Provision | | | (59,600 | ) | | | (44 | )" in text
    assert "Net | 2,000 | | 30 | %" in text
    assert "Slide | July 23, 2018 PPNR $922 total 2,469.0 and 3,000 bps" in text
    assert "Other | 2000 | | 800" in text                   # scaled into the range of years, not shifted
    assert "Revenue was $300 million." in text
    assert docs[1].text == "We have 4,000 employees since 2002."


def test_history_years_still_applies():
    docs, *_ = masking.pre_mask(case(), random.Random(0), config(history_years=3))
    assert docs[1].text == "We have 4,000 employees since an earlier year."


def test_rounds_mask_the_same_way_then_redact():
    m = Fake(lambda n, *a: {"redactions": [{"term": "2,600 tenants", "replacement": "many tenants",
                                            "reason": "r"}]})
    g = Fake(lambda n, *a: {"guesses": [{"company": "Someone", "ticker": "", "confidence": 0.1}], "clues": []})
    masked = masking.mask_case(case(), m, g, random.Random(0), config(max_rounds=1))
    assert "over 2,600 tenants" in m.calls[0]["prompt"]
    text = masked.documents[0].text
    assert "over many tenants" in text and "|\n126,592\n|" in text
    assert "1,300" not in g.calls[0]["prompt"] and "63,296" not in g.calls[0]["prompt"]
