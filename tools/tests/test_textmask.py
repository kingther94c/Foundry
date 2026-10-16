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


@pytest.mark.parametrize(
    "src, expected",
    [
        ("Revenue | 1,234 | 1,000", "Revenue | 617 | 500"),
        ("Net loss | $ | (80.2) | | -4.6", "Net loss | $ | (40.1) | | -2.3"),
        ("Margin | 23.6 | % | | 23.9 | %", "Margin | 23.6 | % | | 23.9 | %"),
        ("| 2026 | | 2025 |", "| 2026 | | 2025 |"),
        ("Sales of 1,234 units", "Sales of 1,234 units"),
        ("", ""),
        # The first cell counts, and the whitespace around a number stays as it was.
        ("1,000 | x", "500 | x"),
        ("x |  1,000\t|y", "x |  500\t|y"),
        ("a | b|1,000|c", "a | b|500|c"),
        # Decimal places are kept; a ratio is scaled like any other number.
        ("Book value | 3.00 | 0.10", "Book value | 1.50 | 0.05"),
        ("Current ratio | 1.8", "Current ratio | 0.9"),
        # Cells that are not just a number.
        ("x | $1.02 | 5.90% | Q2 | 1,234 units | ", "x | $1.02 | 5.90% | Q2 | 1,234 units | "),
        (
            "x | 1,2345 | 12,34 | 1. | .5 | +5 | (-5) | -(5) | 1 000 | --5",
            "x | 1,2345 | 12,34 | 1. | .5 | +5 | (-5) | -(5) | 1 000 | --5",
        ),
        # A "%" cell protects only the cell just before it.
        ("x | 12 | | %", "x | 6 | | %"),
        ("x | 12 |  % of sales", "x | 12 |  % of sales"),
        # Only lines containing " | " are table rows.
        ("a|1,000", "a|1,000"),
        ("Revenue 1,000\nRevenue | 1,000\n", "Revenue 1,000\nRevenue | 500\n"),
        ("Revenue | 1,000\r\nCost | 400", "Revenue | 500\r\nCost | 200"),
    ],
)
def test_scale_table_numbers_half(src, expected):
    assert t.scale_table_numbers(src, 0.5) == expected


def test_scale_table_numbers_years_and_commas():
    # Only a bare 1900-2099 is a year; with a sign, comma, decimals or parentheses it is an amount.
    src = "x | 1899 | 2100 | 2026.0 | (2026) | -2026 | 2,026 | 2026"
    expected = "x | 3798 | 4200 | 4052.0 | (4052) | -4052 | 4,052 | 2026"
    assert t.scale_table_numbers(src, 2) == expected
    assert t.scale_table_numbers("x | 900,000 | 9000", 2) == "x | 1,800,000 | 18000"


def test_scale_table_numbers_rejects_non_positive_factor():
    for factor in (0, -1):
        with pytest.raises(ValueError):
            t.scale_table_numbers("no table here", factor)


@pytest.mark.parametrize(
    "src, expected",
    [
        ("founded in 1902", "founded in an earlier year"),
        ("Reform Act of 1995.", "Reform Act of an earlier year."),
        ("in 2025 and 2022", "in 2025 and an earlier year"),
        ("$1,995", "$1,995"),
        ("2019.5", "2019.5"),
        ("FY2019", "FY2019"),
        ("FY19", "FY19"),
        # Only years strictly before the cutoff are replaced.
        ("2023 vs 2022", "2023 vs an earlier year"),
        # The same edges as shift_years.
        ("1899, 2100, 12019, 2019,5 and 1,2019", "1899, 2100, 12019, 2019,5 and 1,2019"),
        ("In 2019, then (2020).", "In an earlier year, then (an earlier year)."),
        ("fiscal 2019 and FY 2019", "fiscal an earlier year and FY an earlier year"),
    ],
)
def test_replace_years_before(src, expected):
    assert t.replace_years_before(src, 2023, "an earlier year") == expected


def test_replace_years_before_inserts_replacement_literally():
    assert t.replace_years_before("in 1999 and 2001", 2000, r"\g<0> \1") == r"in \g<0> \1 and 2001"
    # Replacement text is not scanned again.
    assert t.replace_years_before("in 1999", 2000, "1990") == "in 1990"
