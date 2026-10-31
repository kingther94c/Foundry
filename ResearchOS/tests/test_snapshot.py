"""Snapshot tables (v23): researchos.snapshot.

The expected rows are worked out by hand from SPEC.md.
"""

from datetime import date

import pytest

from researchos.snapshot import (
    LOOKBACKS,
    calendar_list,
    context_table,
    format_change,
    format_level,
    positioning_table,
    target_table,
    trailing_change,
)

AS_OF = "2026-10-30"

# The 10-year yield, in no particular order.
DGS10 = [
    (date(2026, 10, 30), 4.05),
    (date(2025, 10, 30), 3.50),  # exactly a year back: the 1y start, outside the 1y range
    (date(2026, 11, 2), 9.99),  # after the snapshot: never seen
    (date(2026, 7, 30), 4.20),
    (date(2025, 11, 3), 4.50),
    (date(2026, 10, 23), 3.90),
    (date(2026, 9, 30), 4.10),
]

TARGET_INTRO = ("Each level is the last observation on or before 2026-10-30; each change runs "
                "to it from the last observation on or before the date that far back.\n")
TARGET_HEAD = ("| Target | Level | On | 1w | 1m | 3m | 1y | 1y range | 1y percentile |\n"
               "|---|---|---|---|---|---|---|---|---|\n")


def test_lookbacks():
    assert LOOKBACKS == ("1w", "1m", "3m", "1y")


# ---- trailing_change

def test_trailing_change_runs_from_the_last_observation_that_far_back():
    assert trailing_change(DGS10, AS_OF, "1w", "bp") == pytest.approx(15.0)
    assert trailing_change(DGS10, date(2026, 10, 30), "1m", "bp") == pytest.approx(-5.0)
    assert trailing_change(DGS10, AS_OF, "3m", "points") == pytest.approx(-0.15)
    assert trailing_change(DGS10, AS_OF, "1y", "return") == pytest.approx(4.05 / 3.50 - 1)
    # On 2026-10-29 the end is 2026-10-23 and the start the last on or before 2026-10-22.
    assert trailing_change(DGS10, " 2026-10-29 ", "1w", "bp") == pytest.approx(-20.0)


def test_trailing_change_is_none_without_two_observations():
    assert trailing_change([], AS_OF, "1w", "bp") is None
    # Nothing on or before 2024-10-30.
    assert trailing_change(DGS10, AS_OF, "2y", "bp") is None
    # Start and end are the same observation: nothing was observed in between.
    assert trailing_change([(date(2026, 10, 20), 4.0)], AS_OF, "1w", "bp") is None
    # Only an observation after as_of could be the end.
    assert trailing_change([(date(2026, 11, 2), 4.0), (date(2026, 9, 1), 3.0)], "2026-09-30",
                           "1w", "bp") is None
    # A start before the first date Python has: there is none.
    assert trailing_change(DGS10, AS_OF, "10000y", "bp") is None
    # level_change gives None for a value that is not a number.
    assert trailing_change([(date(2026, 10, 1), None), (date(2026, 10, 30), 1.0)], AS_OF,
                           "1w", "points") is None


@pytest.mark.parametrize("horizon", ["1x", "", "w", "1.5m", 7, None])
def test_trailing_change_refuses_a_bad_horizon(horizon):
    with pytest.raises(ValueError):
        trailing_change([], AS_OF, horizon, "bp")


@pytest.mark.parametrize("kind", ["pct", "BP", "", None, 1])
def test_trailing_change_refuses_a_bad_kind(kind):
    with pytest.raises(ValueError):
        trailing_change([], AS_OF, "1w", kind)
    with pytest.raises(ValueError):
        trailing_change(DGS10, AS_OF, "1w", kind)


# ---- formats

def test_format_level():
    assert format_level(4.05) == "4.05"
    assert format_level(4) == "4.00"
    assert format_level(1234.5678) == "1234.57"
    assert format_level(-0.5) == "-0.50"
    for value in (None, float("nan"), float("inf"), True, "4.05"):
        assert format_level(value) == "n/a"


def test_format_change():
    assert format_change(12.0, "bp") == "+12 bp"
    assert format_change(-0.0123, "return") == "-1.2%"
    assert format_change(0.85, "points") == "+0.85"
    assert format_change(-3.6, "bp") == "-4 bp"
    assert format_change(0.25, "return") == "+25.0%"
    assert format_change(-2, "points") == "-2.00"
    for kind in ("bp", "return", "points"):
        for change in (None, float("nan"), float("-inf"), True, "12"):
            assert format_change(change, kind) == "n/a"


@pytest.mark.parametrize("change", [12.0, None])
@pytest.mark.parametrize("kind", ["pct", "level", "", None])
def test_format_change_refuses_another_kind(change, kind):
    with pytest.raises(ValueError):
        format_change(change, kind)


# ---- target_table

