"""Acceptance tests for tools v4 (every amount, whatever the layout), written from SPEC.md
independently of the code (held back from the agent that built it)."""

import pytest

from tools.textmask import scale_money, scale_numbers, scale_table_numbers


@pytest.mark.parametrize("before, after", [
    ("over 1,300 tenants", "over 650 tenants"),
    ("a 1,000 percent rise", "a 1,000 percent rise"),
    ("$1,300", "$1,300"),
    ("500 tenants", "500 tenants"),
    ("Net | 1,000 | | 30 | %", "Net | 500 | | 30 | %"),
    ("Loss | (22 | )", "Loss | (11 | )"),
    ("Q2 | Sales 2,000 and 3", "Q2 | Sales 1,000 and 3"),
    ("(1,000)", "(500)"),
    ("  64", "  32"),
    ("2026", "2026"),
])
def test_spec_examples(before, after):
    assert scale_numbers(before, 0.5) == after


def test_one_cell_per_line_layout():
    text = "|\n535\n|\n|\n63,296\n|\n(1.5)\n-4\n2025\nTotal revenue\n"
    assert scale_numbers(text, 2) == "|\n1070\n|\n|\n126,592\n|\n(3.0)\n-8\n2025\nTotal revenue\n"


def test_split_negatives_in_rows():
    row = "Provision | | | (29,800 | ) | | | 10,850 | | | | (22 | )"
    assert scale_numbers(row, 0.5) == "Provision | | | (14,900 | ) | | | 5,425 | | | | (11 | )"
    # without the closing cell the "(22" cell is no number; "(1,234" is still prose with a comma number
    assert scale_numbers("x | (22 | y", 0.5) == "x | (22 | y"
    assert scale_numbers("x | (1,234 | y", 2) == "x | (2,468 | y"
    assert scale_numbers("x | (-22 | )", 2) == "x | (-22 | )"


def test_rows_match_scale_table_numbers_where_it_applies():
    rows = ("Book value per share | $ | 27.13 | | | $ | 27.05 |\n"
            "Total real estate | 396,542 | | | 6.9 | | | (15,360) |\n"
            "Return on average assets | 1.49 | % | | 1.44 | % |\n"
            "| 2026 | | 2025 |")
    assert scale_numbers(rows, 2.0) == scale_table_numbers(rows, 2.0)


def test_prose_cells_in_flattened_slides():
    line = "Bread | July 23, 2026 3 PPNR $461 $510 2Q25 total 1,234.5 and 2,000% and 3,000 bps"
    assert scale_numbers(line, 2) == ("Bread | July 23, 2026 3 PPNR $461 $510 2Q25 total 2,469.0 and 2,000% "
                                      "and 3,000 bps")


@pytest.mark.parametrize("before, after", [
    ("1,234", "2,468"),                       # a whole line is a number line
    ("about 1,234.", "about 2,468."),
    ("1,234,567 shares", "2,469,134 shares"),
    ("a1,234 b", "a1,234 b"),
    ("x 1,234a", "x 1,234a"),
    ("x 1,2345", "x 1,2345"),
    ("x 1,234,5", "x 1,234,5"),
    ("x 1,234.5.6", "x 1,234.5.6"),
    ("x 21,234", "x 42,468"),
    ("x .1,234", "x .1,234"),
    ("x ,1,234", "x ,1,234"),
    ("x US$1,234", "x US$1,234"),
    ("x $ 1,234", "x $ 2,468"),
    ("x (1,234)", "x (2,468)"),
    ("x 1,234 %", "x 1,234 %"),
    ("x 1,234  Percent", "x 1,234  Percent"),
    ("x 1,234 per cent", "x 1,234 per cent"),
    ("x 1,234BPS", "x 1,234BPS"),
    ("x 1,234 basis points", "x 1,234 basis points"),
    ("x 1,234 percentage points", "x 1,234 percentage points"),
    ("x 1,234 pct", "x 2,468 pct"),
    ("June 30,2026 and 30, 2026", "June 30,2026 and 30, 2026"),
    ("x 1,000.25 y", "x 2,000.50 y"),
])
def test_prose_rules(before, after):
    assert scale_numbers(before, 2) == after


def test_after_scale_money_nothing_is_scaled_twice():
    text = "Revenue was $1,200 million and 1,500 units\nNet | $ | 1,000 | | (2,000 | )\n3,000"
    once = scale_numbers(scale_money(text, 2), 2)
    assert once == "Revenue was $2,400 million and 3,000 units\nNet | $ | 2,000 | | (4,000 | )\n6,000"


def test_line_endings_and_empty():
    assert scale_numbers("1,000\r\nx 2,000\r\n", 2) == "2,000\r\nx 4,000\r\n"
    assert scale_numbers("", 2) == ""


@pytest.mark.parametrize("factor", [0, -1, None])
def test_bad_factor(factor):
    with pytest.raises(ValueError):
        scale_numbers("1,000", factor)
