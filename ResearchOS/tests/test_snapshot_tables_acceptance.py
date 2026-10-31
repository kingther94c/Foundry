"""Acceptance tests for snapshot tables (v23), written from SPEC.md independently of the code
(held back from the agent that built it).

Each case traces to the "Snapshot tables (v23)" section of SPEC.md. Every expected table was
worked out by hand from the fixtures below.
"""

from __future__ import annotations

import random
from datetime import date, datetime
from pathlib import Path

import pytest

from researchos import snapshot

NL = chr(10)
AS_OF = date(2026, 10, 30)

DGS10 = [(date(2025, 10, 30), 4.10), (date(2026, 1, 15), 4.50), (date(2026, 7, 30), 4.40),
         (date(2026, 9, 30), 5.31), (date(2026, 10, 23), 5.00), (date(2026, 10, 29), 4.98),
         (date(2026, 11, 2), 9.99)]
SP500 = [(date(2025, 10, 29), 5000.0), (date(2026, 7, 30), 7000.0), (date(2026, 9, 30), 7600.0),
         (date(2026, 10, 23), 7800.0), (date(2026, 10, 28), 7743.98)]
VIX = [(date(2026, 10, 29), 17.20)]
STALE = [(date(2025, 6, 1), 3.0)]

TARGETS = [
    {"id": "DGS10", "name": "10-year Treasury yield", "unit": "percent", "kind": "bp"},
    {"id": "SP500", "name": "S&P 500", "unit": "index", "kind": "return"},
    {"id": "VIXCLS", "name": "VIX", "unit": "index points", "kind": "points"},
    {"id": "NASDAQQGLDI", "name": "Gold", "unit": "index", "kind": "return"},
    {"id": "OLD", "name": "Stale", "unit": "percent", "kind": "bp"},
]
SERIES = {"DGS10": DGS10, "SP500": SP500, "VIXCLS": VIX, "OLD": STALE}

TARGET_TABLE = NL.join([
    "Each level is the last observation on or before 2026-10-30; each change runs to it from the "
    "last observation on or before the date that far back.",
    "",
    "| Target | Level | On | 1w | 1m | 3m | 1y | 1y range | 1y percentile |",
    "|---|---|---|---|---|---|---|---|---|",
    "| 10-year Treasury yield (DGS10, percent) | 4.98 | 2026-10-29 | -2 bp | -33 bp | +58 bp | +88 bp "
    "| 4.40 to 5.31 | 50 |",
    "| S&P 500 (SP500, index) | 7743.98 | 2026-10-28 | -0.7% | +1.9% | +10.6% | +54.9% "
    "| 7000.00 to 7800.00 | 62 |",
    "| VIX (VIXCLS, index points) | 17.20 | 2026-10-29 | n/a | n/a | n/a | n/a | 17.20 to 17.20 | 50 |",
    "| Gold (NASDAQQGLDI, index) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |",
    "| Stale (OLD, percent) | 3.00 | 2025-06-01 | n/a | n/a | n/a | n/a | n/a | n/a |",
]) + NL


def test_lookbacks():
    assert snapshot.LOOKBACKS == ("1w", "1m", "3m", "1y")


# --- trailing_change -----------------------------------------------------------------------------

@pytest.mark.parametrize("horizon,expected", [("1w", -2.0), ("1m", -33.0), ("3m", 58.0),
                                              ("1y", 88.0), ("2w", -33.0), ("10d", -33.0)])
def test_trailing_change_bp(horizon, expected):
    assert snapshot.trailing_change(DGS10, AS_OF, horizon, "bp") == pytest.approx(expected)


def test_trailing_change_return_and_points():
    assert snapshot.trailing_change(SP500, AS_OF, "1y", "return") == pytest.approx(7743.98 / 5000 - 1)
    assert snapshot.trailing_change(SP500, "2026-10-30", "1w", "return") == \
        pytest.approx(7743.98 / 7800 - 1)
    pts = [(date(2026, 10, 23), 18.0), (date(2026, 10, 30), 17.15)]
    assert snapshot.trailing_change(pts, AS_OF, "1w", "points") == pytest.approx(-0.85)


def test_trailing_change_any_order_and_no_future():
    shuffled = list(DGS10)
    random.Random(3).shuffle(shuffled)
    assert snapshot.trailing_change(shuffled, AS_OF, "1w", "bp") == pytest.approx(-2.0)
    # the 2026-11-02 value never counts
    assert snapshot.trailing_change(DGS10, date(2026, 11, 3), "1w", "bp") == pytest.approx(
        (9.99 - 5.00) * 100)


