"""Building a snapshot (v24): load_macro_config, build_snapshot and the snapshot command.

Nothing here touches the network: an autouse fixture makes urllib.request.urlopen fail;
build_snapshot is given a fake fetch_page, and the CLI tests replace urlopen with a fake.
"""

import codecs
import json
import logging
import urllib.error
import urllib.request
from datetime import date

import pytest

from test_fetch_by_address import FakeWeb

import researchos.snapshot as snapshot
from researchos.__main__ import main
from researchos.cases import Target, load_case, load_resolution
from researchos.jsonio import write_json
from researchos.series import save_series
from researchos.snapshot import (
    build_snapshot,
    context_table,
    load_macro_config,
    positioning_table,
    target_table,
    trailing_change,
)
from researchos.sources import FetchedPage

AS_OF = "2026-10-30"
CASE_ID = "us-macro-2026-10-30"
DEFAULT_UA = "ResearchOS/0.1 (research use)"
UA = "Acme Research ops@acme.example"

FED = "https://www.federalreserve.gov/newsevents/pressreleases/"
JULY_URL = FED + "monetary20260729a.htm"
SEPTEMBER_URL = FED + "monetary20260916a.htm"
NOVEMBER_URL = FED + "monetary20261104a.htm"  # after the snapshot's date
STATEMENT = "The Committee decided to lower the target range by 1/4 percentage point."
MINUTES = "Participants noted that inflation had moved down."


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a real URL")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


def make_config():
    return {
        "case_prefix": "us-macro",
        # Not by length: the calendar runs to the latest horizon end, not the last one's.
        "horizons": ["1w", "3m", "1m"],
        "targets": [
            {"id": "DGS10", "name": "10-year Treasury yield", "unit": "%", "kind": "bp"},
            {"id": "SP500", "name": "S&P 500", "unit": "index", "kind": "return",
             "optional": False},
            {"id": "VIXCLS", "name": "VIX", "unit": "index", "kind": "points",
             "optional": True},
        ],
        "context": [
            {"id": "UNRATE", "name": "Unemployment rate", "unit": "%"},
            {"id": "CPIAUCSL", "name": "Consumer prices", "unit": "index"},
        ],
        "positioning": [
            {"id": "043602", "name": "10-year note", "net": "TY_NET", "open_interest": "TY_OI",
             "release_lag_days": 3},
            {"id": "13874A", "name": "E-mini S&P 500", "net": "ES_NET", "release_lag_days": 3},
        ],
        "documents": [
            {"kind": "fomc_statement", "title": "FOMC statement", "dated": "2026-07-29",
             "url": JULY_URL},
            {"kind": "minutes", "title": "FOMC minutes", "dated": "2026-10-08",
             "text": MINUTES},
            {"kind": "fomc_statement", "title": "FOMC statement", "dated": "2026-09-16",
             "url": SEPTEMBER_URL},
            {"kind": "fomc_statement", "title": "FOMC statement", "dated": "2026-11-04",
             "url": NOVEMBER_URL},
            {"kind": "speech", "title": "Chair's speech", "dated": "2026-11-02",
             "text": "After the snapshot's date."},
        ],
        "calendar": [
            {"date": "2026-11-06", "event": "Employment Situation"},
            {"date": "2026-10-30", "event": "Personal income"},  # on the date, not after it
            {"date": "2027-01-31", "event": "After every horizon"},
            {"date": "2027-01-30", "event": "The 3m horizon ends"},
            {"date": "2026-11-04", "event": "FOMC statement"},
        ],
        "notes": "a key the configuration does not name",
    }


SERIES = {
    "DGS10": [(date(2026, 7, 30), 4.20), (date(2026, 9, 30), 4.10), (date(2026, 10, 23), 3.90),
              (date(2026, 10, 30), 4.05), (date(2026, 11, 2), 9.99)],
    "SP500": [(date(2026, 7, 30), 6000.0), (date(2026, 10, 23), 6500.0),
              (date(2026, 10, 29), 6600.0)],
    "UNRATE": [(date(2026, 8, 1), 4.3), (date(2026, 9, 1), 4.4), (date(2026, 10, 1), 4.5)],
    "TY_NET": [(date(2026, 10, 13), -110000.0), (date(2026, 10, 20), -100000.0),
               (date(2026, 10, 27), -90000.0)],
    "TY_OI": [(date(2026, 10, 20), 4000000.0), (date(2026, 10, 27), 4100000.0)],
    "ES_NET": [(date(2026, 10, 20), 40000.0), (date(2026, 10, 27), 50000.0)],
}
# UNRATE as it was published by the snapshot's date: its vintage file.
VINTAGES = {"UNRATE": [(date(2026, 8, 1), 4.2), (date(2026, 9, 1), 4.3)]}

