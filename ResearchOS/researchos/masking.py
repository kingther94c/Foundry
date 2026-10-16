"""Hide the company: deterministic masking, then masker/guesser rounds."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tools.textmask import (find_terms, redact, replace_years_before, scale_money,
                            scale_numbers, shift_years)

from researchos.cases import Case, Document, document_from_dict, identity_terms, render_documents
from researchos.jsonio import read_json, write_json
from researchos.llm import LLMError

COMPANY_REPLACEMENT = "the Company"
TICKER_REPLACEMENT = "TICKER"
DEFAULT_REPLACEMENT = "[REDACTED]"
# What a year long past (before the MaskConfig.history_years cutoff) becomes.
EARLIER_YEAR = "an earlier year"
MASKED_FILE = "masked.json"
MASK_KEY_FILE = "mask_key.json"


@dataclass
class MaskConfig:
    max_rounds: int = 4
    factor_range: tuple[float, float] = (0.3, 3.0)
    year_offset_range: tuple[int, int] = (-9, -3)
    guess_top_k: int = 3
    # Stop masking at once (status "identifiable") when the identifying guess's
    # confidence is at least this. None never gives up early.
    give_up_confidence: float | None = None
    # Rewrite each pre-masked document as a generic brief (the abstractor agent) and
    # run the masker and guesser rounds on the briefs.
    abstract: bool = False
    # When set, every year before (year of case.event_date) - history_years becomes
    # EARLIER_YEAR: a founding year or a statute's year would otherwise give away the shift.
    # None changes nothing about years.
    history_years: int | None = None


@dataclass
class MaskedCase:
    case_id: str
    documents: list[Document]  # masked; same kinds and order as the case's
    status: str  # "masked" or "identifiable"
    rounds: int  # guesser rounds run
    mapping: dict[str, str]  # every redaction applied (term -> replacement)
    factor: float
    year_offset: int
    final_guesses: list[dict] = field(default_factory=list)
    abstracted: bool = False  # True when the documents are abstractor briefs


MASKER_SYSTEM = """\
You are the masker in a research pipeline that trains an analyst to judge \
earnings reports without knowing which company issued them. You receive \
earnings documents (results release, call transcript, market expectations) \
in which the company name and ticker are already replaced, money amounts are \
multiplied by a secret factor and years are shifted.

Propose further redactions: anything that identifies the company or would let \
someone look up how the stock reacted. Redact:
- product, service, brand and segment names that are specific to the company;
- people (executives, analysts on the call, board members);
- places (headquarters, plants, stores, distinctive regions);
- competitors named as such, partners and major customers;
- URLs, e-mail addresses, phone numbers and exchange listings;
- distinctive figures that are public facts (store counts, subscriber or \
unit counts, employee numbers, record figures the company is known for);
- any remark about the share price, the stock's reaction, or how the stock traded.

Each redaction has the exact term as it appears in the text (it is replaced \
as a whole word, case-insensitively), a neutral generic replacement that keeps \
the economic meaning (e.g. "the flagship product", "the CEO", "a competitor"), \
and a short reason. Do not redact generic financial vocabulary, growth rates, \
margins or ratios: the analysis needs them. If clues from a guesser are given, \
make sure the terms behind those clues are removed. Return an empty list if \
nothing identifying remains."""

GUESSER_SYSTEM = """\
You are the guesser in a masking audit. You receive earnings documents with \
the company's identity hidden (names replaced, money amounts scaled, years \
shifted). Try to identify the company: give your best guesses, most likely \
first, each with the company name, its stock ticker (empty string if unknown) \
and a confidence between 0 and 1. Also list the clues in the text that point \
to the company (specific products, people, places, figures, phrasing), so they \
can be redacted."""

ABSTRACT_SYSTEM = """\
You are the abstractor in a research pipeline that trains an analyst to judge \
earnings reports without knowing which company issued them. You receive one \
document of a company's quarterly report (the results release, the call \
transcript or the market expectations). The company name and ticker are already \
replaced, money amounts are multiplied by a secret factor and years are shifted, \
but the document still has the company's shape: its segment structure, product \
lines and distinctive figures can name it.

Rewrite the document as a brief for an analyst who must judge how the stock will \
react to this report without knowing the company. Keep what that analyst needs:
- the results against the prior year and against what was expected;
- the direction and size of guidance changes;
- margins and growth rates;
- cash flow and capital returns;
- management's tone and the risks raised.
State them as rates, ratios and changes (percent, percentage points, multiples), \
not as amounts. Describe the business and each segment generically ("segment A", \
"the largest segment"). Leave out every name of the company, its products, \
brands, segments, people, places, customers and competitors, and any detail \
found at only one company. Do not add facts the document does not contain."""

_STR = {"type": "string"}

