

# ---- building a snapshot (v24 reference) ----------------------------------------------

import logging as _logging
import re as _re
from pathlib import Path as _Path

from tools.dates import horizon_end as _horizon_end

from researchos import cases as _cases
from researchos import series as _series
from researchos.jsonio import read_json as _read_json

_log = _logging.getLogger("researchos")
_PREFIX = _re.compile(r"[A-Za-z0-9_-]+")
_SERIES_ID = _re.compile(r"[A-Za-z0-9_]+")


def _is_series_id(value) -> bool:
    return isinstance(value, str) and _SERIES_ID.fullmatch(value) is not None


def _is_iso(value) -> bool:
    return isinstance(value, str) and parse_iso_date(value) is not None


def _check_list(path, config, key, check):
    items = config.get(key, [])
    if not isinstance(items, list):
        raise ValueError(f"{path}: {key} must be a list")
    for i, item in enumerate(items):
        if not isinstance(item, dict) or not check(item):
            raise ValueError(f"{path}: {key}[{i}] is not a valid entry: {item!r}")
    return items


def load_macro_config(path) -> dict:
    config = _read_json(path)
    if not isinstance(config, dict):
        raise ValueError(f"{path}: a macro config must be a JSON object")
    prefix = config.get("case_prefix")
    if not isinstance(prefix, str) or _PREFIX.fullmatch(prefix) is None:
        raise ValueError(f"{path}: case_prefix must be letters, digits, '_' and '-'")
    horizons = config.get("horizons")
    if (not isinstance(horizons, list) or not horizons
            or len(set(map(repr, horizons))) != len(horizons)):
        raise ValueError(f"{path}: horizons must be a non-empty list without repeats")
    for h in horizons:
        try:
            _cases.target_key("x", h)
        except ValueError:
            raise ValueError(f"{path}: {h!r} is not a horizon") from None
    targets = config.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError(f"{path}: targets must be a non-empty list")
    seen = set()
    for i, t in enumerate(targets):
        ok = isinstance(t, dict)
        if ok:
            try:
                _cases.target_key(t.get("id"), "1w")
            except ValueError:
                ok = False
        ok = ok and isinstance(t.get("name"), str) and isinstance(t.get("unit"), str) \
            and t.get("kind") in _cases.KINDS and isinstance(t.get("optional", False), bool) \
            and t["id"] not in seen
        if not ok:
            raise ValueError(f"{path}: targets[{i}] is not a valid target: {t!r}")
        seen.add(t["id"])
    _check_list(path, config, "context", lambda e: _is_series_id(e.get("id"))
                and isinstance(e.get("name"), str) and isinstance(e.get("unit"), str))

    def lag_ok(v):
        return isinstance(v, int) and not isinstance(v, bool) and v >= 0

    _check_list(path, config, "positioning", lambda e: _is_series_id(e.get("id"))
                and _is_series_id(e.get("net")) and isinstance(e.get("name"), str)
                and (e.get("open_interest") is None or _is_series_id(e.get("open_interest")))
                and lag_ok(e.get("release_lag_days")))

    def doc_ok(e):
        has = [k for k in ("url", "text") if k in e]
        return (isinstance(e.get("kind"), str) and e["kind"] != "" and isinstance(e.get("title"), str)
                and _is_iso(e.get("dated")) and len(has) == 1 and isinstance(e[has[0]], str))

    _check_list(path, config, "documents", doc_ok)
    _check_list(path, config, "calendar", lambda e: _is_iso(e.get("date"))
                and isinstance(e.get("event"), str))
    out = dict(config)
    for key in ("context", "positioning", "documents", "calendar"):
        out[key] = [dict(e) for e in config.get(key, [])]
    out["targets"] = [dict(t, optional=t.get("optional", False)) for t in targets]
    out["positioning"] = [dict(e, open_interest=e.get("open_interest")) for e in out["positioning"]]
    return out


