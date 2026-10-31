import math

import pytest

from tools import finance as f

NON_NUMBERS = [None, True, False, "4.2", math.nan, math.inf, -math.inf, [4.2], object()]


def test_level_change_bp():
    assert f.level_change(4.21, 4.33, "bp") == pytest.approx(12)
    assert f.level_change(4.33, 4.21, "bp") == pytest.approx(-12)
    assert f.level_change(-0.25, 0.10, "bp") == pytest.approx(35)
    assert f.level_change(0.10, -0.25, "bp") == pytest.approx(-35)
    assert f.level_change(0.0, 0.5, "bp") == pytest.approx(50)
    assert f.level_change(0, 0, "bp") == 0
    assert f.level_change(2, 3, "bp") == 100


def test_level_change_return():
    assert f.level_change(100, 110, "return") == pytest.approx(0.1)
    assert f.level_change(104.5, 99.275, "return") == pytest.approx(-0.05)
    assert f.level_change(100, 0, "return") == -1.0
    assert f.level_change(50, 50, "return") == 0.0
    assert f.level_change(0, 10, "return") is None
    assert f.level_change(0.0, 10, "return") is None
    assert f.level_change(-5, 10, "return") is None


def test_level_change_points():
    assert f.level_change(15.5, 18.0, "points") == pytest.approx(2.5)
    assert f.level_change(20, 18, "points") == -2
    assert f.level_change(-1.5, 1.0, "points") == pytest.approx(2.5)
    assert f.level_change(0, 0, "points") == 0


@pytest.mark.parametrize("kind", ["bp", "return", "points"])
@pytest.mark.parametrize("bad", NON_NUMBERS)
def test_level_change_non_numbers_give_none(kind, bad):
    assert f.level_change(bad, 4.0, kind) is None
    assert f.level_change(4.0, bad, kind) is None
    assert f.level_change(bad, bad, kind) is None


def test_level_change_overflow_gives_none():
    cases = [
        (-1e308, 1e308, "points"),
        (1e308, -1e308, "points"),
        (-1e308, 1e308, "bp"),
        (0.0, 1e307, "bp"),
        (0.0, -1e307, "bp"),
        (1e-308, 1e308, "return"),
        (5e-324, 1.0, "return"),
        (1, 10**400, "return"),
        (1.0, 10**400, "return"),
        (10**400, 1.5, "points"),
        (0.5, 10**400, "bp"),
    ]
    for start, end, kind in cases:
        assert f.level_change(start, end, kind) is None, (kind, type(start), type(end))


def test_level_change_large_values_that_do_not_overflow():
    assert f.level_change(10**400, 10**400 + 1, "points") == 1
    assert f.level_change(10**400, 10**400 + 1, "bp") == 100
    assert f.level_change(10**400, 2 * 10**400, "return") == 1.0
    assert f.level_change(1e308, 1e308, "bp") == 0.0
    assert f.level_change(1e308, -1e308, "return") == -2.0


@pytest.mark.parametrize(
    "kind", ["BP", "bps", "Return", "returns", "pct", "point", " points", "", None, 1, ["bp"]]
)
def test_level_change_rejects_unknown_kind(kind):
    with pytest.raises(ValueError):
        f.level_change(4.0, 4.1, kind)
    with pytest.raises(ValueError):
        f.level_change(None, "x", kind)


def test_direction_default_band():
    assert f.direction(0.1) == "up"
    assert f.direction(-0.1) == "down"
    assert f.direction(0) == "flat"
    assert f.direction(0.0) == "flat"
    assert f.direction(-0.0) == "flat"
    assert f.direction(5e-324) == "up"
    assert f.direction(-5e-324) == "down"
    assert f.direction(10**400) == "up"
    assert f.direction(-(10**400)) == "down"


def test_direction_band_edges_are_flat():
    assert f.direction(5, 5) == "flat"
    assert f.direction(-5, 5) == "flat"
    assert f.direction(5.0, dead_band=5) == "flat"
    assert f.direction(3, 5) == "flat"
    assert f.direction(-3, 5) == "flat"
    assert f.direction(5.000001, 5) == "up"
    assert f.direction(-5.000001, 5) == "down"
    assert f.direction(0.5, 0.25) == "up"
    assert f.direction(0, 0) == "flat"
    assert f.direction(1, -0.0) == "up"
    assert f.direction(10**400, 10**400) == "flat"


def test_direction_scores_level_changes():
    change = f.level_change(4.21, 4.33, "bp")
    assert f.direction(change) == "up"
    assert f.direction(change, 5) == "up"
    assert f.direction(change, 15) == "flat"
    assert f.direction(f.level_change(4.33, 4.21, "bp"), 5) == "down"
    assert f.direction(f.level_change(0, 10, "return")) is None


@pytest.mark.parametrize("change", NON_NUMBERS)
def test_direction_non_number_gives_none(change):
    assert f.direction(change) is None
    assert f.direction(change, 2) is None


@pytest.mark.parametrize(
    "band", [-0.1, -1, None, True, False, "0", math.nan, math.inf, -math.inf, [0]]
)
def test_direction_rejects_bad_band(band):
    with pytest.raises(ValueError):
        f.direction(1.0, band)
    with pytest.raises(ValueError):
        f.direction(None, band)
    with pytest.raises(ValueError):
        f.direction(0, band)


def test_direction_rejects_huge_negative_band():
    with pytest.raises(ValueError):
        f.direction(1.0, -(10**400))