MASKER_SCHEMA = {
    "type": "object",
    "properties": {
        "redactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"term": _STR, "replacement": _STR, "reason": _STR},
                "required": ["term", "replacement", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["redactions"],
    "additionalProperties": False,
}

GUESSER_SCHEMA = {
    "type": "object",
    "properties": {
        "guesses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": _STR,
                    "ticker": _STR,
                    "confidence": {"type": "number"},
                },
                "required": ["company", "ticker", "confidence"],
                "additionalProperties": False,
            },
        },
        "clues": {"type": "array", "items": _STR},
    },
    "required": ["guesses", "clues"],
    "additionalProperties": False,
}

ABSTRACT_SCHEMA = {
    "type": "object",
    "properties": {"brief": _STR},
    "required": ["brief"],
    "additionalProperties": False,
}

_DROP_WORDS = frozenset(
    "the inc incorporated corp corporation co company ltd limited plc holdings group".split()
)


def _history_cutoff(case: Case, config: MaskConfig) -> int | None:
    """The first year kept as a year under `config.history_years`; None when it is unset.

    The cutoff is the year of `case.event_date` minus `history_years`; every year before it
    becomes EARLIER_YEAR. Raises ValueError for a negative (or non-integer) `history_years`
    and, when it is set, for an event date that does not start with a four-digit year.
    """
    years = config.history_years
    if years is None:
        return None
    if isinstance(years, bool) or not isinstance(years, int):
        raise ValueError(f"MaskConfig.history_years must be an integer >= 0 or None, "
                         f"got {years!r}")
    if years < 0:
        raise ValueError(f"MaskConfig.history_years must not be negative, got {years}")
    event_date = case.event_date
    head = event_date[:4] if isinstance(event_date, str) else ""
    if len(head) != 4 or not all(c in "0123456789" for c in head):
        raise ValueError(f"case {case.case_id!r}: event_date {event_date!r} does not start "
                         f"with a four-digit year, which MaskConfig.history_years needs")
    return int(head) - years


def _mask_text(text: str, mapping: dict[str, str], factor: float, year_offset: int,
               cutoff: int | None = None) -> str:
    """Names, then years long past, then money, then the year shift, then every other amount.

    Names go first, so a name that contains a year is still caught. Years before `cutoff`
    (when it is not None) are dropped before the shift, which applies to the years that
    remain. The other amounts are scaled last with `scale_numbers` (v17), whatever the
    layout: table cells, a negative split as "(22" and ")" across two cells, lines that are
    just a number, comma-grouped numbers in prose. Being last, a number that scaling brings
    into the range of years is not taken for a year and shifted, while a year heading a
    table column is shifted and then left alone as a year; money, already scaled by
    `scale_money`, sits right after "$" and is left alone.
    """
    text = redact(text, mapping)
    if cutoff is not None:
        text = replace_years_before(text, cutoff, EARLIER_YEAR)
    text = shift_years(scale_money(text, factor), year_offset)
    return scale_numbers(text, factor)


def _mask_documents(documents: list[Document], mapping: dict[str, str], factor: float,
                    year_offset: int, cutoff: int | None = None) -> list[Document]:
    return [
        Document(
            kind=doc.kind,
            title=_mask_text(doc.title, mapping, factor, year_offset, cutoff),
            text=_mask_text(doc.text, mapping, factor, year_offset, cutoff),
            source="",
        )
        for doc in documents
    ]


def _round_text(text: str, base: dict[str, str], mapping: dict[str, str], factor: float,
                year_offset: int, cutoff: int | None = None) -> str:
    """A redaction-mode round's text: pre_mask's masking with `base`, then `mapping`.

    `base` (company, aliases, ticker) is redacted before anything else, so a name that holds
    a year or an amount is still caught. The masker writes its terms as it saw them --
    years long past dropped, money and every other amount scaled, years shifted, earlier
    replacements in place -- so the full mapping is matched against that masked text only.
    """
    return redact(_mask_text(text, base, factor, year_offset, cutoff), mapping)


def _round_documents(documents: list[Document], base: dict[str, str], mapping: dict[str, str],
                     factor: float, year_offset: int,
                     cutoff: int | None = None) -> list[Document]:
    return [
        Document(
            kind=doc.kind,
            title=_round_text(doc.title, base, mapping, factor, year_offset, cutoff),
            text=_round_text(doc.text, base, mapping, factor, year_offset, cutoff),
            source="",
        )
        for doc in documents
    ]


def guard_documents(documents: list[Document], terms: list[str],
                    mapping: dict[str, str] | None = None) -> list[Document]:
    """Redact any identity term still present to "the Company" (recording it in mapping)."""
    found: list[str] = []
    for doc in documents:
        for term in find_terms(doc.title + "\n" + doc.text, terms):
            if term not in found:
                found.append(term)
    if not found:
        return list(documents)
    guard_map = {term: COMPANY_REPLACEMENT for term in found}
    if mapping is not None:
        for term in found:
            mapping.setdefault(term, COMPANY_REPLACEMENT)
    return [
        Document(kind=doc.kind, title=redact(doc.title, guard_map),
                 text=redact(doc.text, guard_map), source=doc.source)
        for doc in documents
    ]


