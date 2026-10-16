"""Acceptance tests for tables and years long past (v16), written from SPEC.md independently of
the code (held back from the agent that built it).

Each case traces to the "Tables and years long past (v16)" section of SPEC.md.
"""

from __future__ import annotations

import json
import random
from dataclasses import replace

import pytest
from test_acceptance import OUTCOME, Fake, ScriptedClient, make_case

from researchos import cases, masking
from researchos.cases import Case, Document

RELEASE = ("Quillon Corp, founded in 1902, reported results for 2026.\n"
           "Net income | $ | 1,000 | | 800\n"
           "| 2026 | | 2025 |\n"
           "Other | 1000 | | 400\n"
           "Margin | 23.6 | % | | 23.9 | %\n"
           "Under the Reform Act of 1995, statements made in 2024 and 2022 are forward-looking. "
           "Revenue was $150 million.")
TRANSCRIPT = "Vision 1999 Holdings spoke. Since 2019 we have grown."


def case(event_date="2026-07-30"):
    return Case(case_id="q1", company="Quillon Corp", ticker="QLN", aliases=["Vision 1999 Holdings"],
                fiscal_period="Q2 2026", event_date=event_date,
                documents=[Document("release", "Quillon Corp Q2 2026", RELEASE, "https://q.example"),
                           Document("transcript", "QLN call", TRANSCRIPT, "")])


def config(**kw):
    return masking.MaskConfig(factor_range=(2.0, 2.0), year_offset_range=(-8, -8), **kw)


def silent():
    return Fake(lambda n, *a: {"redactions": []})


def missing():
    return Fake(lambda n, *a: {"guesses": [{"company": "Someone Else", "ticker": "", "confidence": 0.2}],
                               "clues": []})


def test_constants_and_config():
    assert masking.EARLIER_YEAR == "an earlier year"
    assert masking.MaskConfig().history_years is None


def test_pre_mask_scales_tables_after_shifting():
    docs, mapping, factor, offset = masking.pre_mask(case(), random.Random(0), config())
    text = docs[0].text
    assert (factor, offset) == (2.0, -8)
    assert "Net income | $ | 2,000 | | 1600" in text
    assert "| 2018 | | 2017 |" in text              # year headers shifted, not scaled
    assert "Other | 2000 | | 800" in text           # scaled into the range of years, not shifted
    assert "Margin | 23.6 | % | | 23.9 | %" in text
    assert "founded in 1894" in text and "Reform Act of 1987" in text   # history_years not set
    assert "Revenue was $300 million." in text
    assert docs[0].title == "the Company Q2 2018" and docs[0].source == ""


def test_years_long_past_become_an_earlier_year():
    docs, *_ = masking.pre_mask(case(), random.Random(0), config(history_years=3))
    release, transcript = docs[0].text, docs[1].text
    assert "founded in an earlier year, reported results for 2018." in release
    assert "Reform Act of an earlier year, statements made in 2016 and an earlier year" in release
    assert "| 2018 | | 2017 |" in release
    assert transcript == "the Company spoke. Since an earlier year we have grown."


def test_history_years_zero_keeps_only_the_event_year_and_later():
    docs, *_ = masking.pre_mask(case(), random.Random(0), config(history_years=0))
    assert "results for 2018." in docs[0].text
    assert "| 2018 | | an earlier year |" in docs[0].text


def test_rounds_use_the_same_masking_then_the_mapping():
    m = Fake(lambda n, *a: {"redactions": [{"term": "2,000", "replacement": "an amount", "reason": "r"},
                                           {"term": "an earlier year", "replacement": "long ago", "reason": "r"}]})
    g = missing()
    masked = masking.mask_case(case(), m, g, random.Random(0), config(history_years=3, max_rounds=1))
    shown = m.calls[0]["prompt"]
    assert "Net income | $ | 2,000 | | 1600" in shown and "founded in an earlier year" in shown
    text = masked.documents[0].text
    assert "Net income | $ | an amount | | 1600" in text
    assert "founded in long ago" in text and "Other | 2000 | | 800" in text
    assert "1,000" not in g.calls[0]["prompt"] and "1902" not in g.calls[0]["prompt"]


def test_negative_history_years_and_a_case_without_a_year():
    with pytest.raises(ValueError):
        masking.pre_mask(case(), random.Random(0), config(history_years=-1))
    with pytest.raises(ValueError):
        masking.mask_case(case(), silent(), missing(), random.Random(0), config(history_years=-1))
    with pytest.raises(ValueError):
        masking.pre_mask(case(event_date="soon"), random.Random(0), config(history_years=3))
    docs, *_ = masking.pre_mask(case(event_date="soon"), random.Random(0), config())
    assert "founded in 1894" in docs[0].text      # without history_years the event date is not read


def test_abstractor_sees_the_pre_masked_tables_and_years():
    briefs = Fake(lambda n, s, prompt, *a: {"brief": "A brief."})
    masking.mask_case(case(), silent(), missing(), random.Random(0),
                      config(history_years=3, abstract=True, max_rounds=1), abstractor=briefs)
    sent = " ".join(c["prompt"] for c in briefs.calls)
    assert "Net income | $ | 2,000 | | 1600" in sent
    assert "1902" not in sent and "1894" not in sent and "founded in an earlier year" in sent


def test_cli_mask_history_years(tmp_path, capsys):
    from researchos.__main__ import main
    old = make_case("c1")
    docs = [replace(d, text=d.text + " Founded in 1950.") for d in old.documents]
    cases.save_case(tmp_path, replace(old, documents=docs), OUTCOME)
    client = ScriptedClient()
    assert main(["train", "--root", str(tmp_path), "--playbook", str(tmp_path / "pb.json"), "--ids", "c1",
                 "--mask-history-years", "3", "--mask-give-up", "0.9"], client_factory=lambda: client) == 0
    capsys.readouterr()
    saved = masking.load_masked(tmp_path, "c1")
    assert all("Founded in an earlier year." in d.text for d in saved.documents)
    for bad in ["-1", "x", "2.5"]:
        assert main(["evaluate", "--root", str(tmp_path), "--playbook", str(tmp_path / "pb.json"),
                     "--ids", "c1", "--mask-history-years", bad], client_factory=lambda: client) == 2