TARGETS = [
    {"id": "DGS10", "name": "10-year Treasury yield", "unit": "percent", "kind": "bp"},
    {"id": "SP500", "name": "S&P 500", "unit": "index", "kind": "return"},
    {"id": "VIXCLS", "name": "VIX", "unit": "index", "kind": "points"},
    {"id": "OLD", "name": "Old level", "unit": "x", "kind": "points"},
]
TARGET_SERIES = {
    "DGS10": DGS10,
    "SP500": [(date(2026, 10, 29), 5100.0), (date(2026, 10, 22), 4950.0),
              (date(2026, 10, 28), 5000.0)],
    "OLD": [(date(2025, 6, 30), 1.5)],
}


def test_target_table():
    assert target_table("20261030", TARGETS, TARGET_SERIES) == (
        TARGET_INTRO + "\n" + TARGET_HEAD
        + "| 10-year Treasury yield (DGS10, percent) | 4.05 | 2026-10-30 | +15 bp | -5 bp "
          "| -15 bp | +55 bp | 3.90 to 4.50 | 30 |\n"
          "| S&P 500 (SP500, index) | 5100.00 | 2026-10-29 | +3.0% | n/a | n/a | n/a "
          "| 4950.00 to 5100.00 | 83 |\n"
          "| VIX (VIXCLS, index) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |\n"
          "| Old level (OLD, x) | 1.50 | 2025-06-30 | n/a | n/a | n/a | n/a | n/a | n/a |\n")


def test_target_table_is_the_same_whatever_the_order_of_the_observations():
    shuffled = {key: list(reversed(value)) for key, value in TARGET_SERIES.items()}
    assert target_table(AS_OF, TARGETS, shuffled) == target_table(date(2026, 10, 30), TARGETS,
                                                                   TARGET_SERIES)


def test_target_table_with_no_targets():
    assert target_table(AS_OF, [], {}) == TARGET_INTRO + "\n" + TARGET_HEAD


def test_target_table_refuses_a_bad_kind():
    target = {"id": "DGS10", "name": "10-year", "unit": "percent", "kind": "pct"}
    with pytest.raises(ValueError, match="DGS10"):
        target_table(AS_OF, [target], {"DGS10": DGS10})


# ---- context_table

def test_context_table():
    entries = [
        {"id": "PAYEMS", "name": "Nonfarm payrolls", "unit": "thousands"},
        {"id": "UNRATE", "name": "Unemployment rate", "unit": "percent"},
        {"id": "CPIAUCSL", "name": "CPI", "unit": "index"},
    ]
    series = {
        "PAYEMS": [(date(2026, 9, 1), 160150.0), (date(2025, 9, 1), 159000.0),
                   (date(2026, 11, 1), 999.0), (date(2026, 8, 1), 160000.0),
                   (date(2025, 10, 1), 159100.0)],
        "UNRATE": [(date(2026, 8, 1), 4.4), (date(2026, 9, 1), 4.3)],
    }
    assert context_table(date(2026, 10, 30), entries, series, {"PAYEMS", "CPIAUCSL"}) == (
        "Economic data, the latest observation on or before 2026-10-30.\n"
        "\n"
        "| Series | Latest | For | Previous | Change | A year earlier | Vintage |\n"
        "|---|---|---|---|---|---|---|\n"
        "| Nonfarm payrolls (PAYEMS, thousands) | 160150.00 | 2026-09-01 | 160000.00 "
        "| +150.00 | 159000.00 | as published by 2026-10-30 |\n"
        "| Unemployment rate (UNRATE, percent) | 4.30 | 2026-09-01 | 4.40 | -0.10 | n/a "
        "| current values |\n"
        "| CPI (CPIAUCSL, index) | n/a | n/a | n/a | n/a | n/a | as published by 2026-10-30 |\n")


# ---- positioning_table

POSITIONING_HEAD = (
    "Speculative positioning: non-commercial net contracts from the CFTC's Commitments of "
    "Traders, each report as it was public on 2026-10-30.\n"
    "\n"
    "| Contract | Net | Report of | Weekly change | 52-week percentile "
    "| Share of open interest |\n"
    "|---|---|---|---|---|---|\n")


