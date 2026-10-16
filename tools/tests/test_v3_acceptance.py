"""Acceptance tests for tools v3 (amounts in tables, years long past), written from SPEC.md
independently of the code (held back from the agent that built it)."""

import pytest

from tools.textmask import replace_years_before, scale_money, scale_table_numbers


@pytest.mark.parametrize("before, after", [
    ("Revenue | 1,234 | 1,000", "Revenue | 617 | 500"),
    ("Net loss | $ | (80.2) | | -4.6", "Net loss | $ | (40.1) | | -2.3"),
    ("Margin | 23.6 | % | | 23.9 | %", "Margin | 23.6 | % | | 23.9 | %"),
    ("| 2026 | | 2025 |", "| 2026 | | 2025 |"),
    ("Sales of 1,234 units", "Sales of 1,234 units"),
])
def test_spec_examples(before, after):
    assert scale_table_numbers(before, 0.5) == after


def test_real_release_rows():
    rows = ("Book value per share | $ | 27.13 | | | $ | 27.05 |\n"
            "Total real estate | 396,542 | | | 6.9 | | | (15,360) |\n"
            "| | $ | 131,234 | | | $ | 151,458 |\n"
            "Return on average assets | 1.49 | % | | 1.44 | % |\n"
            "Commercial real estate | 3,503,981 | 5.90% | 3,579,903 | 5.80%")
    assert scale_table_numbers(rows, 2.0) == (
        "Book value per share | $ | 54.26 | | | $ | 54.10 |\n"
        "Total real estate | 793,084 | | | 13.8 | | | (30,720) |\n"
        "| | $ | 262,468 | | | $ | 302,916 |\n"
        "Return on average assets | 1.49 | % | | 1.44 | % |\n"
        "Commercial real estate | 7,007,962 | 5.90% | 7,159,806 | 5.80%")


def test_first_cell_and_spacing_are_kept():
    assert scale_table_numbers("12 |   3,000   |x", 3) == "36 |   9,000   |x"
    assert scale_table_numbers("  7 | a", 2) == "  14 | a"


def test_only_lines_with_the_cell_separator_are_rows():
    text = "Net income was 1,000 in the quarter\n5|6\nA | 10\n"
    assert scale_table_numbers(text, 2) == "Net income was 1,000 in the quarter\n5|6\nA | 20\n"


def test_line_breaks_and_carriage_returns():
    assert scale_table_numbers("A | 10\r\nB | 20\r\n", 2) == "A | 20\r\nB | 40\r\n"
    assert scale_table_numbers("", 2) == ""


def test_percent_sign_must_open_the_next_cell():
    assert scale_table_numbers("x | 5 | %", 2) == "x | 5 | %"
    assert scale_table_numbers("x | 5 |%", 2) == "x | 5 |%"
    assert scale_table_numbers("x | 5 | | %", 2) == "x | 10 | | %"
    assert scale_table_numbers("x | 5 | pct %", 2) == "x | 10 | pct %"


def test_what_is_not_a_number():
    for cell in ["$1.02", "5.90%", "Q2", "\u2014", "1,234 units", "1,23", "12,3456", "1.", ".5", "+5",
                 "(-5)", "- 5", "1 000", "(5", "5)"]:
        row = f"a | {cell} | b"
        assert scale_table_numbers(row, 2) == row, cell


def test_years_and_near_years():
    assert scale_table_numbers("a | 1999 | 2099 | 1900", 2) == "a | 1999 | 2099 | 1900"
    assert scale_table_numbers("a | 1899 | 2100", 2) == "a | 3798 | 4200"
    assert scale_table_numbers("a | 2,025 | 2025.0 | (2025) | -2025", 2) == "a | 4,050 | 4050.0 | (4050) | -4050"


def test_formatting_follows_scale_money():
    for number in ["1,234.567", "0.05", "999", "1,000,000", "12.5"]:
        expected = scale_money("$" + number, 0.37)[1:]
        assert scale_table_numbers(f"r | {number}", 0.37) == f"r | {expected}"
    assert scale_table_numbers("r | 999", 2) == "r | 1998"      # no comma when the original had none


@pytest.mark.parametrize("factor", [0, -1, None])
def test_bad_factor(factor):
    with pytest.raises(ValueError):
        scale_table_numbers("a | 1", factor)


@pytest.mark.parametrize("before, after", [
    ("founded in 1902", "founded in an earlier year"),
    ("Reform Act of 1995.", "Reform Act of an earlier year."),
    ("in 2025 and 2022", "in 2025 and an earlier year"),
    ("$1,995", "$1,995"),
    ("2019.5", "2019.5"),
    ("FY2019", "FY2019"),
    ("FY19", "FY19"),
])
def test_replace_years_before_examples(before, after):
    assert replace_years_before(before, 2023, "an earlier year") == after


def test_replace_years_before_boundaries():
    r = lambda t: replace_years_before(t, 2023, "X")
    assert r("2023 2022 1900 1899 2100") == "2023 X X 1899 2100"
    assert r("12022 20221 2022,5 2022.5 .2022 ,2022") == "12022 20221 2022,5 2022.5 .2022 ,2022"
    assert r("2022, then 2021. (2020) -2019 x2018 _2017 \u00e92016") == "X, then X. (X) -X x2018 _X \u00e9X"
    assert r("") == ""
    assert replace_years_before("1999 and 2000", 2000, "") == " and 2000"
