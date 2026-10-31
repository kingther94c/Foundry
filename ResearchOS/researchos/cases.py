"""Cases, documents and outcomes, and their on-disk layout.

Layout under a root directory::

    <root>/<case_id>/case.json        the Case (never contains the outcome)
    <root>/<case_id>/outcome.json     the Outcome
    <root>/<case_id>/resolution.json  which targets of a macro case have resolved (v21)
    <root>/<case_id>/prediction.json  the sealed forecast, written before any outcome is read
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
# The sealed forecast (v28: moved here from researchos.loop, which imports it from here).
PREDICTION_FILE = "prediction.json"
DOMAINS = ("earnings", "macro")
# How a target's change is measured (tools.finance.level_change).
KINDS = ("bp", "return", "points")
# A target id: ASCII letters, digits and underscores only, matched as a whole.
_TARGET_ID = re.compile(r"[A-Za-z0-9_]+")
_HORIZON_RULE = "a count and d, w, m or y, such as '3m', with no whitespace around it"


@dataclass
class Document:
    kind: str  # "release" | "transcript" | "expectations"
    title: str
    text: str
    source: str = ""


@dataclass
class Target:
    """A market level a macro case forecasts over one or more horizons (v21)."""
    id: str  # e.g. "DGS10"
    name: str  # e.g. "10-year Treasury yield"
    kind: str  # one of KINDS
    horizons: list[str]  # e.g. ["1w", "1m", "3m"]
    as_of_date: str  # ISO date of the observation the forecasts start from
    as_of_value: float  # the level on that date
    # horizon -> the change over the same length of time up to as_of_date
    trailing: dict[str, float | None] = field(default_factory=dict)


@dataclass
class TargetOutcome:
    """How one target moved over one horizon (v21)."""
    target: str  # the Target's id
    horizon: str
    kind: str
    start_date: str
    start: float
    end_date: str
    end: float
    change: float  # tools.finance.level_change(start, end, kind)
    up: bool  # change > 0


@dataclass
class Outcome:
    # Stock minus market, first reacting session; None when a macro case has no such number.
    abnormal_return_1d: float | None = None
    stock_return_1d: float | None = None
    market_return_1d: float | None = None
    notes: str = ""
    sources: list[str] = field(default_factory=list)
    # target_key(target id, horizon) -> how that target moved over that horizon (v21)
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
    domain: str = "earnings"  # one of DOMAINS (v21)
    targets: list[Target] = field(default_factory=list)  # what a macro case forecasts (v21)


def document_from_dict(data: dict) -> Document:
    return Document(
        kind=data["kind"],
        title=data.get("title", ""),
        text=data.get("text", ""),
        source=data.get("source", ""),
    )


def target_from_dict(data: dict) -> Target:
    """A Target; id, kind, horizons, as_of_date and as_of_value are required (KeyError).

    name defaults to "" and trailing to {}; horizons becomes a new list and trailing a new
    dict (a value of another type is kept as it is, for check_targets to refuse).
    """
    target_id, kind, horizons = data["id"], data["kind"], data["horizons"]
    as_of_date, as_of_value = data["as_of_date"], data["as_of_value"]
    trailing = data.get("trailing", {})
    return Target(
        id=target_id,
        name=data.get("name", ""),
        kind=kind,
        horizons=list(horizons) if isinstance(horizons, (list, tuple)) else horizons,
        as_of_date=as_of_date,
        as_of_value=as_of_value,
        trailing=dict(trailing) if isinstance(trailing, dict) else trailing,
    )


def target_outcome_from_dict(data: dict) -> TargetOutcome:
    """A TargetOutcome; every field is required (KeyError when one is missing)."""
    return TargetOutcome(
        target=data["target"],
        horizon=data["horizon"],
        kind=data["kind"],
        start_date=data["start_date"],
        start=data["start"],
        end_date=data["end_date"],
        end=data["end"],
        change=data["change"],
        up=data["up"],
    )


def case_to_dict(case: Case) -> dict:
    """What case.json holds: asdict(case), less domain and targets for an earnings case
    without targets, so that such a file is exactly what it was before v21."""
    data = asdict(case)
    if case.domain == "earnings" and not case.targets:
        del data["domain"], data["targets"]
    return data


def outcome_to_dict(outcome: Outcome) -> dict:
    """What outcome.json holds: asdict(outcome), less targets when there are none."""
    data = asdict(outcome)
    if not outcome.targets:
        del data["targets"]
    return data


def save_case(root, case: Case, outcome: Outcome | None = None) -> None:
    """Write case.json, and outcome.json when an outcome is given, under <root>/<case_id>/.

    With no outcome only case.json is written; an existing outcome.json is left as it is.
    """
    case_dir = Path(root) / case.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    write_json(case_dir / CASE_FILE, case_to_dict(case))
    if outcome is not None:
        write_json(case_dir / OUTCOME_FILE, outcome_to_dict(outcome))


def save_outcome(root, case_id: str, outcome: Outcome) -> None:
    """Write outcome.json under <root>/<case_id>/ (creating the directory), as save_case does."""
    case_dir = Path(root) / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    write_json(case_dir / OUTCOME_FILE, outcome_to_dict(outcome))


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
    """Read outcome.json.

    A file without a "targets" key reads exactly as before v21 (KeyError when
    abnormal_return_1d is missing). With one, abnormal_return_1d defaults to None and every
    entry of targets becomes a TargetOutcome under its key.
    """
    data = read_json(Path(root) / case_id / OUTCOME_FILE)
    if not (isinstance(data, dict) and "targets" in data):
        return Outcome(
            abnormal_return_1d=data["abnormal_return_1d"],
            stock_return_1d=data.get("stock_return_1d"),
            market_return_1d=data.get("market_return_1d"),
            notes=data.get("notes", ""),
            sources=list(data.get("sources", [])),
        )
    return Outcome(
        abnormal_return_1d=data.get("abnormal_return_1d"),
        stock_return_1d=data.get("stock_return_1d"),
        market_return_1d=data.get("market_return_1d"),
        notes=data.get("notes", ""),
        sources=list(data.get("sources", [])),
        targets={key: target_outcome_from_dict(value) for key, value in data["targets"].items()},
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


# ---- macro cases (v21) ----------------------------------------------------------------

def _valid_target_id(target_id) -> bool:
    return isinstance(target_id, str) and _TARGET_ID.fullmatch(target_id) is not None


def _valid_horizon(horizon) -> bool:
    """True if parse_horizon accepts horizon and it has no whitespace around it."""
    if not isinstance(horizon, str) or horizon != horizon.strip():
        return False
    try:
        parse_horizon(horizon)
    except ValueError:
        return False
    return True


def target_key(target_id, horizon) -> str:
    """The name a target's forecast over a horizon goes by: f"{target_id}_{horizon}".

    Raises ValueError when target_id is not a str of ASCII letters, digits and underscores
    only, or when horizon is not a valid horizon (one tools.dates.parse_horizon accepts,
    with no whitespace around it).
    """
    if not _valid_target_id(target_id):
        raise ValueError(f"target id {target_id!r} is not ASCII letters, digits and "
                         f"underscores only")
    if not _valid_horizon(horizon):
        raise ValueError(f"target {target_id}: horizon {horizon!r} is not a valid horizon "
                         f"({_HORIZON_RULE})")
    return f"{target_id}_{horizon}"


def _target_list(targets) -> list:
    try:
        return list(targets)
    except TypeError:
        raise ValueError(f"targets must be a list of Target, got {targets!r}") from None


def check_targets(targets) -> None:
    """Raise ValueError unless targets is a non-empty list of well-formed Targets.

    The error names the target by its id, or by its position in the list when the id itself
    is the problem. name and trailing are not checked.
    """
    items = _target_list(targets)
    if not items:
        raise ValueError("targets is empty: a macro case forecasts at least one target")
    seen: set[str] = set()
    for position, target in enumerate(items):
        where = f"the target at position {position} (targets[{position}])"
        if not isinstance(target, Target):
            raise ValueError(f"{where} is not a Target: {target!r}")
        if not _valid_target_id(target.id):
            raise ValueError(f"{where}: id {target.id!r} is not ASCII letters, digits and "
                             f"underscores only")
        name = f"target {target.id}"
        if target.id in seen:
            raise ValueError(f"{name}: two targets have the id {target.id!r} (the second at "
                             f"position {position})")
        seen.add(target.id)
        if target.kind not in KINDS:
            raise ValueError(f"{name}: kind {target.kind!r} is not one of {', '.join(KINDS)}")
        horizons = target.horizons
        if not isinstance(horizons, list) or not horizons:
            raise ValueError(f"{name}: horizons must be a non-empty list of horizons, "
                             f"got {horizons!r}")
        seen_horizons: set[str] = set()
        for horizon in horizons:
            if not _valid_horizon(horizon):
                raise ValueError(f"{name}: horizon {horizon!r} is not a valid horizon "
                                 f"({_HORIZON_RULE})")
            if horizon in seen_horizons:
                raise ValueError(f"{name}: horizon {horizon!r} is given twice")
            seen_horizons.add(horizon)
        as_of_date = target.as_of_date
        if not isinstance(as_of_date, str) or parse_iso_date(as_of_date) is None:
            raise ValueError(f"{name}: as_of_date {as_of_date!r} is not an ISO date (YYYY-MM-DD)")
        if not is_number(target.as_of_value):
            raise ValueError(f"{name}: as_of_value {target.as_of_value!r} is not a number")


def target_keys(case: Case) -> list[str]:
    """target_key of every target of the case and every horizon of that target.

    Target by target in the case's order, each target's horizons in its order. [] when the
    case has no targets (every earnings case); otherwise check_targets runs first and its
    ValueError passes through.
    """
    if not case.targets:
        return []
    check_targets(case.targets)
    return [target_key(target.id, horizon) for target in case.targets
            for horizon in target.horizons]


def macro_case(case_id: str, as_of, targets, documents) -> Case:
    """A macro case: a snapshot of the markets as of the close of `as_of`.

    `as_of` is stored in its canonical form YYYY-MM-DD, in fiscal_period ("as of <as_of>")
    and as event_date. Raises ValueError when as_of is not an ISO date string, when
    check_targets(targets) fails, or when a target's as_of_date is after as_of (a snapshot
    cannot start from a level it had not seen yet), naming the target.
    """
    when = parse_iso_date(as_of) if isinstance(as_of, str) else None
    if when is None:
        raise ValueError(f"macro case {case_id}: as_of {as_of!r} is not an ISO date string "
                         f"(YYYY-MM-DD)")
    as_of = when.isoformat()
    targets = _target_list(targets)
    check_targets(targets)
    for target in targets:
        if parse_iso_date(target.as_of_date) > when:
            raise ValueError(f"macro case {case_id}: target {target.id}: as_of_date "
                             f"{target.as_of_date} is after as_of {as_of}; a snapshot cannot "
                             f"start from a level it had not seen yet")
    return Case(case_id=case_id, company="", ticker="", aliases=[],
                fiscal_period=f"as of {as_of}", event_date=as_of, documents=list(documents),
                domain="macro", targets=targets)


def resolution_manifest(case: Case, resolved=()) -> dict:
    """Which of a macro case's targets have resolved: dates and flags, never a level.

    {"as_of": <event_date, canonical>, "targets": {key: {"horizon_end": <ISO date>,
    "resolved": <bool>}}}, one entry per key of target_keys(case) in that order; a target's
    horizon ends on horizon_end(<event_date>, horizon). "resolved" is True exactly for the
    keys in `resolved` (any iterable of keys). Raises ValueError when the case is not a
    macro case, when its event_date is not an ISO date string, when target_keys fails, when
    a horizon end is outside the dates Python can represent, or when `resolved` names a key
    the case does not have.
    """
    if case.domain != "macro":
        raise ValueError(f"case {case.case_id}: domain {case.domain!r} is not 'macro'; only a "
                         f"macro case has a resolution manifest")
    value = case.event_date
    as_of = parse_iso_date(value) if isinstance(value, str) else None
    if as_of is None:
        raise ValueError(f"case {case.case_id}: event_date {value!r} is not an ISO date "
                         f"(YYYY-MM-DD)")
    keys = target_keys(case)
    ends: list[str] = []
    for target in case.targets:
        for horizon in target.horizons:
            try:
                ends.append(horizon_end(as_of, horizon).isoformat())
            except ValueError as exc:
                raise ValueError(f"case {case.case_id}: target {target.id}: the {horizon} "
                                 f"horizon from {as_of.isoformat()} does not end on a date: "
                                 f"{exc}") from exc
    marked = list(resolved)
    for key in marked:
        if key not in keys:
            raise ValueError(f"case {case.case_id}: resolved names {key!r}, which is not one of "
                             f"its target keys ({', '.join(keys)})")
    return {"as_of": as_of.isoformat(),
            "targets": {key: {"horizon_end": end, "resolved": key in marked}
                        for key, end in zip(keys, ends)}}


def save_resolution(root, case_id: str, manifest: dict) -> None:
    """Write the manifest as <root>/<case_id>/resolution.json (creating the directory)."""
    case_dir = Path(root) / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    write_json(case_dir / RESOLUTION_FILE, manifest)


def load_resolution(root, case_id: str) -> dict | None:
    """The manifest in <root>/<case_id>/resolution.json; None when there is none.

    Only resolution.json is read: outcome.json is never opened here.
    """
    path = Path(root) / case_id / RESOLUTION_FILE
    if not path.is_file():
        return None
    return read_json(path)


def _is_resolved(entry) -> bool:
    return isinstance(entry, dict) and entry.get("resolved") is True


def unresolved_targets(case: Case, manifest) -> list[str]:
    """The keys of target_keys(case), in that order, that the manifest does not mark resolved.

    A key is resolved only when manifest["targets"][key] is a dict whose "resolved" is True;
    anything else is unresolved (no manifest or one that is not a dict, a missing or
    non-dict "targets", a missing key, a "resolved" that is not True). Keys the manifest has
    and the case does not are ignored. ValueError from target_keys passes through.
    """
    keys = target_keys(case)
    entries = manifest.get("targets") if isinstance(manifest, dict) else None
    if not isinstance(entries, dict):
        entries = {}
    return [key for key in keys if not _is_resolved(entries.get(key))]
