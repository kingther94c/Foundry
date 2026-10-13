"""Cases, documents and outcomes, and their on-disk layout.

Layout under a root directory::

    <root>/<case_id>/case.json      the Case (never contains the outcome)
    <root>/<case_id>/outcome.json   the Outcome
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CASE_FILE = "case.json"
OUTCOME_FILE = "outcome.json"


@dataclass
class Document:
    kind: str  # "release" | "transcript" | "expectations"
    title: str
    text: str
    source: str = ""


@dataclass
class Outcome:
    abnormal_return_1d: float  # stock minus market, first reacting session
    stock_return_1d: float | None = None
    market_return_1d: float | None = None
    notes: str = ""
    sources: list[str] = field(default_factory=list)


@dataclass
class Case:
    case_id: str
    company: str
    ticker: str
    aliases: list[str]  # other names that identify it (brands, CEO, ...)
    fiscal_period: str  # e.g. "Q4 FY2026"
    event_date: str  # ISO date of the first session that can react
    documents: list[Document]


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def document_from_dict(data: dict) -> Document:
    return Document(
        kind=data["kind"],
        title=data.get("title", ""),
        text=data.get("text", ""),
        source=data.get("source", ""),
    )


def save_case(root, case: Case, outcome: Outcome) -> None:
    """Write case.json and outcome.json under <root>/<case_id>/."""
    case_dir = Path(root) / case.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    _write_json(case_dir / CASE_FILE, asdict(case))
    _write_json(case_dir / OUTCOME_FILE, asdict(outcome))


def load_case(root, case_id: str) -> Case:
    """Read case.json only; outcome.json is never opened here."""
    data = _read_json(Path(root) / case_id / CASE_FILE)
    return Case(
        case_id=data["case_id"],
        company=data["company"],
        ticker=data["ticker"],
        aliases=list(data.get("aliases", [])),
        fiscal_period=data.get("fiscal_period", ""),
        event_date=data.get("event_date", ""),
        documents=[document_from_dict(d) for d in data.get("documents", [])],
    )


def load_outcome(root, case_id: str) -> Outcome:
    data = _read_json(Path(root) / case_id / OUTCOME_FILE)
    return Outcome(
        abnormal_return_1d=data["abnormal_return_1d"],
        stock_return_1d=data.get("stock_return_1d"),
        market_return_1d=data.get("market_return_1d"),
        notes=data.get("notes", ""),
        sources=list(data.get("sources", [])),
    )


def list_cases(root) -> list[str]:
    """The ids of the directories under root that contain a case.json, sorted."""
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / CASE_FILE).is_file())


def identity_terms(case: Case) -> list[str]:
    """company, ticker, then aliases; no empty strings, no case-insensitive duplicates."""
    terms: list[str] = []
    seen: set[str] = set()
    for term in [case.company, case.ticker, *case.aliases]:
        if not isinstance(term, str):
            continue
        term = term.strip()
        if not term or term.casefold() in seen:
            continue
        seen.add(term.casefold())
        terms.append(term)
    return terms


def render_documents(documents: list[Document]) -> str:
    """Render documents as plain text with kind and title as headings (no source)."""
    parts = []
    for doc in documents:
        parts.append(f"## [{doc.kind}] {doc.title}\n\n{doc.text.strip()}\n")
    return "\n".join(parts)
