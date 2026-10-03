"""features-v1 (docs/MODEL_SELECTION.md §19): player-quality-weighted absences and NHL special-teams inputs."""
from types import SimpleNamespace

import pytest

from app import availability_quality as aq
from app.candidates import features, features_extra as fx, registry


def test_game_score_is_hollinger_and_tolerates_missing_fields():
    r = {"points": "20", "field_goals_made": "8", "field_goals_attempted": "15", "free_throws_attempted": "4",
         "free_throws_made": "3", "offensive_rebounds": "2", "defensive_rebounds": "5", "steals": "1", "assists": "6",
         "blocks": "1", "fouls": "2", "turnovers": "3"}
    want = 20 + 0.4 * 8 - 0.7 * 15 - 0.4 * 1 + 0.7 * 2 + 0.3 * 5 + 1 + 0.7 * 6 + 0.7 * 1 - 0.4 * 2 - 3
    assert aq.game_score(r) == pytest.approx(want)
    assert aq.game_score({"points": "NA", "assists": ""}) == 0.0


def box_row(pid, day, minutes, points, season_day=None):
    return {"athlete_id": pid, "game_date": day, "minutes": str(minutes), "points": str(points), "did_not_play": "false"}


def test_quality_uses_only_games_before_the_report_date_and_centres_on_the_previous_season():
    rows = {2022: [box_row("p", "2021-12-01", 30, 30), box_row("q", "2021-12-01", 30, 9)],
            2023: [box_row("p", "2022-11-01", 30, 60), box_row("p", "2022-11-02", 30, 60), box_row("p", "2022-11-03", 30, 60),
                   box_row("p", "2022-11-04", 30, 60), box_row("p", "2022-11-05", 30, 60),
                   box_row("p", "2022-11-06", 10, 300)]}            # the report day's own game must never count
    h = aq.QualityHistory([2022, 2023], loader=lambda s: rows[s])
    league_2022 = (30 + 9) / 60
    q, basis = h.quality("p", "2022-11-06", 2023)
    assert basis == "ok" and q == pytest.approx(60 / 30 - league_2022)
    assert h.quality("nobody", "2022-11-06", 2023) == (0.0, "no_history")
    assert h.quality("p", "2021-12-02", 2022)[1] == "no_reference_season"


def test_special_inputs_count_attempts_power_play_goals_and_short_handed_goals_against():
    s = fx.SpecialInputs()
    shot = lambda side, event, sit="1551", goal=False, en=False: SimpleNamespace(side=side, event=event, situation=sit,
                                                                                goal=goal, empty_net=en)
    g = SimpleNamespace(home_team_id="H", away_team_id="A")
    shots = [shot("home", "shot-on-goal"), shot("home", "missed-shot"), shot("home", "blocked-shot"),
             shot("away", "shot-on-goal"),
             shot("home", "goal", "1451", goal=True),                     # home 5 v away 4: home power-play goal
             shot("away", "goal", "0651", goal=True, en=True)]            # empty-net goal: not a special-teams goal
    s.update_box(SimpleNamespace(game=g, shots=shots))
    f = s.features(SimpleNamespace(home_team_id="H", away_team_id="A"))
    assert f["shot_share_diff"] == pytest.approx(4 / 6 - 2 / 6)
    assert f["pp_goals_diff"] == pytest.approx(1.0) and f["sh_goals_against_diff"] == pytest.approx(-1.0)
    assert s.features(SimpleNamespace(home_team_id="X", away_team_id="Y")) == \
        {"shot_share_diff": 0.0, "pp_goals_diff": 0.0, "sh_goals_against_diff": 0.0}


def test_new_feature_set_leaves_existing_sets_and_candidate_identities_unchanged():
    assert features.FEATURE_SETS["nhl_special"] == features.NHL_BASE + fx.SPECIAL
    assert features.FEATURE_SETS["nhl_base"] == features.NHL_BASE
    assert all(s.features != "nhl_special" for s in registry.SPECS)      # nothing new is recorded live from this study
