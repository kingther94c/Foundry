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


def scale_money(text: str, factor: int | float) -> str:
    """Return text with every money amount multiplied by factor, keeping its format; never None."""
    if factor is None or factor <= 0:
        raise ValueError(f"factor must be positive, got {factor!r}")

    def replace(m: re.Match[str]) -> str:
        int_part = m.group("int")
        frac = m.group("frac")
        decimals = len(frac) if frac is not None else 0
        number = int_part.replace(",", "") + ("." + frac if frac is not None else "")
        value = float(number) * factor
        spec = f",.{decimals}f" if "," in int_part else f".{decimals}f"
        return m.group("prefix") + format(value, spec)

    return _MONEY.sub(replace, text)


_YEARS = re.compile(
    r"FY(?P<fy>[0-9]{2})(?![0-9])"
    r"|(?<![0-9,.$])(?P<year>(?:19|20)[0-9]{2})(?![0-9])(?![,.][0-9])"
)


def shift_years(text: str, offset: int) -> str:
    """Return text with four-digit years and two-digit FYdd fiscal years shifted by offset; never None."""

    def replace(m: re.Match[str]) -> str:
        if m.group("fy") is not None:
            return "FY" + f"{(2000 + int(m.group('fy')) + offset) % 100:02d}"
        return str(int(m.group("year")) + offset)

    return _YEARS.sub(replace, text)