def test_positioning_table():
    entries = [
        {"id": "TY", "name": "10-year T-note futures", "net": "TY_NET",
         "open_interest": "TY_OI", "release_lag_days": 3},
        {"id": "TY_LATE", "name": "10-year T-note futures, a day later", "net": "TY_NET",
         "open_interest": "TY_OI", "release_lag_days": 4},
        {"id": "ES", "name": "E-mini S&P 500", "net": "ES_NET", "open_interest": None,
         "release_lag_days": 0},
        {"id": "GC", "name": "Gold", "net": "GC_NET", "open_interest": "GC_OI",
         "release_lag_days": 3},
    ]
    series = {
        # 2025-10-28 is exactly 52 weeks before 2026-10-27: outside that report's window.
        "TY_NET": [(date(2026, 10, 27), -123456.0), (date(2025, 10, 28), -100000.0),
                   (date(2026, 11, 3), 5.0), (date(2026, 10, 13), -120000.0),
                   (date(2026, 10, 20), -150000.0)],
        "TY_OI": [(date(2026, 10, 27), 4000000.0), (date(2026, 10, 19), 3900000.0)],
        "ES_NET": [(date(2026, 10, 30), 2500.0)],
        "GC_OI": [(date(2026, 10, 27), 500000.0)],
    }
    assert positioning_table(AS_OF, entries, series) == (
        POSITIONING_HEAD
        + "| 10-year T-note futures (TY) | -123,456 | 2026-10-27 | +26,544 | 50 | -3.1% |\n"
          "| 10-year T-note futures, a day later (TY_LATE) | -150,000 | 2026-10-20 | -30,000 "
          "| 17 | n/a |\n"
          "| E-mini S&P 500 (ES) | 2,500 | 2026-10-30 | n/a | 50 | n/a |\n"
          "| Gold (GC) | n/a | n/a | n/a | n/a | n/a |\n")


def test_positioning_share_of_open_interest():
    def row(open_interest, lag=0):
        entries = [{"id": "X", "name": "X", "net": "X_NET", "open_interest": "X_OI",
                    "release_lag_days": lag}]
        series = {"X_NET": [(date(2026, 10, 27), 1500.0)],
                  "X_OI": [(date(2026, 10, 27), open_interest)]}
        return positioning_table(AS_OF, entries, series)[len(POSITIONING_HEAD):]

    assert row(20000.0) == "| X (X) | 1,500 | 2026-10-27 | n/a | 50 | +7.5% |\n"
    assert row(0.0) == "| X (X) | 1,500 | 2026-10-27 | n/a | 50 | n/a |\n"
    # A lag too long for the calendar: no report was public yet.
    assert row(20000.0, 10 ** 12) == "| X (X) | n/a | n/a | n/a | n/a | n/a |\n"


@pytest.mark.parametrize("lag", [-1, True, False, 3.0, "3", None])
def test_positioning_table_refuses_a_bad_release_lag(lag):
    entries = [{"id": "TY", "name": "T-note", "net": "TY_NET", "open_interest": None,
                "release_lag_days": lag}]
    with pytest.raises(ValueError, match="TY"):
        positioning_table(AS_OF, entries, {})


# ---- calendar_list

def test_calendar_list():
    events = [
        {"date": "2026-11-06", "event": "Payrolls (October)"},
        {"date": "2026-10-30", "event": "PCE (September)"},
        {"date": " 2026-11-04 ", "event": "FOMC decision"},
        {"date": "2026-11-06", "event": "Consumer sentiment"},
        {"date": "2026-12-01", "event": "ISM manufacturing"},
        {"date": "2026-11-30", "event": "Month end"},
    ]
    assert calendar_list(AS_OF, events, date(2026, 11, 30)) == (
        "Scheduled after 2026-10-30, through 2026-11-30:\n"
        "\n"
        "- 2026-11-04: FOMC decision\n"
        "- 2026-11-06: Payrolls (October)\n"
        "- 2026-11-06: Consumer sentiment\n"
        "- 2026-11-30: Month end\n")


def test_calendar_list_with_nothing_scheduled():
    events = [{"date": "2026-10-30", "event": "PCE (September)"}]
    assert calendar_list(date(2026, 10, 30), events, "20261130") == (
        "Scheduled after 2026-10-30, through 2026-11-30:\n\n- none listed\n")
    assert calendar_list(AS_OF, [], AS_OF) == (
        "Scheduled after 2026-10-30, through 2026-10-30:\n\n- none listed\n")


@pytest.mark.parametrize("bad", ["Nov 6", "2026-11-31", "", None, date(2026, 11, 6), 20261106])
def test_calendar_list_refuses_an_event_without_an_iso_date(bad):
    events = [{"date": "2026-11-04", "event": "FOMC decision"}, {"date": bad, "event": "Payrolls"}]
    with pytest.raises(ValueError, match="Payrolls"):
        calendar_list(AS_OF, events, "2026-11-30")


@pytest.mark.parametrize("through", ["next month", "2026-11-31", None, 20261130])
def test_calendar_list_refuses_a_bad_through(through):
    with pytest.raises(ValueError):
        calendar_list(AS_OF, [], through)


# ---- as_of

CALLS = [
    lambda as_of: trailing_change([], as_of, "1w", "bp"),
    lambda as_of: target_table(as_of, [], {}),
    lambda as_of: context_table(as_of, [], {}, set()),
    lambda as_of: positioning_table(as_of, [], {}),
    lambda as_of: calendar_list(as_of, [], "2026-11-30"),
]


@pytest.mark.parametrize("call", CALLS)
@pytest.mark.parametrize("as_of", ["2026/10/30", "30 Oct 2026", "", 20261030, None,
                                   ["2026-10-30"]])
def test_every_function_refuses_a_bad_as_of(call, as_of):
    with pytest.raises(ValueError):
        call(as_of)