SUMMARY = {
    "case_id": CASE_ID,
    "as_of": AS_OF,
    "targets": ["DGS10", "SP500"],
    "left_out": ["VIXCLS"],
    "documents": ["dashboard", "economy", "positioning", "calendar", "fomc_statement",
                  "minutes"],
    "keys": ["DGS10_1w", "DGS10_3m", "DGS10_1m", "SP500_1w", "SP500_3m", "SP500_1m"],
}


def cut(observations):
    return [pair for pair in observations if pair[0] <= date(2026, 10, 30)]


def write_series(tmp_path, series=SERIES, vintages=VINTAGES):
    series_dir = tmp_path / "series"
    series_dir.mkdir(exist_ok=True)
    for series_id, observations in series.items():
        save_series(series_dir / f"{series_id}.json", observations)
    for series_id, observations in vintages.items():
        save_series(series_dir / f"{series_id}@{AS_OF}.json", observations)
    return series_dir


def write_config(tmp_path, config=None):
    path = tmp_path / "snapshots.json"
    write_json(path, make_config() if config is None else config)
    return path


class Pages:
    """A fetch_page serving {url: text, or an exception to raise}; records each url asked for."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url not in self.pages:
            raise AssertionError(f"fetched an address the test does not serve: {url}")
        served = self.pages[url]
        if isinstance(served, BaseException):
            raise served
        return FetchedPage(url, "a page title", served)


def warnings_of(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == "researchos" and r.levelno == logging.WARNING]


def build(tmp_path, config=None, *, as_of=AS_OF, pages=None, root="root", series_dir=None):
    """build_snapshot over the stored series (written first unless series_dir is given)."""
    series_dir = write_series(tmp_path) if series_dir is None else series_dir
    loaded = load_macro_config(write_config(tmp_path, config))
    pages = Pages({SEPTEMBER_URL: STATEMENT}) if pages is None else pages
    return build_snapshot(tmp_path / root, loaded, as_of, series_dir=series_dir,
                          fetch_page=pages)


# ---- load_macro_config

def test_load_macro_config_fills_in_the_defaults(tmp_path):
    minimal = {"case_prefix": "us_macro-2", "horizons": ["1w"],
               "targets": [{"id": "DGS10", "name": "10-year", "unit": "%", "kind": "bp"}]}
    config = load_macro_config(write_config(tmp_path, minimal))
    assert config["case_prefix"] == "us_macro-2"
    assert config["horizons"] == ["1w"]
    assert config["targets"] == [{"id": "DGS10", "name": "10-year", "unit": "%", "kind": "bp",
                                  "optional": False}]
    assert config["context"] == []
    assert config["positioning"] == []
    assert config["documents"] == []
    assert config["calendar"] == []


def test_load_macro_config_keeps_what_is_given(tmp_path):
    given = make_config()
    config = load_macro_config(write_config(tmp_path, given))
    assert config["horizons"] == ["1w", "3m", "1m"]
    assert [t["optional"] for t in config["targets"]] == [False, False, True]
    assert [p["open_interest"] for p in config["positioning"]] == ["TY_OI", None]
    assert [p["release_lag_days"] for p in config["positioning"]] == [3, 3]
    assert config["context"] == given["context"]
    assert config["documents"] == given["documents"]
    assert config["calendar"] == given["calendar"]


def test_load_macro_config_reads_a_file_windows_wrote(tmp_path):
    path = tmp_path / "utf16.json"
    path.write_bytes(codecs.BOM_UTF16_LE + json.dumps(make_config()).encode("utf-16-le"))
    assert load_macro_config(path)["case_prefix"] == "us-macro"


def test_load_macro_config_missing_file_is_an_oserror(tmp_path):
    with pytest.raises(OSError):
        load_macro_config(tmp_path / "missing.json")


@pytest.mark.parametrize("data", [[], "us-macro", None, 3])
def test_load_macro_config_refuses_what_is_not_an_object(tmp_path, data):
    path = tmp_path / "snapshots.json"
    write_json(path, data)
    with pytest.raises(ValueError) as info:
        load_macro_config(path)
    assert str(path) in str(info.value)


def test_load_macro_config_refuses_what_is_not_json(tmp_path):
    path = tmp_path / "snapshots.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_macro_config(path)


DROP = object()


def edited(where, value):
    """make_config() with the item at ``where`` (keys and indexes) set to value, or dropped."""
    data = make_config()
    *parents, last = where
    holder = data
    for key in parents:
        holder = holder[key]
    if value is DROP:
        del holder[last]
    else:
        holder[last] = value
    return data


def edit_id(edit):
    where, value = edit
    return ".".join(map(str, where)) + (" dropped" if value is DROP else f"={value!r}")


BAD = [
    (("case_prefix",), DROP), (("case_prefix",), 7), (("case_prefix",), ""),
    (("case_prefix",), "us macro"), (("case_prefix",), "us/macro"),
    (("case_prefix",), "us-macro\n"), (("case_prefix",), "macro" + chr(233)),
    (("horizons",), DROP), (("horizons",), None), (("horizons",), []), (("horizons",), "1w"),
    (("horizons",), ["1w", "1w"]), (("horizons",), ["1x"]), (("horizons",), [" 1w"]),
    (("horizons",), ["1w", 7]),
    (("targets",), DROP), (("targets",), []), (("targets",), {}), (("targets",), ["DGS10"]),
    (("targets", 0, "id"), "DGS-10"), (("targets", 0, "id"), DROP), (("targets", 0, "id"), 10),
    (("targets", 1, "id"), "DGS10"),
    (("targets", 0, "name"), DROP), (("targets", 0, "name"), 7), (("targets", 0, "unit"), None),
    (("targets", 0, "kind"), "pct"), (("targets", 0, "kind"), DROP),
    (("targets", 2, "optional"), "yes"), (("targets", 2, "optional"), None),
    (("targets", 2, "optional"), 1),
    (("context",), None), (("context",), {}), (("context",), [7]),
    (("context", 0, "id"), "UN RATE"), (("context", 0, "id"), DROP),
    (("context", 0, "name"), DROP), (("context", 0, "unit"), 5),
    (("positioning",), "043602"), (("positioning", 0, "id"), "04-3602"),
    (("positioning", 0, "id"), DROP), (("positioning", 0, "net"), DROP),
    (("positioning", 0, "net"), "TY NET"), (("positioning", 0, "name"), None),
    (("positioning", 0, "open_interest"), "TY OI"), (("positioning", 0, "open_interest"), 7),
    (("positioning", 0, "release_lag_days"), -1), (("positioning", 0, "release_lag_days"), True),
    (("positioning", 0, "release_lag_days"), 3.0), (("positioning", 0, "release_lag_days"), "3"),
    (("positioning", 0, "release_lag_days"), DROP),
    (("documents",), {}), (("documents",), [None]), (("documents", 0, "kind"), ""),
    (("documents", 0, "kind"), 7), (("documents", 0, "kind"), DROP),
    (("documents", 0, "title"), DROP), (("documents", 0, "title"), None),
    (("documents", 0, "dated"), "2026-13-01"), (("documents", 0, "dated"), DROP),
    (("documents", 0, "dated"), 20260729),
    (("documents", 0, "text"), "both a url and a text"), (("documents", 0, "url"), DROP),
    (("documents", 0, "url"), 7), (("documents", 1, "text"), None),
    (("calendar",), "2026-11-06"), (("calendar",), [[]]), (("calendar", 0, "date"), "soon"),
    (("calendar", 0, "date"), DROP), (("calendar", 0, "date"), None),
    (("calendar", 0, "event"), DROP), (("calendar", 0, "event"), 5),
]


@pytest.mark.parametrize("where, value", BAD, ids=[edit_id(edit) for edit in BAD])
def test_load_macro_config_refuses_a_bad_config_naming_the_file(tmp_path, where, value):
    path = write_config(tmp_path, edited(where, value))
    with pytest.raises(ValueError) as info:
        load_macro_config(path)
    assert str(path) in str(info.value)


GOOD = [
    (("case_prefix",), "A_b-9"), (("horizons",), ["10d", "2y"]), (("targets", 0, "name"), ""),
    (("targets", 0, "source"), "FRED"), (("targets", 2, "optional"), False),
    (("positioning", 0, "open_interest"), None), (("positioning", 0, "release_lag_days"), 0),
    (("documents", 0, "dated"), " 2026-07-29 "), (("calendar", 0, "event"), ""),
    (("context",), []), (("calendar",), []),
]


@pytest.mark.parametrize("where, value", GOOD, ids=[edit_id(edit) for edit in GOOD])
def test_load_macro_config_accepts(tmp_path, where, value):
    load_macro_config(write_config(tmp_path, edited(where, value)))


# ---- build_snapshot

def test_build_snapshot_saves_the_case_of_the_date(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="researchos")
    pages = Pages({SEPTEMBER_URL: STATEMENT})
    assert build(tmp_path, pages=pages) == SUMMARY
    # Only the statement taken is fetched: neither the one before it nor the one after.
    assert pages.calls == [SEPTEMBER_URL]
    root = tmp_path / "root"
    # No outcome: the case's targets resolve later.
    assert sorted(p.name for p in (root / CASE_ID).iterdir()) == ["case.json", "resolution.json"]
    case = load_case(root, CASE_ID)
    assert (case.case_id, case.domain, case.event_date) == (CASE_ID, "macro", AS_OF)
    assert (case.company, case.ticker, case.aliases) == ("", "", [])
    assert case.fiscal_period == "as of 2026-10-30"
    assert [(d.kind, d.title, d.source) for d in case.documents] == [
        ("dashboard", "Markets as of 2026-10-30", ""),
        ("economy", "Economic data as of 2026-10-30", ""),
        ("positioning", "Positioning as of 2026-10-30", ""),
        ("calendar", "Calendar after 2026-10-30", ""),
        ("fomc_statement", "FOMC statement (2026-09-16)", SEPTEMBER_URL),
        ("minutes", "FOMC minutes (2026-10-08)", ""),
    ]
    assert case.documents[4].text == STATEMENT
    assert case.documents[5].text == MINUTES
    warnings = warnings_of(caplog)
    assert len(warnings) == 1 and "VIXCLS" in warnings[0]


def test_build_snapshot_targets_start_from_the_last_observation(tmp_path):
    build(tmp_path)
    dgs10, sp500 = load_case(tmp_path / "root", CASE_ID).targets
    horizons = ["1w", "3m", "1m"]
    assert dgs10 == Target(
        id="DGS10", name="10-year Treasury yield", kind="bp", horizons=horizons,
        as_of_date="2026-10-30", as_of_value=4.05,
        trailing={h: trailing_change(cut(SERIES["DGS10"]), AS_OF, h, "bp") for h in horizons})
    assert dgs10.trailing == pytest.approx({"1w": 15.0, "3m": -15.0, "1m": -5.0})
    assert (sp500.id, sp500.kind, sp500.horizons) == ("SP500", "return", horizons)
    # Its last observation on or before the date is the day before.
    assert (sp500.as_of_date, sp500.as_of_value) == ("2026-10-29", 6600.0)
    assert sp500.trailing == pytest.approx({"1w": 6600 / 6500 - 1, "3m": 0.1, "1m": 0.1})


def test_build_snapshot_tables_show_what_was_known(tmp_path):
    build(tmp_path)
    config = load_macro_config(tmp_path / "snapshots.json")
    texts = {d.kind: d.text for d in load_case(tmp_path / "root", CASE_ID).documents}
    assert texts["dashboard"] == target_table(
        AS_OF, config["targets"][:2], {"DGS10": cut(SERIES["DGS10"]), "SP500": cut(SERIES["SP500"])})
    assert "9.99" not in texts["dashboard"]
    # UNRATE as published by the date; CPIAUCSL has no file at all.
    assert texts["economy"] == context_table(AS_OF, config["context"],
                                             {"UNRATE": VINTAGES["UNRATE"]}, {"UNRATE"})
    assert "| Unemployment rate (UNRATE, %) | 4.30 | 2026-09-01 | 4.20 |" in texts["economy"]
    assert ("| Consumer prices (CPIAUCSL, index) | n/a | n/a | n/a | n/a | n/a | current values |"
            in texts["economy"])
    assert texts["positioning"] == positioning_table(
        AS_OF, config["positioning"], {k: SERIES[k] for k in ("TY_NET", "TY_OI", "ES_NET")})
    # Through the latest horizon end: 3m from 2026-10-30.
    assert texts["calendar"] == ("Scheduled after 2026-10-30, through 2027-01-30:\n"
                                 "\n"
                                 "- 2026-11-04: FOMC statement\n"
                                 "- 2026-11-06: Employment Situation\n"
                                 "- 2027-01-30: The 3m horizon ends\n")


def test_build_snapshot_saves_the_manifest_nothing_resolved(tmp_path):
    build(tmp_path)
    manifest = load_resolution(tmp_path / "root", CASE_ID)
    assert manifest["as_of"] == AS_OF
    assert list(manifest["targets"]) == SUMMARY["keys"]
    assert manifest["targets"]["DGS10_1w"] == {"horizon_end": "2026-11-06", "resolved": False}
    assert manifest["targets"]["DGS10_3m"] == {"horizon_end": "2027-01-30", "resolved": False}
    assert manifest["targets"]["SP500_1m"] == {"horizon_end": "2026-11-30", "resolved": False}
    assert not any(entry["resolved"] for entry in manifest["targets"].values())


def test_build_snapshot_same_inputs_same_bytes(tmp_path):
    series_dir = write_series(tmp_path)
    files = []
    for root in ("a", "b", "a"):  # "a" twice: building again before it is sealed rewrites it
        build(tmp_path, root=root, series_dir=series_dir)
        files.append([(tmp_path / root / CASE_ID / name).read_bytes()
                      for name in ("case.json", "resolution.json")])
    assert files[0] == files[1] == files[2]


def test_build_snapshot_again_rewrites_the_case(tmp_path):
    series_dir = write_series(tmp_path)
    build(tmp_path, series_dir=series_dir)
    before = (tmp_path / "root" / CASE_ID / "case.json").read_bytes()
    save_series(series_dir / "VIXCLS.json", [(date(2026, 10, 30), 16.5)])
    summary = build(tmp_path, series_dir=series_dir,
                    pages=Pages({SEPTEMBER_URL: "A revised page."}))
    assert summary["targets"] == ["DGS10", "SP500", "VIXCLS"]
    assert summary["left_out"] == []
    assert (tmp_path / "root" / CASE_ID / "case.json").read_bytes() != before
    case = load_case(tmp_path / "root", CASE_ID)
    assert case.targets[2].as_of_value == 16.5
    assert case.documents[4].text == "A revised page."
    keys = list(load_resolution(tmp_path / "root", CASE_ID)["targets"])
    assert keys[-3:] == ["VIXCLS_1w", "VIXCLS_3m", "VIXCLS_1m"]


@pytest.mark.parametrize("sealed", ["prediction.json", "outcome.json"])
def test_build_snapshot_never_rebuilds_a_sealed_or_resolved_case(tmp_path, monkeypatch, sealed):
    series_dir = write_series(tmp_path)
    build(tmp_path, series_dir=series_dir)
    case_dir = tmp_path / "root" / CASE_ID
    (case_dir / sealed).write_text("{}", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in case_dir.iterdir()}

    def no_reading(*args, **kwargs):
        raise AssertionError("a series was read")

    monkeypatch.setattr(snapshot, "load_series", no_reading)
    pages = Pages({})
    with pytest.raises(ValueError, match=CASE_ID):
        build(tmp_path, series_dir=series_dir, pages=pages)
    assert pages.calls == []
    assert {p.name: p.read_bytes() for p in case_dir.iterdir()} == before


def test_build_snapshot_refuses_missing_targets_before_fetching_or_writing(tmp_path):
    config = make_config()
    config["targets"].append({"id": "DEXJPUS", "name": "Yen per dollar", "unit": "yen",
                              "kind": "return"})
    # SP500 is seen only after the date; DEXJPUS has no file at all.
    series_dir = write_series(tmp_path, dict(SERIES, SP500=[(date(2026, 11, 2), 6700.0)]))
    pages = Pages({})
    with pytest.raises(ValueError) as info:
        build(tmp_path, config, series_dir=series_dir, pages=pages)
    message = str(info.value)
    assert "SP500" in message and "DEXJPUS" in message
    assert "VIXCLS" not in message
    assert not (tmp_path / "root").exists()
    assert pages.calls == []


def test_build_snapshot_needs_one_target(tmp_path):
    config = make_config()
    config["targets"] = [config["targets"][2]]  # VIXCLS: optional, and no series
    pages = Pages({})
    with pytest.raises(ValueError, match="VIXCLS"):
        build(tmp_path, config, pages=pages)
    assert not (tmp_path / "root").exists()
    assert pages.calls == []


@pytest.mark.parametrize("error", [
    urllib.error.URLError("connection refused"),
    TimeoutError("timed out"),
    ValueError("cannot read content type 'application/pdf'"),
])
def test_build_snapshot_leaves_out_a_page_it_cannot_fetch(tmp_path, caplog, error):
    caplog.set_level(logging.WARNING, logger="researchos")
    pages = Pages({SEPTEMBER_URL: error})
    summary = build(tmp_path, pages=pages)
    assert summary["documents"] == ["dashboard", "economy", "positioning", "calendar", "minutes"]
    # The statement before it is not fetched in its place.
    assert pages.calls == [SEPTEMBER_URL]
    assert any(SEPTEMBER_URL in message for message in warnings_of(caplog))
    documents = load_case(tmp_path / "root", CASE_ID).documents
    assert [d.kind for d in documents] == summary["documents"]


def test_build_snapshot_lets_other_fetch_errors_through(tmp_path):
    def broken(url):
        raise RuntimeError("a bug, not a dead link")

    with pytest.raises(RuntimeError):
        build(tmp_path, pages=broken)
    assert not (tmp_path / "root").exists()


def test_build_snapshot_takes_the_latest_text_of_each_kind_published_by_then(tmp_path):
    config = make_config()
    del config["context"], config["positioning"], config["calendar"]
    config["documents"] = [
        {"kind": "minutes", "title": "Minutes", "dated": "2026-11-20",
         "url": "https://x.example/minutes-november"},
        {"kind": "statement", "title": "Statement", "dated": "2026-10-01", "text": "October"},
        {"kind": "statement", "title": "Statement", "dated": "2026-10-30", "text": "first"},
        {"kind": "statement", "title": "Late statement", "dated": " 2026-10-30 ",
         "url": "https://x.example/late"},
        {"kind": "statement", "title": "Statement", "dated": "2026-09-01", "text": "September"},
        {"kind": "minutes", "title": "Minutes", "dated": "2026-10-08", "text": "October minutes"},
        {"kind": "speech", "title": "Speech", "dated": "2026-10-31",
         "url": "https://x.example/speech"},
    ]
    pages = Pages({"https://x.example/late": "the later entry"})
    summary = build(tmp_path, config, pages=pages)
    # Kinds in the order they first appear; the speech has nothing by the date.
    assert summary["documents"] == ["dashboard", "minutes", "statement"]
    assert pages.calls == ["https://x.example/late"]
    documents = load_case(tmp_path / "root", CASE_ID).documents
    assert [(d.kind, d.title, d.text, d.source) for d in documents[1:]] == [
        ("minutes", "Minutes (2026-10-08)", "October minutes", ""),
        ("statement", "Late statement (2026-10-30)", "the later entry", "https://x.example/late"),
    ]


@pytest.mark.parametrize("as_of", [date(2026, 10, 30), " 2026-10-30 "])
def test_build_snapshot_as_of_is_a_date_argument(tmp_path, as_of):
    summary = build(tmp_path, as_of=as_of)
    assert (summary["case_id"], summary["as_of"]) == (CASE_ID, AS_OF)
    assert (tmp_path / "root" / CASE_ID / "case.json").is_file()


@pytest.mark.parametrize("as_of", ["2026-13-01", "soon", 20261030, None])
def test_build_snapshot_refuses_a_bad_as_of(tmp_path, as_of):
    pages = Pages({})
    with pytest.raises(ValueError):
        build(tmp_path, as_of=as_of, pages=pages)
    assert not (tmp_path / "root").exists()
    assert pages.calls == []


# ---- the snapshot command

PAGE = (b"<html><head><title>FOMC</title></head><body><p>" + STATEMENT.encode("ascii")
        + b"</p></body></html>")
WEB = {SEPTEMBER_URL: (PAGE, "text/html; charset=utf-8")}


def no_client():
    raise AssertionError("the snapshot command built a client")


def cli_args(tmp_path, config=None, series=SERIES):
    series_dir = write_series(tmp_path, series)
    return ["snapshot", "--root", str(tmp_path / "root"),
            "--config", str(write_config(tmp_path, config)),
            "--as-of", AS_OF, "--series-dir", str(series_dir)]


def test_cli_snapshot(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("RESEARCHOS_USER_AGENT", raising=False)
    web = FakeWeb(WEB)
    monkeypatch.setattr(urllib.request, "urlopen", web)
    code = main(cli_args(tmp_path), client_factory=no_client)
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert json.loads(captured.out) == SUMMARY
    assert web.urls() == [SEPTEMBER_URL]
    assert web.user_agents() == {DEFAULT_UA}
    case = load_case(tmp_path / "root", CASE_ID)
    assert (case.documents[4].text, case.documents[4].source) == (STATEMENT, SEPTEMBER_URL)
    assert not (tmp_path / "root" / CASE_ID / "outcome.json").exists()


def test_cli_snapshot_out(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", FakeWeb(WEB))
    out = tmp_path / "summary.json"
    code = main(cli_args(tmp_path) + ["--out", str(out)], client_factory=no_client)
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert captured.out == ""
    assert json.loads(out.read_text(encoding="utf-8")) == SUMMARY


@pytest.mark.parametrize("env, extra, expected", [
    (None, [], DEFAULT_UA),
    ("   ", [], DEFAULT_UA),
    (UA, [], UA),
    (UA, ["--user-agent", "Cli Tester cli@example.com"], "Cli Tester cli@example.com"),
])
def test_cli_snapshot_user_agent(tmp_path, capsys, monkeypatch, env, extra, expected):
    if env is None:
        monkeypatch.delenv("RESEARCHOS_USER_AGENT", raising=False)
    else:
        monkeypatch.setenv("RESEARCHOS_USER_AGENT", env)
    web = FakeWeb(WEB)
    monkeypatch.setattr(urllib.request, "urlopen", web)
    code = main(cli_args(tmp_path) + extra, client_factory=no_client)
    assert code == 0, capsys.readouterr().err
    assert web.user_agents() == {expected}


@pytest.mark.parametrize("drop, value", [
    (None, ("--as-of", "2026-13-01")),
    (None, ("--as-of", "soon")),
    ("--as-of", None),
    ("--root", None),
    ("--config", None),
    ("--series-dir", None),
])
def test_cli_snapshot_bad_arguments_exit_2(tmp_path, capsys, drop, value):
    args = cli_args(tmp_path)
    if drop is not None:
        at = args.index(drop)
        del args[at:at + 2]
    if value is not None:
        args[args.index(value[0]) + 1] = value[1]
    code = main(args, client_factory=no_client)
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert not (tmp_path / "root").exists()


def test_cli_snapshot_missing_config_exits_1(tmp_path, capsys):
    args = cli_args(tmp_path)
    args[args.index("--config") + 1] = str(tmp_path / "missing.json")
    code = main(args, client_factory=no_client)
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == "" and captured.err.startswith("error:")
    assert not (tmp_path / "root").exists()


def test_cli_snapshot_bad_config_exits_1(tmp_path, capsys):
    args = cli_args(tmp_path, edited(("case_prefix",), "us macro"))
    code = main(args, client_factory=no_client)
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == "" and "case_prefix" in captured.err
    assert not (tmp_path / "root").exists()


def test_cli_snapshot_missing_target_exits_1(tmp_path, capsys):
    series = {k: v for k, v in SERIES.items() if k != "SP500"}
    code = main(cli_args(tmp_path, series=series), client_factory=no_client)
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == "" and "SP500" in captured.err
    assert not (tmp_path / "root").exists()


def test_cli_snapshot_sealed_case_exits_1(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", FakeWeb(WEB))
    args = cli_args(tmp_path)
    assert main(args, client_factory=no_client) == 0
    capsys.readouterr()
    case_dir = tmp_path / "root" / CASE_ID
    (case_dir / "prediction.json").write_text("{}", encoding="utf-8")
    before = (case_dir / "case.json").read_bytes()
    code = main(args, client_factory=no_client)
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == "" and CASE_ID in captured.err
    assert (case_dir / "case.json").read_bytes() == before
