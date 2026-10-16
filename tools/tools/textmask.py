"""Masking helpers for hiding identifying details in documents."""

import re
from collections.abc import Iterable, Mapping

_NOT_AFTER_WORD = r"(?<![A-Za-z0-9])"
_NOT_BEFORE_WORD = r"(?![A-Za-z0-9])"


def _whole_word_pattern(keys: list[str], case_sensitive: bool) -> re.Pattern[str]:
    """Return a pattern matching any key as a whole word, one capture group per key."""
    alternatives = "|".join(f"({re.escape(k)})" for k in keys)
    if not case_sensitive:
        # Scope IGNORECASE to the keys so the ASCII word-boundary classes stay ASCII-only.
        alternatives = f"(?i:{alternatives})"
    else:
        alternatives = f"(?:{alternatives})"
    return re.compile(_NOT_AFTER_WORD + alternatives + _NOT_BEFORE_WORD)


def redact(text: str, mapping: Mapping[str, str], *, case_sensitive: bool = False) -> str:
    """Return text with every whole-word occurrence of each key replaced by its value; never None."""
    keys = sorted((k for k in mapping if k), key=len, reverse=True)
    if not keys:
        return text
    pattern = _whole_word_pattern(keys, case_sensitive)
    return pattern.sub(lambda m: mapping[keys[m.lastindex - 1]], text)


def find_terms(text: str, terms: Iterable[str], *, case_sensitive: bool = False) -> list[str]:
    """Return the terms occurring in text as whole words, in the given order without duplicates; never None."""
    found: list[str] = []
    for term in terms:
        if not term or term in found:
            continue
        if _whole_word_pattern([term], case_sensitive).search(text):
            found.append(term)
    return found


# Prefix, then a number: comma-grouped digits or plain digits, with an optional decimal part.
# Any scale word or suffix that follows is left untouched, so it need not be matched.
_MONEY = re.compile(
    r"(?P<prefix>US\$|\$)"
    r"(?P<int>[0-9]{1,3}(?:,[0-9]{3})+(?![0-9])|[0-9]+)"
    r"(?:\.(?P<frac>[0-9]+))?"
)


def _check_factor(factor: int | float) -> None:
    """Raise ValueError unless factor is positive; returns None."""
    if factor is None or factor <= 0:
        raise ValueError(f"factor must be positive, got {factor!r}")


def _scaled(int_part: str, frac: str | None, factor: int | float) -> str:
    """Return the unsigned number int_part[.frac] times factor, keeping its decimals and comma style."""
    decimals = len(frac) if frac is not None else 0
    number = int_part.replace(",", "") + ("." + frac if frac is not None else "")
    value = float(number) * factor
    spec = f",.{decimals}f" if "," in int_part else f".{decimals}f"
    return format(value, spec)


def scale_money(text: str, factor: int | float) -> str:
    """Return text with every money amount multiplied by factor, keeping its format; never None."""
    _check_factor(factor)

    def replace(m: re.Match[str]) -> str:
        return m.group("prefix") + _scaled(m.group("int"), m.group("frac"), factor)

    return _MONEY.sub(replace, text)


# What a table cell must be, once the whitespace around it is stripped, to be scaled:
# an optional "-" and an unsigned number, or an unsigned number in parentheses.
_UNSIGNED = r"(?P<int>[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.(?P<frac>[0-9]+))?"
_CELL_SIGNED = re.compile(r"(?P<minus>-?)" + _UNSIGNED)
_CELL_PARENS = re.compile(r"\(" + _UNSIGNED + r"\)")
# A bare year in a cell is a column heading, not an amount.
_CELL_YEAR = re.compile(r"(?:19|20)[0-9]{2}")


def _scale_cell(cell: str, factor: int | float) -> str:
    """Return the cell with its number scaled; unchanged if it is a year or not just a number."""
    body = cell.strip()
    if _CELL_YEAR.fullmatch(body):
        return cell
    m = _CELL_SIGNED.fullmatch(body)
    if m is not None:
        number = m.group("minus") + _scaled(m.group("int"), m.group("frac"), factor)
    else:
        m = _CELL_PARENS.fullmatch(body)
        if m is None:
            return cell
        number = "(" + _scaled(m.group("int"), m.group("frac"), factor) + ")"
    start = len(cell) - len(cell.lstrip())
    return cell[:start] + number + cell[start + len(body):]


def _scale_row(row: str, factor: int | float) -> str:
    """Return the table row with each number cell scaled, except one followed by a "%" cell."""
    cells = row.split("|")
    scaled = []
    for i, cell in enumerate(cells):
        before_percent = i + 1 < len(cells) and cells[i + 1].strip().startswith("%")
        scaled.append(cell if before_percent else _scale_cell(cell, factor))
    return "|".join(scaled)


def scale_table_numbers(text: str, factor: int | float) -> str:
    """Return text with each number filling a table cell multiplied by factor; never None."""
    _check_factor(factor)
    lines = text.split("\n")
    return "\n".join(_scale_row(line, factor) if " | " in line else line for line in lines)


# A four-digit year from 1900 to 2099 not followed by a digit, nor by "," or "." and a
# digit. What may precede it is set by each pattern's own lookbehind.
_YEAR = r"(?:19|20)[0-9]{2}(?![0-9])(?![,.][0-9])"

_YEARS = re.compile(r"FY(?P<fy>[0-9]{2})(?![0-9])" + r"|(?<![0-9,.$])(?P<year>" + _YEAR + r")")

# The same years, except one directly after an ASCII letter (as in "FY2019").
_YEARS_NOT_AFTER_LETTER = re.compile(r"(?<![0-9,.$A-Za-z])" + _YEAR)


def shift_years(text: str, offset: int) -> str:
    """Return text with four-digit years and two-digit FYdd fiscal years shifted by offset; never None."""

    def replace(m: re.Match[str]) -> str:
        if m.group("fy") is not None:
            return "FY" + f"{(2000 + int(m.group('fy')) + offset) % 100:02d}"
        return str(int(m.group("year")) + offset)

    return _YEARS.sub(replace, text)


def replace_years_before(text: str, cutoff: int, replacement: str) -> str:
    """Return text with each year below cutoff, unless directly after a letter, replaced; never None."""

    def replace(m: re.Match[str]) -> str:
        return replacement if int(m.group()) < cutoff else m.group()

    return _YEARS_NOT_AFTER_LETTER.sub(replace, text)
