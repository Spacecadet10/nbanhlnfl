"""features-v1 (docs/MODEL_SELECTION.md §19): player-quality-weighted absences and NHL special-teams inputs."""
from types import SimpleNamespace

import pytest

from app import availability_quality as aq
from app.candidates import features, features_extra as fx, registry
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)


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


def test_prop_snapshots_are_raw_bounded_and_never_fail_the_caller(nhl_store):
    from datetime import datetime, timezone
    from app import props_collect as pc
    from test_live_markets import add_upcoming, quote
    conn = nhl_store
    add_upcoming(conn, start="2026-10-12T19:00:00", at="2026-10-12T20:30:00Z")
    quote(conn, "2026-10-12T21:05:00Z", date="2026-10-12T23:00Z")
    calls = []

    def fake(url):
        calls.append(url)
        return b'{"items": []}'
    boom = pc.collect(conn, "nhl", now=datetime(2026, 10, 12, 21, 5, tzinfo=timezone.utc),
                      fetch=lambda u: (_ for _ in ()).throw(RuntimeError("down")))
    assert boom["status"] == "partial" and boom["failed"] and boom["stored"] == 0      # a failure is retried later
    now = datetime(2026, 10, 12, 21, 30, tzinfo=timezone.utc)
    out = pc.collect(conn, "nhl", now=now, fetch=fake)
    assert out["targets"] == 1 and out["stored"] == 1 and "propBets" in calls[0]
    again = pc.collect(conn, "nhl", now=now, fetch=fake)
    assert again["stored"] == 0 and again["recent"] == 1 and len(calls) == 1           # once per game per 55 min
    later = datetime(2026, 10, 12, 22, 30, tzinfo=timezone.utc)
    assert pc.collect(conn, "nhl", now=later, fetch=lambda u: None)["not_listed"] == 1  # 404: counted, marker stored
    assert pc.collect(conn, "nhl", now=later, fetch=fake)["recent"] == 1              # the marker stops a re-request
    stamps = [r[0] for r in conn.execute("SELECT f.retrieved_at FROM raw_fetches f JOIN raw_responses r USING (raw_id) "
                                         "WHERE r.source='espn_props' ORDER BY f.fetch_id")]
    assert stamps == ["2026-10-12T21:30:00Z", "2026-10-12T22:30:00Z"]
    n = conn.execute("SELECT count(*) FROM raw_responses WHERE source='espn_props'").fetchone()[0]
    assert n == 2                                # the snapshot and one "not listed" marker
    far = pc.collect(conn, "nhl", now=datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc), fetch=fake)
    assert far["targets"] == 0                                                          # start more than two hours away