def test_trailing_change_none():
    assert snapshot.trailing_change([], AS_OF, "1w", "bp") is None
    assert snapshot.trailing_change(VIX, AS_OF, "1w", "points") is None  # no start
    assert snapshot.trailing_change(STALE, AS_OF, "1w", "bp") is None  # the same observation
    assert snapshot.trailing_change([(date(2026, 10, 31), 1.0)], AS_OF, "1w", "bp") is None
    zero = [(date(2026, 10, 23), 0.0), (date(2026, 10, 30), 5.0)]
    assert snapshot.trailing_change(zero, AS_OF, "1w", "return") is None  # level_change: None


@pytest.mark.parametrize("horizon", ["1W", "0d", " ", "1mo", None, 1])
def test_trailing_change_bad_horizon(horizon):
    with pytest.raises(ValueError):
        snapshot.trailing_change([], AS_OF, horizon, "bp")


@pytest.mark.parametrize("kind", ["bps", "", None, "Return"])
def test_trailing_change_bad_kind(kind):
    with pytest.raises(ValueError):
        snapshot.trailing_change([], AS_OF, "1w", kind)
    with pytest.raises(ValueError):
        snapshot.trailing_change(DGS10, AS_OF, "1w", kind)


@pytest.mark.parametrize("as_of", ["2026-10-32", "yesterday", 20261030, None, 3.5])
def test_trailing_change_bad_as_of(as_of):
    with pytest.raises(ValueError):
        snapshot.trailing_change(DGS10, as_of, "1w", "bp")


# --- formatting --------------------------------------------------------------------------------

@pytest.mark.parametrize("value,text", [(4.98, "4.98"), (7743.98, "7743.98"), (0, "0.00"),
                                        (159044, "159044.00"), (-0.35, "-0.35"), (2.345678, "2.35"),
                                        (None, "n/a"), (True, "n/a"), ("4.2", "n/a"),
                                        (float("nan"), "n/a"), (float("inf"), "n/a")])
def test_format_level(value, text):
    assert snapshot.format_level(value) == text


@pytest.mark.parametrize("change,kind,text", [
    (12.0, "bp", "+12 bp"), (-33.4, "bp", "-33 bp"), (0, "bp", "+0 bp"), (157.6, "bp", "+158 bp"),
    (-0.0123, "return", "-1.2%"), (0.548796, "return", "+54.9%"), (0.0, "return", "+0.0%"),
    (0.85, "points", "+0.85"), (-1.3, "points", "-1.30"), (2, "points", "+2.00"),
    (None, "bp", "n/a"), (True, "return", "n/a"), ("1", "points", "n/a"), (float("nan"), "bp", "n/a"),
])
def test_format_change(change, kind, text):
    assert snapshot.format_change(change, kind) == text


@pytest.mark.parametrize("change", [1.0, None])
def test_format_change_bad_kind(change):
    with pytest.raises(ValueError):
        snapshot.format_change(change, "pct")


# --- target_table ----------------------------------------------------------------------------------

def test_target_table():
    assert snapshot.target_table(AS_OF, TARGETS, SERIES) == TARGET_TABLE


def test_target_table_takes_an_iso_string_and_any_order():
    shuffled = {k: list(reversed(v)) for k, v in SERIES.items()}
    assert snapshot.target_table(" 2026-10-30 ", TARGETS, shuffled) == TARGET_TABLE


def test_target_table_never_shows_a_later_value():
    later = dict(SERIES, DGS10=DGS10 + [(date(2026, 10, 31), 1.0), (date(2027, 1, 1), 2.0)])
    assert snapshot.target_table(AS_OF, TARGETS, later) == TARGET_TABLE


def test_target_table_empty():
    expected = NL.join([TARGET_TABLE.split(NL)[0], "",
                        "| Target | Level | On | 1w | 1m | 3m | 1y | 1y range | 1y percentile |",
                        "|---|---|---|---|---|---|---|---|---|"]) + NL
    assert snapshot.target_table(AS_OF, [], SERIES) == expected


def test_target_table_window_starts_after_a_year_ago():
    obs = [(date(2025, 10, 30), 1.0), (date(2025, 10, 31), 2.0), (date(2026, 10, 30), 3.0)]
    text = snapshot.target_table(AS_OF, [{"id": "X", "name": "X", "unit": "u", "kind": "points"}],
                                 {"X": obs})
    row = text.split(NL)[-2]
    assert row == ("| X (X, u) | 3.00 | 2026-10-30 | +1.00 | +1.00 | +1.00 | +2.00 | 2.00 to 3.00 | 75 |")


