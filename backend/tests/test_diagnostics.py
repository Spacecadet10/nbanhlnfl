"""Read-only diagnostics: regression/confusion metrics, permutation importance, learning curve, data checks, report."""
import math
import random
import sqlite3

import pytest

from app import diagnostics, metrics
from app.candidates import features, registry
from app.candidates.features import Row


def test_regression_errors_and_confusion_match_hand_computed_values():
    e = metrics.regression_errors([10, 20, 30, 40], [12, 18, 30, 50])
    assert e["n"] == 4 and e["mae"] == pytest.approx(3.5) and e["medae"] == pytest.approx(2.0)
    assert e["rmse"] == pytest.approx(math.sqrt((4 + 4 + 0 + 100) / 4)) and e["bias"] == pytest.approx(2.5)
    assert e["r2"] == pytest.approx(1 - 108 / sum((a - 27.5) ** 2 for a in (12, 18, 30, 50)))
    assert metrics.regression_errors([], []) is None
    c = metrics.confusion([0.7, 0.6, 0.4, 0.2, 0.5], [True, False, True, False, True])
    assert (c["home_picked_home_won"], c["home_picked_away_won"], c["away_picked_home_won"], c["away_picked_away_won"]) == (2, 1, 1, 1)
    assert c["precision"] == pytest.approx(2 / 3) and c["recall"] == pytest.approx(2 / 3) and c["f1"] == pytest.approx(2 / 3)
    zero = metrics.confusion([0.7, 0.3], [False, True])     # home picked once and lost; the home win was missed
    assert (zero["precision"], zero["recall"], zero["f1"]) == (0.0, 0.0, 0.0)
    assert metrics.confusion([0.3], [False])["f1"] is None  # no home pick and no home win: undefined


@pytest.fixture
def toy(monkeypatch):
    """Four seasons of games where `signal` decides the winner and `noise` is unrelated."""
    monkeypatch.setitem(features.FEATURE_SETS, "toy", ("signal", "noise"))
    spec = registry.Spec("logit_l2:toy", "nba", "logit_l2", "binary", "toy", "candidate", "test")
    rng = random.Random(1)
    rows = []
    for season in (2021, 2022, 2023, 2024):
        for i in range(300):
            day = f"{season - 1}-11-{1 + i % 28:02d}"
            s = rng.gauss(0, 1)
            won = rng.random() < 1 / (1 + math.exp(-2 * s))
            rows.append(Row(f"g{season}-{i}", season, f"{day}T23:00:00Z", f"{day}T05:00:00Z", {"signal": s, "noise": rng.gauss(0, 1)},
                            f"{day}T06:00:00Z" if season < 2024 else f"{season}-06-30T00:00:00Z",
                            {"home_won": won, "margin": 1 if won else -1, "total": 200, "home": 0, "away": 0, "regulation": None},
                            {"teams": ("h", "a")}))
    return spec, rows


def test_permutation_importance_ranks_the_informative_input_first_and_uses_only_earlier_seasons(toy):
    spec, rows = toy
    r = diagnostics.permutation_importance(spec, rows, 2024, repeats=3)
    assert r["training_games"] == 900 and r["scored_games"] == 300
    assert r["features"]["signal"]["log_loss"]["mean"] > 0.05
    assert abs(r["features"]["noise"]["log_loss"]["mean"]) < 0.01


def test_learning_curve_adds_one_earlier_season_per_point_with_paired_comparisons(toy):
    spec, rows = toy
    r = diagnostics.learning_curve(spec, rows, 2024)
    assert [p["seasons"] for p in r["points"]] == [[2023], [2022, 2023], [2021, 2022, 2023]]
    assert "change_vs_previous" not in r["points"][0]
    change = r["points"][1]["change_vs_previous"]["log_loss"]
    assert change["games"] == 300 and change["interval"][0] <= change["mean"] <= change["interval"][1]


def test_data_checks_find_duplicates_and_name_problems():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE games (game_id, season_type, tipoff_at, home_team_id, away_team_id, status, home_score, away_score)")
    c.execute("CREATE TABLE teams (team_id, name)")
    c.execute("CREATE TABLE goalie_snapshots (espn_player_id, player_name)")
    c.executemany("INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
        ("g1", "regular", "2024-01-10T00:00:00Z", "h", "a", "final", 100, 90),
        ("g2", "regular", "2024-01-09T23:30:00Z", "h", "a", "final", 95, 90),       # same Eastern date as g1
        ("g5", "regular", "2024-01-10T20:00:00Z", "h", "a", "postponed", None, None),  # a postponement is not a duplicate
        ("g3", "other", "2024-02-18T00:00:00Z", "x", "y", "final", 1, 0),
        ("g4", "other", "2024-02-18T01:00:00Z", "x", "y", "final", 1, 0)])          # All-Star style events: not flagged
    c.executemany("INSERT INTO teams VALUES (?, ?)", [("h", "MontrÃ©al"), ("a", "Boston")])
    c.executemany("INSERT INTO goalie_snapshots VALUES (?, ?)", [("1", "Jakub Dobeš"), ("1", "Jakub Dobes"), ("2", "Ana  Ruiz"),
                                                                 ("3", "jakub dobes")])
    r = diagnostics.data_checks(c)
    assert [d["game_ids"] for d in r["duplicate_games"]] == [["g2", "g1"]]
    assert {(n["id"], n["problem"]) for n in r["name_problems"]} == {("h", "encoding damage"), ("2", "extra spaces")}
    assert r["one_id_several_spellings"] == {"1": ["Jakub Dobes", "Jakub Dobeš"]}
    assert r["one_name_several_ids"] == {"jakub dobes": ["1", "3"]}


def test_html_report_renders_and_escapes_names(toy):
    spec, rows = toy
    games = [{"game_id": "g", "season": 2024, "tipoff_at": "2024-01-01T00:00:00Z", "teams": ("h", "a"),
              "total_error": e, "margin_error": -e} for e in (-12.0, 3.0, 25.0)]
    report = {"sport": "nba", "scope": "test", "permutation_importance": [diagnostics.permutation_importance(spec, rows, 2024, 1)],
              "learning_curve": [diagnostics.learning_curve(spec, rows, 2024)], "walk_forward": {"games": games},
              "outliers": {"beyond_3_sd": 0, "largest": games}}
    page = diagnostics.render_html(report, {"h": "<Home>", "a": "Away"})
    assert page.startswith("<!doctype html>") and "&lt;Home&gt;" in page and "<Home>" not in page and page.count("<svg") == 6


def test_goalie_imputer_does_not_recover_the_default_through_goalie_diff():
    from app.diagnostics import _masked
    names = ["goalie_home", "goalie_away", "goalie_diff", "goalie_exp_home", "goalie_exp_away"]
    known = Row("k", 2020, "", "", {"goalie_home": 0.1, "goalie_away": -0.2, "goalie_diff": 0.3, "goalie_exp_home": 5.0,
                                    "goalie_exp_away": 4.0})
    debut = Row("d", 2020, "", "", {"goalie_home": 0.0, "goalie_away": -0.2, "goalie_diff": 0.2, "goalie_exp_home": 0.0,
                                    "goalie_exp_away": 4.0})
    X = _masked([known, debut], names)
    assert not any(math.isnan(v) for v in X[0])
    assert math.isnan(X[1][0]) and math.isnan(X[1][2]) and X[1][1] == -0.2
