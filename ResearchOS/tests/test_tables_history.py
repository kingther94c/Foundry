"""Tables and years long past (v16): scale_table_numbers and replace_years_before in masking."""

import json
import random

import pytest
from tools.textmask import (redact, replace_years_before, scale_money, scale_numbers,
                            shift_years)

from conftest import FakeLLM, make_case, make_outcome, schema_client

from researchos.__main__ import main
from researchos.cases import Document, save_case
from researchos.masking import EARLIER_YEAR, MaskConfig, mask_case, pre_mask

FACTOR = 2.0
OFFSET = -8
NO_REDACTIONS = {"redactions": []}
GUESS_MISS = {"guesses": [{"company": "Globex", "ticker": "GBX", "confidence": 0.3}],
              "clues": []}

HISTORY = ("Founded in 1987, the firm was sued under the PSLRA 1995. In 2025 revenue was "
           "$1,200.0 million, up from 2024.")
TABLE = ("Three months ended | 2025 | 2024\n"
         "Revenue | $ | 1,234 | 950\n"
         "Gross margin | 41.0% | 39.5%")


def config(**kwargs):
    return MaskConfig(factor_range=(FACTOR, FACTOR), year_offset_range=(OFFSET, OFFSET),
                      **kwargs)


def table_case(event_date="2026-02-01"):
    case = make_case()
    case.event_date = event_date
    case.aliases = [*case.aliases, "Studio 1984"]
    release = Document(kind="release", title="Acme Widgets Inc. results since 1987",
                       text=HISTORY + "\n" + TABLE + "\nStudio 1984 sales rose.",
                       source="https://acme.example.com/ir")
    case.documents = [release, *case.documents[1:]]
    return case


def one_doc_case(text, event_date="2026-02-01"):
    case = make_case()
    case.event_date = event_date
    case.documents = [Document(kind="release", title="Results", text=text)]
    return case


def reference(text, mapping, cutoff):
    """The v16 order with v17's last step: names, years long past, money, year shift, amounts.

    v17 replaced the last step, scale_table_numbers, with scale_numbers.
    """
    text = redact(text, mapping)
    if cutoff is not None:
        text = replace_years_before(text, cutoff, EARLIER_YEAR)
    return scale_numbers(shift_years(scale_money(text, FACTOR), OFFSET), FACTOR)


def test_constant_and_default():
    assert EARLIER_YEAR == "an earlier year"
    assert MaskConfig().history_years is None


def test_pre_mask_drops_years_long_past_and_scales_tables():
    case = table_case()
    docs, mapping, factor, offset = pre_mask(case, random.Random(0), config(history_years=20))
    assert (factor, offset) == (FACTOR, OFFSET)
    for masked, orig in zip(docs, case.documents):
        assert masked.text == reference(orig.text, mapping, 2006)
        assert masked.title == reference(orig.title, mapping, 2006)
        assert masked.source == ""
    release = docs[0].text
    # years before 2026 - 20 are gone, and were dropped before the shift
    assert "Founded in an earlier year" in release and "PSLRA an earlier year" in release
    for year in ("1987", "1995", "1979"):
        assert year not in release
    # the remaining years are shifted
    assert "In 2017 revenue" in release and "up from 2016" in release
    # table numbers are scaled after the shift: 950 * 2 is not taken for a year, and the
    # years heading the columns are shifted, then left alone
    assert "Revenue | $ | 2,468 | 1900" in release
    assert "1,234" not in release and "1892" not in release
    assert "Three months ended | 2017 | 2016" in release
    assert "Gross margin | 41.0% | 39.5%" in release
    # a name that contains a year is redacted before any year is touched
    assert "Studio" not in release and "the Company sales rose." in release
    assert docs[0].title == "the Company results since an earlier year"


def test_without_history_years_nothing_about_years_changes():
    case = table_case()
    docs, mapping, _, _ = pre_mask(case, random.Random(0), config())
    for masked, orig in zip(docs, case.documents):
        assert masked.text == reference(orig.text, mapping, None)
    release = docs[0].text
    assert EARLIER_YEAR not in release
    assert "Founded in 1979" in release and "PSLRA 1987" in release
    # table numbers are scaled whether or not history_years is set
    assert "Revenue | $ | 2,468 | 1900" in release


@pytest.mark.parametrize("history_years, text, expected", [
    (20, "In 2005, 2006 and 2007.", "In an earlier year, 1998 and 1999."),
    (1, "In 2024 and 2025.", "In an earlier year and 2017."),
    (0, "In 2025 and 2026.", "In an earlier year and 2018."),
])
def test_cutoff_is_event_year_minus_history_years(history_years, text, expected):
    case = one_doc_case(text)
    docs, _, _, _ = pre_mask(case, random.Random(0), config(history_years=history_years))
    assert docs[0].text == expected


def test_negative_history_years_raises():
    case = table_case()
    with pytest.raises(ValueError):
        pre_mask(case, random.Random(0), config(history_years=-1))
    masker, guesser = FakeLLM(responses=[NO_REDACTIONS]), FakeLLM(responses=[GUESS_MISS])
    with pytest.raises(ValueError):
        mask_case(case, masker, guesser, random.Random(0), config(history_years=-1))
    assert not masker.calls and not guesser.calls


@pytest.mark.parametrize("event_date", ["", "26-02-01", "Feb 1, 2026", "202X-02-01", " 2026-02-01",
                                        None])
