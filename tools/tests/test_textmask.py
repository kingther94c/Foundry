import pytest

from tools import textmask as t


def test_redact_whole_words_and_case():
    assert t.redact("Acme's CEO met acme.", {"Acme": "X"}) == "X's CEO met X."
    assert t.redact("Acmes and BigAcme", {"Acme": "X"}) == "Acmes and BigAcme"
    assert t.redact("acme", {"Acme": "X"}, case_sensitive=True) == "acme"


def test_redact_longest_key_wins_and_no_rescan():
    mapping = {"Acme": "Company", "Acme Cloud": "Product"}
    assert t.redact("Acme Cloud by Acme", mapping) == "Product by Company"
    assert t.redact("Acme and Beta", {"Acme": "Beta", "Beta": "Gamma"}) == "Beta and Gamma"


def test_redact_literal_and_empty_keys():
    assert t.redact("a.b axb", {"a.b": "Z", "": "nope"}) == "Z axb"
    assert t.redact("price (USD)", {"(USD)": "[cur]"}) == "price [cur]"
    assert t.redact("text", {}) == "text"


def test_redact_kelvin_sign_is_not_ascii_letter():
    # U+212A folds to 'k' under IGNORECASE but is not an ASCII letter.
    assert t.redact("\u212aAcme", {"Acme": "X"}) == "\u212aX"


def test_find_terms():
    text = "Acme's rival, Beta Corp, beat ACME."
    assert t.find_terms(text, ["Beta", "Gamma", "acme", "Beta", "eta"]) == ["Beta", "acme"]
    assert t.find_terms(text, ["acme"], case_sensitive=True) == []


@pytest.mark.parametrize(
    "src, expected",
    [
        ("$90.0 billion", "$45.0 billion"),
        ("$90,007 million", "$45,004 million"),
        ("US$4.81", "US$2.40"),
        ("$1.2bn", "$0.6bn"),
        ("18%", "18%"),
        ("Revenue of $10M and $3.0 Trillion.", "Revenue of $5M and $1.5 Trillion."),
        ("$1,000 fee", "$500 fee"),
        ("$5.", "$2."),
    ],
)
def test_scale_money_half(src, expected):
    assert t.scale_money(src, 0.5) == expected


def test_scale_money_grows_commas_only_when_original_had_them():
    assert t.scale_money("$900,000", 2) == "$1,800,000"
    assert t.scale_money("$9000", 2) == "$18000"
    with pytest.raises(ValueError):
        t.scale_money("$1", 0)


@pytest.mark.parametrize(
    "src, expected",
    [
        ("fiscal 2026", "fiscal 2019"),
        ("FY26", "FY19"),
        ("FY2026", "FY2019"),
        ("in 2026, sales", "in 2019, sales"),
        ("$2,026", "$2,026"),
        ("12026", "12026"),
        ("2026.5", "2026.5"),
        ("FY03", "FY96"),
        ("year 2026.", "year 2019."),
        ("1899 and 2100", "1899 and 2100"),
        ("FY267", "FY267"),
    ],
)
def test_shift_years(src, expected):
    assert t.shift_years(src, -7) == expected