def pre_mask(case: Case, rng: random.Random,
             config: MaskConfig) -> tuple[list[Document], dict[str, str], float, int]:
    """Deterministic masking: names, years long past, money and every other amount scaled by
    a secret factor, years shifted.

    Raises ValueError for a negative `config.history_years`, or when it is set and the
    case's event date does not start with a four-digit year.
    """
    cutoff = _history_cutoff(case, config)  # checked before anything is drawn
    factor = rng.uniform(*config.factor_range)
    year_offset = rng.randint(*config.year_offset_range)
    mapping: dict[str, str] = {}
    for name in [case.company, *case.aliases]:
        if isinstance(name, str) and name.strip():
            mapping[name.strip()] = COMPANY_REPLACEMENT
    if isinstance(case.ticker, str) and case.ticker.strip():
        mapping[case.ticker.strip()] = TICKER_REPLACEMENT
    documents = _mask_documents(case.documents, mapping, factor, year_offset, cutoff)
    return documents, mapping, factor, year_offset


def normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    cleaned = "".join(c if (c.isalnum() or c == " ") else " " for c in name.lower())
    return " ".join(w for w in cleaned.split() if w not in _DROP_WORDS).strip()


def identifying_guess(guesses: list[dict], case: Case, top_k: int) -> dict | None:
    """The first of the first top_k guesses that names the company (or its ticker), else None."""
    names = {normalize_name(n) for n in [case.company, *case.aliases]}
    names.discard("")
    ticker = case.ticker.strip().lower() if isinstance(case.ticker, str) else ""
    for guess in list(guesses or [])[: max(top_k, 0)]:
        if not isinstance(guess, dict):
            continue
        company = normalize_name(guess.get("company", ""))
        if company and company in names:
            return guess
        guessed_ticker = guess.get("ticker", "")
        if (ticker and isinstance(guessed_ticker, str)
                and guessed_ticker.strip().lower() == ticker):
            return guess
    return None


def is_identified(guesses: list[dict], case: Case, top_k: int) -> bool:
    return identifying_guess(guesses, case, top_k) is not None


def _gives_up(guess: dict | None, threshold: float | None) -> bool:
    """True if masking should stop: the identifying guess is at least `threshold` confident.

    A confidence that is missing or not a number (a string, a bool, NaN, ...) never gives up.
    """
    if guess is None or threshold is None:
        return False
    confidence = guess.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return False
    return confidence >= threshold


def _masker_prompt(documents: list[Document], clues: list[str]) -> str:
    parts = ["Masked documents:\n", render_documents(documents)]
    if clues:
        parts.append(
            "\nA guesser still identified the company from this text. Its clues:\n"
            + "\n".join(f"- {c}" for c in clues)
        )
    parts.append("\nPropose the redactions still needed.")
    return "\n".join(parts)


def _guesser_prompt(documents: list[Document]) -> str:
    return ("Masked documents:\n\n" + render_documents(documents)
            + "\nWhich company is this? Give your guesses and the clues.")


def _rewrite_replacements(mapping: dict[str, str], term: str, replacement: str) -> None:
    """Redact `term` inside every replacement already in mapping (whole word, any case)."""
    single = {term: replacement}
    for key, value in list(mapping.items()):
        if find_terms(value, [term]):
            mapping[key] = redact(value, single)


def _add_redactions(mapping: dict[str, str], output: dict) -> None:
    """Add the masker's redactions to mapping, in order; a term already there is skipped.

    Adding a term first rewrites every replacement already in the mapping that contains it,
    so text an earlier redaction put in is redacted too and one redact pass with the mapping
    still gives the final text. The new term's own replacement is kept as given.
    """
    existing = {k.casefold() for k in mapping}
    redactions = output.get("redactions") if isinstance(output, dict) else None
    for item in redactions or []:
        if not isinstance(item, dict):
            continue
        term = item.get("term")
        if not isinstance(term, str) or not term.strip():
            continue
        term = term.strip()
        if term.casefold() in existing:
            # An existing term keeps its place (its replacement may have been rewritten).
            continue
        replacement = item.get("replacement")
        if not isinstance(replacement, str):
            replacement = DEFAULT_REPLACEMENT
        _rewrite_replacements(mapping, term, replacement)
        mapping[term] = replacement
        existing.add(term.casefold())


def _abstractor_prompt(document: Document) -> str:
    return (f"Document kind: {document.kind}\n\n"
            f"Pre-masked text:\n\n{document.text}\n\n"
            f"Rewrite this {document.kind} document as a brief.")


