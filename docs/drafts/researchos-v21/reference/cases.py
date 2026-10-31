"""Cases, documents and outcomes, and their on-disk layout.

Layout under a root directory::

    <root>/<case_id>/case.json        the Case (never contains the outcome)
    <root>/<case_id>/outcome.json     the Outcome
    <root>/<case_id>/resolution.json  which macro targets have resolved (dates only)

Hand-written reference for v21; not part of the build.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tools.dates import horizon_end, parse_horizon, parse_iso_date
from tools.stats import is_number

from researchos.jsonio import read_json, write_json

CASE_FILE = "case.json"
OUTCOME_FILE = "outcome.json"
RESOLUTION_FILE = "resolution.json"
DOMAINS = ("earnings", "macro")
KINDS = ("bp", "return", "points")
_ID = re.compile(r"[A-Za-z0-9_]+")


@dataclass
class Document:
    kind: str  # "release" | "transcript" | "expectations"
    title: str
    text: str
    source: str = ""


@dataclass
class Target:
    id: str
    name: str
    kind: str
    horizons: list[str]
    as_of_date: str
    as_of_value: float
    trailing: dict[str, float | None] = field(default_factory=dict)


@dataclass
class TargetOutcome:
    target: str
    horizon: str
    kind: str
    start_date: str
    start: float
    end_date: str
    end: float
    change: float
    up: bool


@dataclass
class Outcome:
    abnormal_return_1d: float | None = None  # stock minus market, first reacting session
    stock_return_1d: float | None = None
    market_return_1d: float | None = None
    notes: str = ""
    sources: list[str] = field(default_factory=list)
    targets: dict[str, TargetOutcome] = field(default_factory=dict)


@dataclass
class Case:
    case_id: str
    company: str
    ticker: str
    aliases: list[str]  # other names that identify it (brands, CEO, ...)
    fiscal_period: str  # e.g. "Q4 FY2026"
    event_date: str  # ISO date of the first session that can react
    documents: list[Document]
    domain: str = "earnings"
    targets: list[Target] = field(default_factory=list)


def document_from_dict(data: dict) -> Document:
    return Document(
        kind=data["kind"],
        title=data.get("title", ""),
        text=data.get("text", ""),
        source=data.get("source", ""),
    )


def target_from_dict(data: dict) -> Target:
    return Target(
        id=data["id"],
        name=data.get("name", ""),
        kind=data["kind"],
        horizons=list(data["horizons"]),
        as_of_date=data["as_of_date"],
        as_of_value=data["as_of_value"],
        trailing=dict(data.get("trailing", {})),
    )


def target_outcome_from_dict(data: dict) -> TargetOutcome:
    return TargetOutcome(
        target=data["target"], horizon=data["horizon"], kind=data["kind"],
        start_date=data["start_date"], start=data["start"], end_date=data["end_date"],
        end=data["end"], change=data["change"], up=data["up"],
    )


def _valid_id(value) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _valid_horizon(value) -> bool:
    try:
        parse_horizon(value)
    except ValueError:
        return False
    return value == value.strip()


def target_key(target_id, horizon) -> str:
    if not _valid_id(target_id):
        raise ValueError(f"target id {target_id!r} must be ASCII letters, digits and underscores")
    if not _valid_horizon(horizon):
        raise ValueError(f"target {target_id}: {horizon!r} is not a horizon such as '1w' or '3m'")
    return f"{target_id}_{horizon}"


def check_targets(targets) -> None:
    if not targets:
        raise ValueError("a macro case needs at least one target")
    seen: set[str] = set()
    for i, target in enumerate(targets):
        if not isinstance(target, Target):
            raise ValueError(f"target {i} is not a Target: {target!r}")
        if not _valid_id(target.id):
            raise ValueError(f"target {i}: id {target.id!r} must be ASCII letters, digits "
                             f"and underscores")
        name = target.id
        if name in seen:
            raise ValueError(f"target {name} appears twice")
        seen.add(name)
        if target.kind not in KINDS:
            raise ValueError(f"target {name}: kind {target.kind!r} is not one of {KINDS}")
        horizons = target.horizons
        if not isinstance(horizons, list) or not horizons:
            raise ValueError(f"target {name}: horizons must be a non-empty list")
        for h in horizons:
            if not _valid_horizon(h):
                raise ValueError(f"target {name}: {h!r} is not a horizon such as '1w' or '3m'")
        if len(set(horizons)) != len(horizons):
            raise ValueError(f"target {name}: a horizon is repeated in {horizons}")
        if not isinstance(target.as_of_date, str) or parse_iso_date(target.as_of_date) is None:
            raise ValueError(f"target {name}: as_of_date {target.as_of_date!r} is not an ISO date")
        if not is_number(target.as_of_value):
            raise ValueError(f"target {name}: as_of_value {target.as_of_value!r} is not a number")


def target_keys(case: Case) -> list[str]:
    if not case.targets:
        return []
    check_targets(case.targets)
    return [target_key(t.id, h) for t in case.targets for h in t.horizons]


def _iso(value):
    return parse_iso_date(value) if isinstance(value, str) else None


def macro_case(case_id, as_of, targets, documents) -> Case:
    when = _iso(as_of)
    if when is None:
        raise ValueError(f"case {case_id}: as_of {as_of!r} is not an ISO date (YYYY-MM-DD)")
    targets = list(targets)
    check_targets(targets)
    for t in targets:
        if parse_iso_date(t.as_of_date) > when:
            raise ValueError(f"case {case_id}: target {t.id} starts from {t.as_of_date}, "
                             f"after the snapshot's date {when.isoformat()}")
    day = when.isoformat()
    return Case(case_id=case_id, company="", ticker="", aliases=[],
                fiscal_period=f"as of {day}", event_date=day, documents=list(documents),
                domain="macro", targets=targets)


def _case_dict(case: Case) -> dict:
    data = asdict(case)
    if case.domain == "earnings" and not case.targets:
        del data["domain"], data["targets"]
    return data


def _outcome_dict(outcome: Outcome) -> dict:
    data = asdict(outcome)
    if not outcome.targets:
        del data["targets"]
    return data


def save_case(root, case: Case, outcome: Outcome | None = None) -> None:
    """Write case.json, and outcome.json when an outcome is given."""
    case_dir = Path(root) / case.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    write_json(case_dir / CASE_FILE, _case_dict(case))
    if outcome is not None:
        write_json(case_dir / OUTCOME_FILE, _outcome_dict(outcome))


def save_outcome(root, case_id: str, outcome: Outcome) -> None:
    case_dir = Path(root) / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    write_json(case_dir / OUTCOME_FILE, _outcome_dict(outcome))


def load_case(root, case_id: str) -> Case:
    """Read case.json only; outcome.json is never opened here."""
    data = read_json(Path(root) / case_id / CASE_FILE)
    return Case(
        case_id=data["case_id"],
        company=data["company"],
        ticker=data["ticker"],
        aliases=list(data.get("aliases", [])),
        fiscal_period=data.get("fiscal_period", ""),
        event_date=data.get("event_date", ""),
        documents=[document_from_dict(d) for d in data.get("documents", [])],
        domain=data.get("domain", "earnings"),
        targets=[target_from_dict(t) for t in data.get("targets", [])],
    )


def load_outcome(root, case_id: str) -> Outcome:
    data = read_json(Path(root) / case_id / OUTCOME_FILE)
    if "targets" not in data:
        abnormal = data["abnormal_return_1d"]
        targets = {}
    else:
        abnormal = data.get("abnormal_return_1d")
        targets = {k: target_outcome_from_dict(v) for k, v in data["targets"].items()}
    return Outcome(
        abnormal_return_1d=abnormal,
        stock_return_1d=data.get("stock_return_1d"),
        market_return_1d=data.get("market_return_1d"),
        notes=data.get("notes", ""),
        sources=list(data.get("sources", [])),
        targets=targets,
    )


def resolution_manifest(case: Case, resolved=()) -> dict:
    if case.domain != "macro":
        raise ValueError(f"case {case.case_id}: only a macro case has a resolution manifest")
    when = _iso(case.event_date)
    if when is None:
        raise ValueError(f"case {case.case_id}: event_date {case.event_date!r} is not an ISO date")
    keys = target_keys(case)
    done = list(resolved)
    unknown = [k for k in done if k not in keys]
    if unknown:
        raise ValueError(f"case {case.case_id}: no target key {unknown[0]!r}")
    entries = {}
    for t in case.targets:
        for h in t.horizons:
            entries[target_key(t.id, h)] = {"horizon_end": horizon_end(when, h).isoformat(),
                                            "resolved": target_key(t.id, h) in done}
    return {"as_of": when.isoformat(), "targets": entries}


def save_resolution(root, case_id: str, manifest: dict) -> None:
    case_dir = Path(root) / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    write_json(case_dir / RESOLUTION_FILE, manifest)


def load_resolution(root, case_id: str) -> dict | None:
    path = Path(root) / case_id / RESOLUTION_FILE
    if not path.is_file():
        return None
    return read_json(path)


def unresolved_targets(case: Case, manifest) -> list[str]:
    keys = target_keys(case)
    entries = manifest.get("targets") if isinstance(manifest, dict) else None
    if not isinstance(entries, dict):
        return keys
    out = []
    for k in keys:
        entry = entries.get(k)
        if not (isinstance(entry, dict) and entry.get("resolved") is True):
            out.append(k)
    return out


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