def test_target_table_bad_as_of():
    with pytest.raises(ValueError):
        snapshot.target_table("2026-02-30", TARGETS, SERIES)


# --- context_table ----------------------------------------------------------------------------------

UNRATE = [(date(2025, 8, 1), 4.3), (date(2025, 9, 1), 4.1), (date(2026, 8, 1), 4.1),
          (date(2026, 9, 1), 4.2), (date(2026, 11, 1), 9.9)]
PAYEMS = [(date(2026, 9, 1), 159044.0)]
ENTRIES = [{"id": "UNRATE", "name": "Unemployment rate", "unit": "percent"},
           {"id": "PAYEMS", "name": "Nonfarm payrolls", "unit": "thousands"},
           {"id": "CPIAUCSL", "name": "CPI", "unit": "index"}]

CONTEXT_TABLE = NL.join([
    "Economic data, the latest observation on or before 2026-10-30.",
    "",
    "| Series | Latest | For | Previous | Change | A year earlier | Vintage |",
    "|---|---|---|---|---|---|---|",
    "| Unemployment rate (UNRATE, percent) | 4.20 | 2026-09-01 | 4.10 | +0.10 | 4.10 "
    "| as published by 2026-10-30 |",
    "| Nonfarm payrolls (PAYEMS, thousands) | 159044.00 | 2026-09-01 | n/a | n/a | n/a | current values |",
    "| CPI (CPIAUCSL, index) | n/a | n/a | n/a | n/a | n/a | as published by 2026-10-30 |",
]) + NL


def test_context_table():
    series = {"UNRATE": UNRATE, "PAYEMS": PAYEMS}
    assert snapshot.context_table(AS_OF, ENTRIES, series, {"UNRATE", "CPIAUCSL"}) == CONTEXT_TABLE
    assert snapshot.context_table("2026-10-30", ENTRIES, series, ["CPIAUCSL", "UNRATE"]) == \
        CONTEXT_TABLE


def test_context_table_previous_is_the_observation_before_the_latest():
    obs = [(date(2026, 7, 1), 1.0), (date(2026, 9, 1), 1.5), (date(2026, 8, 1), 2.5),
           (date(2025, 8, 15), 0.5)]
    text = snapshot.context_table(AS_OF, [{"id": "X", "name": "X", "unit": "u"}], {"X": obs}, ())
    assert text.split(NL)[-2] == "| X (X, u) | 1.50 | 2026-09-01 | 2.50 | -1.00 | 0.50 | current values |"


# --- positioning_table ------------------------------------------------------------------------------

TY_NET = [(date(2025, 10, 28), -500000.0), (date(2026, 1, 6), -1200000.0),
          (date(2026, 10, 20), -964293.0), (date(2026, 10, 27), -981538.0), (date(2026, 11, 3), 1.0)]
TY_OI = [(date(2026, 10, 20), 5868295.0), (date(2026, 10, 27), 5881588.0)]
POSITIONS = {"TY_net": TY_NET, "TY_oi": TY_OI, "ES_net": [(date(2026, 10, 27), 150000.0)],
             "ES_oi": [(date(2026, 10, 27), 2000000.0)]}
INTRO = ("Speculative positioning: non-commercial net contracts from the CFTC's Commitments of "
         "Traders, each report as it was public on 2026-10-30.")
HEAD = ["| Contract | Net | Report of | Weekly change | 52-week percentile | Share of open interest |",
        "|---|---|---|---|---|---|"]


def entry(id="TY", lag=3, oi="TY_oi", net=None):
    return {"id": id, "name": {"TY": "10-year T-note futures", "ES": "E-mini S&P 500"}.get(id, id),
            "net": net or f"{id}_net", "open_interest": oi, "release_lag_days": lag}


def test_positioning_table():
    text = snapshot.positioning_table(AS_OF, [entry(), entry("ES", oi="ES_oi"), entry("CL")],
                                      POSITIONS)
    assert text == NL.join([INTRO, "", *HEAD,
                            "| 10-year T-note futures (TY) | -981,538 | 2026-10-27 | -17,245 | 50 | -16.7% |",
                            "| E-mini S&P 500 (ES) | 150,000 | 2026-10-27 | n/a | 50 | +7.5% |",
                            "| CL (CL) | n/a | n/a | n/a | n/a | n/a |"]) + NL