def _load(series_dir, sid, day):
    try:
        return _series.load_series(series_dir, sid, day)
    except OSError:
        return []


def build_snapshot(root, config, as_of, *, series_dir, fetch_page) -> dict:
    day = _day(as_of)
    iso = day.isoformat()
    case_id = f"{config['case_prefix']}-{iso}"
    case_dir = _Path(root) / case_id
    for name in ("prediction.json", _cases.OUTCOME_FILE):
        if (case_dir / name).exists():
            raise ValueError(f"case {case_id}: {name} exists; the snapshot is not rebuilt")
    horizons = list(config["horizons"])
    kept, left_out, missing, obs_by_id = [], [], [], {}
    for t in config["targets"]:
        obs = _load(series_dir, t["id"], day)
        if last_on_or_before(obs, day) is None:
            if t.get("optional", False):
                left_out.append(t["id"])
                _log.warning("snapshot %s: optional target %s has no observation; left out",
                             case_id, t["id"])
            else:
                missing.append(t["id"])
            continue
        kept.append(t)
        obs_by_id[t["id"]] = obs
    if missing:
        raise ValueError(f"case {case_id}: no observation on or before {iso} for "
                         f"{', '.join(missing)}")
    targets = []
    for t in kept:
        obs = obs_by_id[t["id"]]
        d, v = last_on_or_before(obs, day)
        targets.append(_cases.Target(
            id=t["id"], name=t["name"], kind=t["kind"], horizons=list(horizons),
            as_of_date=d.isoformat(), as_of_value=v,
            trailing={h: trailing_change(obs, day, h, t["kind"]) for h in horizons}))
    docs = [_cases.Document("dashboard", f"Markets as of {iso}",
                            target_table(day, kept, obs_by_id))]
    if config["context"]:
        ctx = {e["id"]: _load(series_dir, e["id"], day) for e in config["context"]}
        published = [e["id"] for e in config["context"]
                     if (_Path(series_dir) / f"{e['id']}@{iso}.json").is_file()]
        docs.append(_cases.Document("economy", f"Economic data as of {iso}",
                                    context_table(day, config["context"], ctx, published)))
    if config["positioning"]:
        pos = {}
        for e in config["positioning"]:
            for sid in (e["net"], e.get("open_interest")):
                if sid is not None:
                    pos[sid] = _load(series_dir, sid, day)
        docs.append(_cases.Document("positioning", f"Positioning as of {iso}",
                                    positioning_table(day, config["positioning"], pos)))
    if config["calendar"]:
        through = max(_horizon_end(day, h) for h in horizons)
        docs.append(_cases.Document("calendar", f"Calendar after {iso}",
                                    calendar_list(day, config["calendar"], through)))
    kinds = []
    for e in config["documents"]:
        if e["kind"] not in kinds:
            kinds.append(e["kind"])
    for kind in kinds:
        best = None
        for e in config["documents"]:
            if e["kind"] != kind:
                continue
            dated = parse_iso_date(e["dated"])
            if dated <= day and (best is None or dated >= best[0]):
                best = (dated, e)
        if best is None:
            continue
        dated, e = best
        title = f"{e['title']} ({dated.isoformat()})"
        if "text" in e:
            docs.append(_cases.Document(kind, title, e["text"]))
            continue
        try:
            page = fetch_page(e["url"])
        except (OSError, ValueError) as exc:
            _log.warning("snapshot %s: left out %s: %s", case_id, e["url"], exc)
            continue
        docs.append(_cases.Document(kind, title, page.text, e["url"]))
    case = _cases.macro_case(case_id, iso, targets, docs)
    _cases.save_case(root, case)
    _cases.save_resolution(root, case_id, _cases.resolution_manifest(case))
    return {"case_id": case_id, "as_of": iso, "targets": [t.id for t in targets],
            "left_out": left_out, "documents": [d.kind for d in docs],
            "keys": _cases.target_keys(case)}