def _abstract_documents(documents: list[Document], abstractor) -> list[Document]:
    """One abstractor call per document, in order; each becomes Document(kind, "<kind> brief")."""
    briefs: list[Document] = []
    for doc in documents:
        output = abstractor.json(ABSTRACT_SYSTEM, _abstractor_prompt(doc), ABSTRACT_SCHEMA)
        brief = output.get("brief") if isinstance(output, dict) else None
        if not isinstance(brief, str) or not brief.strip():
            raise LLMError(f"abstractor returned no brief for the {doc.kind} document")
        briefs.append(Document(kind=doc.kind, title=f"{doc.kind} brief", text=brief, source=""))
    return briefs


def _redact_documents(documents: list[Document], mapping: dict[str, str]) -> list[Document]:
    return [
        Document(kind=doc.kind, title=redact(doc.title, mapping),
                 text=redact(doc.text, mapping), source=doc.source)
        for doc in documents
    ]


def mask_case(case: Case, masker, guesser, rng: random.Random,
              config: MaskConfig = MaskConfig(), *, abstractor=None) -> MaskedCase:
    if config.abstract and abstractor is None:
        raise ValueError("MaskConfig.abstract is set but no abstractor was given")
    terms = identity_terms(case)
    documents, mapping, factor, year_offset = pre_mask(case, rng, config)
    # pre_mask's own mapping, applied to the originals before scaling and shifting.
    base = dict(mapping)
    # pre_mask has already checked history_years and the event date.
    cutoff = _history_cutoff(case, config)
    documents = guard_documents(documents, terms, mapping)
    briefs: list[Document] | None = None
    if config.abstract:
        # Redaction keeps a document's shape (segments, product lines, ratios), which still
        # names a large company; the rounds run on generic briefs instead.
        briefs = guard_documents(_abstract_documents(documents, abstractor), terms, mapping)
        documents = briefs
    status = "identifiable"
    rounds = 0
    clues: list[str] = []
    final_guesses: list[dict] = []
    for round_no in range(1, config.max_rounds + 1):
        output = masker.json(MASKER_SYSTEM, _masker_prompt(documents, clues if round_no > 1 else []),
                             MASKER_SCHEMA)
        _add_redactions(mapping, output)
        if briefs is None:
            # Re-mask the originals; the masker's terms match the scaled, shifted text it saw.
            documents = _round_documents(case.documents, base, mapping, factor, year_offset,
                                         cutoff)
        else:
            # The briefs were written from text already scaled and shifted: redact only.
            documents = _redact_documents(briefs, mapping)
        documents = guard_documents(documents, terms, mapping)
        result = guesser.json(GUESSER_SYSTEM, _guesser_prompt(documents), GUESSER_SCHEMA)
        rounds += 1
        result = result if isinstance(result, dict) else {}
        final_guesses = [g for g in result.get("guesses") or [] if isinstance(g, dict)]
        hit = identifying_guess(final_guesses, case, config.guess_top_k)
        if hit is None:
            status = "masked"
            break
        if _gives_up(hit, config.give_up_confidence):
            # Named this confidently after redaction: it recognizes the business itself,
            # so further rounds only cost tokens. Status stays "identifiable".
            break
        clues = [c for c in result.get("clues") or [] if isinstance(c, str)]
    documents = guard_documents(documents, terms, mapping)
    return MaskedCase(
        case_id=case.case_id,
        documents=documents,
        status=status,
        rounds=rounds,
        mapping=mapping,
        factor=factor,
        year_offset=year_offset,
        final_guesses=final_guesses,
        abstracted=briefs is not None,
    )


def save_masked(root, masked: MaskedCase) -> None:
    case_dir = Path(root) / masked.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    public = {
        "documents": [asdict(d) for d in masked.documents],
        "status": masked.status,
        "rounds": masked.rounds,
        "final_guesses": masked.final_guesses,
        "abstracted": bool(masked.abstracted),
    }
    key = {"mapping": masked.mapping, "factor": masked.factor, "year_offset": masked.year_offset}
    write_json(case_dir / MASKED_FILE, public)
    write_json(case_dir / MASK_KEY_FILE, key)


def load_masked(root, case_id: str) -> MaskedCase:
    case_dir = Path(root) / case_id
    public = read_json(case_dir / MASKED_FILE)
    key = read_json(case_dir / MASK_KEY_FILE)
    return MaskedCase(
        case_id=case_id,
        documents=[document_from_dict(d) for d in public["documents"]],
        status=public["status"],
        rounds=public.get("rounds", 0),
        mapping=dict(key.get("mapping", {})),
        factor=key.get("factor", 1.0),
        year_offset=key.get("year_offset", 0),
        final_guesses=list(public.get("final_guesses", [])),
        abstracted=bool(public.get("abstracted", False)),
    )