def test_positioning_release_lag():
    text = snapshot.positioning_table("2026-10-30", [entry(lag=4)], POSITIONS)
    assert text.split(NL)[-2] == \
        "| 10-year T-note futures (TY) | -964,293 | 2026-10-20 | +235,707 | 50 | -16.4% |"
    text = snapshot.positioning_table(date(2026, 10, 27), [entry(lag=0)], POSITIONS)
    assert "| -981,538 | 2026-10-27 |" in text


def test_positioning_share_needs_the_same_date():
    series = dict(POSITIONS, TY_oi=[(date(2026, 10, 26), 5881588.0)])
    assert snapshot.positioning_table(AS_OF, [entry()], series).split(NL)[-2].endswith("| 50 | n/a |")
    series = dict(POSITIONS, TY_oi=[(date(2026, 10, 27), 0.0)])
    assert snapshot.positioning_table(AS_OF, [entry()], series).split(NL)[-2].endswith("| n/a |")
    assert snapshot.positioning_table(AS_OF, [entry(oi=None)], POSITIONS).split(NL)[-2].endswith(
        "| -17,245 | 50 | n/a |")


@pytest.mark.parametrize("lag", [-1, True, 3.0, "3", None])
def test_positioning_bad_lag_names_the_entry(lag):
    with pytest.raises(ValueError, match="TY"):
        snapshot.positioning_table(AS_OF, [entry(lag=lag)], POSITIONS)


def test_positioning_empty():
    assert snapshot.positioning_table(AS_OF, [], POSITIONS) == NL.join([INTRO, "", *HEAD]) + NL


# --- calendar_list ----------------------------------------------------------------------------------

EVENTS = [{"date": "2026-11-04", "event": "FOMC decision"},
          {"date": "2026-10-30", "event": "same day"},
          {"date": "2026-11-06", "event": "Employment Situation (October)"},
          {"date": "2027-01-30", "event": "edge"},
          {"date": "2027-01-31", "event": "late"},
          {"date": "2026-11-04", "event": "second the same day"},
          {"date": " 2026-12-10 ", "event": "CPI"},
          {"date": "2026-10-01", "event": "past"}]


def test_calendar_list():
    assert snapshot.calendar_list(AS_OF, EVENTS, "2027-01-30") == NL.join([
        "Scheduled after 2026-10-30, through 2027-01-30:", "",
        "- 2026-11-04: FOMC decision", "- 2026-11-04: second the same day",
        "- 2026-11-06: Employment Situation (October)", "- 2026-12-10: CPI", "- 2027-01-30: edge",
    ]) + NL


def test_calendar_list_none():
    assert snapshot.calendar_list("2026-10-30", EVENTS[1:2], date(2026, 11, 6)) == NL.join([
        "Scheduled after 2026-10-30, through 2026-11-06:", "", "- none listed"]) + NL
    assert snapshot.calendar_list(AS_OF, [], AS_OF) == NL.join([
        "Scheduled after 2026-10-30, through 2026-10-30:", "", "- none listed"]) + NL


@pytest.mark.parametrize("day", ["Nov 4", "", None, date(2026, 11, 4), "2026-11-31"])
def test_calendar_list_bad_date_names_the_event(day):
    with pytest.raises(ValueError, match="Thanksgiving"):
        snapshot.calendar_list(AS_OF, [EVENTS[0], {"date": day, "event": "Thanksgiving"}],
                               "2027-01-30")


def test_calendar_list_bad_dates():
    with pytest.raises(ValueError):
        snapshot.calendar_list("soon", EVENTS, "2027-01-30")
    with pytest.raises(ValueError):
        snapshot.calendar_list(AS_OF, EVENTS, 20270130)


def test_dates_written_in_iso_form():
    text = snapshot.calendar_list(datetime(2026, 10, 30, 16, 0), EVENTS[:1], "2026-11-30")
    assert text.startswith("Scheduled after 2026-10-30, through 2026-11-30:")


# --- the source ----------------------------------------------------------------------------------------

def test_snapshot_module_rules():
    text = Path(snapshot.__file__).read_text(encoding="utf-8")
    text.encode("ascii")
    for word in ("anthropic", "fromisoformat", "isfinite", "urllib"):
        assert word not in text, word
    assert "level_change" in text and "percentile_rank" in text and "last_on_or_before" in text