def test_event_date_without_a_four_digit_year_raises_only_with_history_years(event_date):
    case = table_case(event_date)
    with pytest.raises(ValueError):
        pre_mask(case, random.Random(0), config(history_years=5))
    masker, guesser = FakeLLM(responses=[NO_REDACTIONS]), FakeLLM(responses=[GUESS_MISS])
    with pytest.raises(ValueError):
        mask_case(case, masker, guesser, random.Random(0), config(history_years=5))
    assert not masker.calls and not guesser.calls
    # without history_years the event date plays no part in masking
    docs, _, _, _ = pre_mask(case, random.Random(0), config())
    assert "PSLRA 1987" in docs[0].text


def test_redaction_rounds_mask_the_originals_the_same_way():
    case = table_case()
    masker = FakeLLM(responses=[{"redactions": [
        {"term": "2,468", "replacement": "a figure", "reason": "a public figure"}]}])
    guesser = FakeLLM(responses=[GUESS_MISS])
    m = mask_case(case, masker, guesser, random.Random(0), config(history_years=20))
    assert m.status == "masked" and m.rounds == 1
    _, base, _, _ = pre_mask(case, random.Random(0), config(history_years=20))
    for masked, orig in zip(m.documents, case.documents):
        assert masked.text == redact(reference(orig.text, base, 2006), m.mapping)
        assert masked.title == redact(reference(orig.title, base, 2006), m.mapping)
        assert masked.source == ""
    # the masker's term, written as it saw the scaled table, is matched
    assert "2,468" in masker.calls[0]["prompt"]
    assert "Revenue | $ | a figure | 1900" in m.documents[0].text
    for call in masker.calls + guesser.calls:
        prompt = call["prompt"]
        assert EARLIER_YEAR in prompt
        for real in ("1987", "1995", "1,234", "Studio"):
            assert real not in prompt


def test_abstractor_receives_pre_masked_documents():
    case = table_case()
    abstractor = FakeLLM(responses=[{"brief": "Revenue grew 12% from the prior year."}])
    m = mask_case(case, FakeLLM(responses=[NO_REDACTIONS]), FakeLLM(responses=[GUESS_MISS]),
                  random.Random(0), config(history_years=20, abstract=True),
                  abstractor=abstractor)
    assert m.abstracted
    docs, _, _, _ = pre_mask(case, random.Random(0), config(history_years=20, abstract=True))
    assert len(abstractor.calls) == len(case.documents)
    for call, doc in zip(abstractor.calls, docs):
        assert doc.text in call["prompt"]
        for real in ("1987", "1995", "1,234"):
            assert real not in call["prompt"]
    assert "Revenue | $ | 2,468 | 1900" in abstractor.calls[0]["prompt"]
    assert "PSLRA an earlier year" in abstractor.calls[0]["prompt"]


# ---- CLI --------------------------------------------------------------------------

def setup_root(tmp_path):
    root = tmp_path / "cases"
    save_case(root, make_case("a"), make_outcome(ar=0.02))
    return root


def no_client():
    raise AssertionError("no client may be built on bad arguments")


def test_cli_mask_history_years_is_passed_on(tmp_path, capsys, monkeypatch):
    import researchos.__main__ as main_mod

    root = setup_root(tmp_path)
    seen = []

    def fake_run(root_, ids, playbook, roles, seed=0, mask_config=None, knowledge_cutoff=None):
        seen.append(mask_config)
        return {"n": 0}

    monkeypatch.setattr(main_mod, "train", fake_run)
    monkeypatch.setattr(main_mod, "evaluate", fake_run)
    args = ["--root", str(root), "--playbook", str(tmp_path / "pb.json"), "--ids", "a"]
    assert main(["train", *args, "--mask-history-years", "10"],
                client_factory=schema_client) == 0
    assert main(["evaluate", *args, "--mask-history-years", "0"],
                client_factory=schema_client) == 0
    assert main(["evaluate", *args, "--mask-history-years", "5", "--mask-give-up", "0.9",
                 "--mask-abstract"], client_factory=schema_client) == 0
    capsys.readouterr()
    assert seen == [MaskConfig(history_years=10), MaskConfig(history_years=0),
                    MaskConfig(history_years=5, give_up_confidence=0.9, abstract=True)]


@pytest.mark.parametrize("bad", ["-1", "x", "1.5", ""])
def test_cli_bad_history_years_is_a_bad_argument(tmp_path, capsys, bad):
    root = setup_root(tmp_path)
    for cmd in ("train", "evaluate"):
        code = main([cmd, "--root", str(root), "--playbook", str(tmp_path / "pb.json"),
                     "--ids", "a", "--mask-history-years", bad], client_factory=no_client)
        assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--mask-history-years" in captured.err
    assert not (root / "a" / "masked.json").exists()


def test_cli_train_masks_years_long_past(tmp_path, capsys):
    root = setup_root(tmp_path)  # event_date 2026-02-01; the release says "up 12% from 2024"
    client = schema_client()
    code = main(["train", "--root", str(root), "--playbook", str(tmp_path / "pb.json"),
                 "--ids", "a", "--mask-history-years", "1"], client_factory=lambda: client)
    assert code == 0
    assert [s["case_id"] for s in json.loads(capsys.readouterr().out)] == ["a"]
    masked = json.loads((root / "a" / "masked.json").read_text(encoding="utf-8"))
    assert "up 12% from an earlier year" in masked["documents"][0]["text"]
    assert "from 2024" not in str(client.requests)
