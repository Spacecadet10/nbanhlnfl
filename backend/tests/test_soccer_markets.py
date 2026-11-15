"""Soccer goal/corner market math (app/soccer_markets.py): settlement of half, whole and quarter lines, probabilities and
fair prices from a stored joint grid, double chance and draw no bet from a 1X2 forecast. Hand-made grids only."""
import math

import pytest

from app import soccer_markets as sm


def grid(cells: dict, n: int = 6) -> list:
    g = [[0.0] * n for _ in range(n)]
    for (h, a), p in cells.items():
        g[h][a] = p
    return g


@pytest.mark.parametrize("count,line,side,expected", [
    (3, 2.5, "over", "win"), (2, 2.5, "over", "loss"), (2, 2.5, "under", "win"),
    (2, 2.0, "over", "push"), (3, 2.0, "over", "win"), (1, 2.0, "under", "win"), (2, 2.0, "under", "push"),
    (2, 2.25, "over", "half_loss"), (3, 2.25, "over", "win"), (2, 2.25, "under", "half_win"), (1, 2.25, "under", "win"),
    (3, 2.75, "over", "half_win"), (2, 2.75, "over", "loss"), (4, 2.75, "over", "win"), (3, 2.75, "under", "half_loss"),
    (0, 0.5, "over", "loss"), (1, 0.5, "over", "win"),
])
def test_total_lines_settle_with_push_and_split_stakes(count, line, side, expected):
    assert sm.grade_total(count, line, side) == expected


@pytest.mark.parametrize("home,away,line,side,expected", [
    (5, 3, -1.5, "home", "win"), (5, 4, -1.5, "home", "loss"), (5, 4, -1.0, "home", "push"),
    (5, 4, -1.25, "home", "half_loss"), (6, 4, -1.25, "home", "win"), (5, 4, -0.75, "home", "half_win"),
    (5, 4, 1.0, "away", "push"), (5, 4, 1.25, "away", "half_win"), (5, 4, 0.75, "away", "half_loss"),
    (3, 3, 0.0, "home", "push"), (3, 3, -0.25, "home", "half_loss"), (3, 3, 0.25, "away", "half_win"),
])
def test_handicap_compares_the_side_plus_its_own_line(home, away, line, side, expected):
    assert sm.grade_handicap(home, away, line, side) == expected


def test_a_line_must_be_a_quarter_step():
    with pytest.raises(ValueError):
        sm.grade_total(2, 2.1, "over")
    with pytest.raises(ValueError):
        sm.grade_total(2, 2.5, "sideways")


@pytest.mark.parametrize("grade,decimal,factor", [
    ("win", 1.9, 1.9), ("half_win", 1.9, 1.45), ("push", 1.9, 1.0), ("void", 1.9, 1.0), ("half_loss", 1.9, 0.5),
    ("loss", 1.9, 0.0),
])
def test_return_factor_per_unit_stake(grade, decimal, factor):
    assert sm.return_factor(grade, decimal) == pytest.approx(factor)


def test_probabilities_from_a_grid_cover_every_result_and_sum_to_one():
    g = grid({(0, 0): 0.1, (1, 0): 0.2, (1, 1): 0.3, (2, 1): 0.25, (3, 2): 0.15})
    over2 = sm.probabilities(g, "goals_total", "over", 2.0)       # totals 0,1,2,3,5
    assert over2 == pytest.approx({"win": 0.4, "half_win": 0.0, "push": 0.3, "half_loss": 0.0, "loss": 0.3})
    over225 = sm.probabilities(g, "goals_total", "over", 2.25)
    assert over225["win"] == pytest.approx(0.4) and over225["half_loss"] == pytest.approx(0.3)
    assert over225["loss"] == pytest.approx(0.3)
    btts = sm.probabilities(g, "btts", "yes", None)
    assert btts["win"] == pytest.approx(0.7) and btts["loss"] == pytest.approx(0.3)
    home_over = sm.probabilities(g, "goals_home", "over", 1.5)
    assert home_over["win"] == pytest.approx(0.4)
    away_under = sm.probabilities(g, "goals_away", "under", 0.5)
    assert away_under["win"] == pytest.approx(0.3)
    hc = sm.probabilities(g, "corners_handicap", "home", -0.5)
    assert hc["win"] == pytest.approx(0.6)
    for p in (over2, over225, btts, home_over, away_under, hc):
        assert sum(p.values()) == pytest.approx(1.0)


def test_fair_price_and_expected_return_account_for_pushes_and_halves():
    probs = {"win": 0.4, "half_win": 0.0, "push": 0.3, "half_loss": 0.0, "loss": 0.3}
    d = sm.fair_decimal(probs)
    assert d == pytest.approx(1 + 0.3 / 0.4)
    assert sm.expected_return(probs, d) == pytest.approx(1.0)
    split = {"win": 0.4, "half_win": 0.0, "push": 0.0, "half_loss": 0.3, "loss": 0.3}
    assert sm.expected_return(split, sm.fair_decimal(split)) == pytest.approx(1.0)
    assert sm.fair_decimal({"win": 0.0, "half_win": 0.0, "push": 1.0, "half_loss": 0.0, "loss": 0.0}) is None


def test_fair_line_is_the_quarter_line_whose_two_sides_are_closest_to_even():
    g = grid({(1, 1): 0.5, (2, 1): 0.5})          # total 2 or 3 with equal chance: 2.5 is exactly even
    assert sm.fair_line(g, "goals_total") == 2.5
    g = grid({(1, 0): 0.25, (1, 1): 0.5, (2, 1): 0.25})   # total 1, 2, 3: 2.0 is even (push on 2)
    assert sm.fair_line(g, "goals_total") == 2.0


def test_double_chance_and_draw_no_bet_come_from_the_1x2_forecast_only():
    p = (0.5, 0.3, 0.2)
    assert sm.probabilities_1x2(p, "double_chance", "home_draw")["win"] == pytest.approx(0.8)
    assert sm.probabilities_1x2(p, "double_chance", "draw_away")["win"] == pytest.approx(0.5)
    assert sm.probabilities_1x2(p, "double_chance", "home_away")["win"] == pytest.approx(0.7)
    dnb = sm.probabilities_1x2(p, "draw_no_bet", "home")
    assert dnb == pytest.approx({"win": 0.5, "half_win": 0.0, "push": 0.3, "half_loss": 0.0, "loss": 0.2})
    assert sm.fair_decimal(dnb) == pytest.approx(1 + 0.2 / 0.5)
    assert sm.grade_1x2_market((1, 1), "draw_no_bet", "away") == "push"
    assert sm.grade_1x2_market((2, 1), "double_chance", "home_away") == "win"
    assert sm.grade_1x2_market((2, 1), "double_chance", "draw_away") == "loss"


def test_corner_markets_never_accept_a_1x2_forecast():
    with pytest.raises(ValueError):
        sm.probabilities_1x2((0.5, 0.3, 0.2), "corners_total", "over")
    with pytest.raises(ValueError):
        sm.probabilities((0.5, 0.3, 0.2), "corners_total", "over", 9.5)


def test_grade_market_reads_the_family_counts():
    assert sm.grade_market("corners_total", "over", 9.5, (6, 4)) == "win"
    assert sm.grade_market("corners_away", "under", 4.0, (6, 4)) == "push"
    assert sm.grade_market("goals_home", "over", 1.75, (2, 0)) == "half_win"
    assert sm.grade_market("btts", "no", None, (2, 0)) == "win"
    assert math.isclose(sum(sm.probabilities(grid({(5, 5): 1.0}, 26), "corners_total", "over", 9.5).values()), 1.0)
