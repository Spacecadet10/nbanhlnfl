"""NCAAB model and evaluation (docs/NCAAB_PLAN.md "Evaluation plan"): point-in-time inputs, venue, Division I rule,
early-season prior, idempotent live prediction, the sealed decision and the contract `app/ncaab_app.py` relies on.

Every store here is synthetic (ncaab_support.make_store); nothing reads `data/` or the scratch stores."""
import json
import math
import random
from datetime import datetime, timedelta, timezone

import pytest

import ncaab_support as sup
from app import backtest, ncaab, ncaab_app
from app.models import college
from app.models.base import PregameGame, Result

RAW = sup.RAW


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _plus(ts: str, **kw) -> str:
    return _iso(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) + timedelta(**kw))


def add_team(conn, tid: str) -> None:
    conn.execute("INSERT OR IGNORE INTO teams (team_id, abbr, name, first_seen_at) VALUES (?, ?, ?, ?)",
                 (f"ncaab:{tid}", f"T{tid}", f"Team {tid}", RAW))


def add(conn, event, tipoff, home, away, hs=None, aws=None, *, status="final", neutral=0, season=2027, d1=True,
        available=None, box=((60, 20), (60, 20)), season_type=None):
    """A game with its NCAAB context row (both_d1) and, when final, the scoreboard FGA/FTA (team_game_stats)."""
    for t in (home, away):
        add_team(conn, t)
    final = status == "final"
    sup.add_game(conn, event, tipoff, home, away, status, hs, aws, season_type or ("regular" if d1 else "other"), neutral,
                 season, available or (_plus(tipoff, hours=4) if final else None))
    gid = f"ncaab:{event}"
    conn.execute("INSERT INTO ncaab_game_context (game_id, season_type_id, tournament_note, conference_competition, "
                 "home_conference_id, away_conference_id, both_d1, ot_periods, source_status, updated_at, last_raw_id) "
                 "VALUES (?, 2, NULL, 0, '1', ?, ?, ?, ?, ?, 1)",
                 (gid, "2" if d1 else None, int(d1), 0 if final else None, status.upper(), RAW))
    if final and box:
        for is_home, team, pts, (fga, fta) in ((1, home, hs, box[0]), (0, away, aws, box[1])):
            conn.execute("INSERT INTO team_game_stats (game_id, team_id, is_home, points, fga, fta, last_raw_id) "
                         "VALUES (?, ?, ?, ?, ?, ?, 1)", (gid, f"ncaab:{team}", is_home, pts, fga, fta))
    conn.commit()
    return gid


CFG = {"college_elo_config": {"k": 30.0, "home_advantage": 75.0, "carry": 0.8, "mov": True},
       "college_eff_config": {"eta": 0.04, "carry": 0.7, "w0": 8}}
SIGMAS = {"elo_margin": 11.0, "eff_margin": 10.5, "eff_total": 17.0, "eff_rho": 0.05, "points_total": 18.0,
          "points_home": 11.0, "points_away": 11.0}
SHIPPED = {"spread": "college_eff", "total": "college_eff", "team_total_home": "running_points",
           "team_total_away": "running_points"}


def sealed_decision(conn, path, **override):
    rows = ncaab.load_rows(conn, cutoff="9999-12-31T00:00:00Z", seasons=ncaab_app.PERIODS["validation"])
    d = {"sport": "ncaab", "winner_model": "college_elo", "winner_calibration": None, **CFG, "sigmas": SIGMAS,
         "markets": {m: {"model": v} for m, v in SHIPPED.items()},
         "validation_dataset_version": ncaab.dataset_version(rows), "validation_seasons": list(ncaab_app.PERIODS["validation"]),
         "selected_on": "validation", "data_cutoff": "2026-11-17T12:00:00Z", "feature_version": "x",
         "training_command": "synthetic", "frozen_at": RAW, "validation_report": "synthetic", **override}
    d = college.seal(d)
    d["model_version"] = college.winner_version(d)
    d = college.seal({k: v for k, v in d.items() if k != "sha256"})
    path.write_text(json.dumps(d, indent=2))
    return d


@pytest.fixture
def store(tmp_path, monkeypatch):
    conn = sup.make_store(tmp_path, monkeypatch)
    college.clear_cache()
    path = tmp_path / "ncaab_frozen_decision.json"
    monkeypatch.setattr(college, "DECISION_PATH", path)
    # two weeks of 2026-27 results among teams 1-4, then tonight's game (1 v 2)
    add(conn, "100", "2026-11-04T00:00:00Z", "1", "3", 80, 70)
    add(conn, "101", "2026-11-05T00:00:00Z", "2", "4", 66, 71)
    add(conn, "102", "2026-11-08T00:00:00Z", "3", "2", 75, 72)
    add(conn, "103", "2026-11-10T00:00:00Z", "4", "1", 60, 77)
    add(conn, "104", "2026-11-12T00:00:00Z", "2", "1", 70, 68, neutral=1)
    add(conn, "401", sup.GAME_START, "1", "2", status="scheduled")
    sealed_decision(conn, path)
    return conn


CUTOFF = "2026-11-17T21:30:00Z"     # two hours before tonight's game


def _p(conn, cutoff=CUTOFF, game=sup.GAME, **kw):
    college.clear_cache()
    return college.predict(conn, game, cutoff=cutoff, **kw)


# --- no look-ahead ------------------------------------------------------------------------------------------------
def test_a_result_available_only_after_the_cutoff_never_moves_the_forecast(store):
    before = _p(store)
    add(store, "200", "2026-11-17T01:00:00Z", "1", "4", 95, 50, available="2026-11-17T22:00:00Z")  # known after cutoff
    assert _p(store) == before
    assert _p(store, cutoff="2026-11-17T22:10:00Z")["home_win_prob"] != before["home_win_prob"]


def test_same_day_unfinished_and_future_rows_are_ignored(store):
    before = _p(store)
    add(store, "201", "2026-11-17T18:00:00Z", "1", "3", 40, 30, status="in_progress")      # same day, not final
    add(store, "202", "2026-11-17T20:00:00Z", "2", "4", status="scheduled")
    add(store, "203", "2026-11-20T00:00:00Z", "1", "4", 99, 40, available="2026-11-20T04:00:00Z")  # a future final
    assert _p(store) == before


def test_the_current_games_own_label_is_never_read(store, tmp_path):
    # tonight's game finished in a replay: its score is in the store but became available after the cutoff
    store.execute("UPDATE games SET status='final', home_score=100, away_score=40, result_available_at=?, "
                  "result_first_seen_at=? WHERE game_id=?", ("2026-11-18T03:30:00Z", "2026-11-18T03:30:00Z", sup.GAME))
    a = _p(store)
    store.execute("UPDATE games SET home_score=40, away_score=100 WHERE game_id=?", (sup.GAME,))
    assert _p(store) == a
    with pytest.raises(college.NotForecastable):          # a cutoff after the start is not a pregame forecast
        _p(store, cutoff="2026-11-17T23:31:00Z")


def test_the_walk_forward_rows_respect_result_availability(store):
    rows, box = college.rows_at(store, CUTOFF)
    assert all(r.result_available_at <= CUTOFF for r in rows)
    assert sup.GAME not in {r.game.game_id for r in rows}
    assert set(box) <= {r.game.game_id for r in rows}


def test_live_prediction_equals_the_walk_forward_backtest_at_the_same_cutoff(store):
    """The eval walk (backtest.run) and the live state give the same probability for the same information time."""
    cutoff = backtest.cutoff_for(sup.GAME_START)
    live = _p(store, cutoff=cutoff)["home_win_prob"]
    rows, _ = college.rows_at(store, cutoff)
    d = college.load_decision()
    elo = college.CollegeElo(college.CollegeEloConfig(**d["college_elo_config"]))
    target = backtest.Row(PregameGame(sup.GAME, 2027, "regular", sup.GAME_START, "ncaab:1", "ncaab:2", False), cutoff, 0, 1,
                          "2026-11-18T03:30:00Z")
    out = backtest.run(elo, rows + [target])
    assert out[-1]["game_id"] == sup.GAME and abs(out[-1]["p_home"] - live) < 1e-12


# --- venue and Division I -------------------------------------------------------------------------------------------
def test_a_neutral_site_has_no_home_advantage():
    elo = college.CollegeElo(college.CollegeEloConfig(k=30, home_advantage=100, carry=1.0, mov=False))
    eff = college.CollegeEff(college.CollegeEffConfig(eta=0.04, carry=0.7, w0=8))
    eff.ha, eff.ha_n = 4.0, 100
    g = PregameGame("ncaab:1", 2027, "regular", "2026-11-17T23:30:00Z", "ncaab:1", "ncaab:2", True)
    home = PregameGame("ncaab:1", 2027, "regular", "2026-11-17T23:30:00Z", "ncaab:1", "ncaab:2", False)
    assert elo.predict(g) == 0.5 and elo.margin(g) == 0.0
    assert elo.predict(home) > 0.5 and elo.margin(home) == pytest.approx(100 / 25)
    mh, ma = eff.expected(g)
    assert mh == pytest.approx(ma)
    hh, ha = eff.expected(home)
    assert hh - ha == pytest.approx(4.0)


def test_a_neutral_result_does_not_teach_home_advantage():
    eff = college.CollegeEff(college.CollegeEffConfig(eta=0.04, carry=0.7, w0=8))
    g = PregameGame("ncaab:9", 2027, "regular", "2026-11-17T23:30:00Z", "ncaab:1", "ncaab:2", True)
    eff.update(Result(g, 90, 60))
    assert eff.ha_n == 0 and eff.ha == 0.0


def test_non_division_i_games_never_rate_and_are_never_forecast(store):
    before = _p(store)
    gid = add(store, "300", "2026-11-15T00:00:00Z", "1", "9", 120, 30, d1=False)
    assert _p(store) == before
    assert gid not in {r.game.game_id for r in college.rows_at(store, CUTOFF)[0]}
    later = add(store, "301", "2026-11-18T00:00:00Z", "1", "9", status="scheduled", d1=False)
    with pytest.raises(college.NotForecastable):
        _p(store, game=later)


# --- early-season prior --------------------------------------------------------------------------------------------
def test_early_season_ratings_blend_the_carried_prior_with_games_played():
    cfg = college.CollegeEffConfig(eta=0.04, carry=0.5, w0=8)
    eff = college.CollegeEff(cfg)
    eff.season = 2026
    eff.prior["ncaab:1"] = [0.0, 0.0, 0.0]
    eff.cur["ncaab:1"] = [10.0, -4.0, 2.0]
    eff.n["ncaab:1"] = 30
    blended_end = [(8 * 0 + 30 * x) / 38 for x in (10.0, -4.0, 2.0)]
    assert eff.rating("ncaab:1", 2026) == pytest.approx(blended_end)
    assert eff.rating("ncaab:1", 2027) == pytest.approx([0.5 * x for x in blended_end])   # carried, no game yet
    g = PregameGame("ncaab:8", 2027, "regular", "2026-11-17T23:30:00Z", "ncaab:1", "ncaab:2", True)
    eff.update(Result(g, 80, 60))
    prior = [0.5 * x for x in blended_end]
    cur = eff.cur["ncaab:1"]
    assert eff.n["ncaab:1"] == 1 and eff.prior["ncaab:1"] == pytest.approx(prior)
    assert eff.rating("ncaab:1", 2027) == pytest.approx([(8 * p + 1 * c) / 9 for p, c in zip(prior, cur)])


def test_a_larger_prior_weight_moves_less_on_the_first_games():
    moves = []
    for w0 in (4, 12):
        eff = college.CollegeEff(college.CollegeEffConfig(eta=0.04, carry=0.7, w0=w0))
        g = PregameGame("ncaab:8", 2027, "regular", "2026-11-17T23:30:00Z", "ncaab:1", "ncaab:2", True)
        eff.update(Result(g, 90, 60))
        moves.append(abs(eff.rating("ncaab:1", 2027)[0]))
    assert moves[0] > moves[1] > 0


def test_a_game_without_fga_fta_updates_from_points_with_tempo_fixed():
    eff = college.CollegeEff(college.CollegeEffConfig(eta=0.04, carry=0.7, w0=8))
    g = PregameGame("ncaab:8", 2027, "regular", "2026-11-17T23:30:00Z", "ncaab:1", "ncaab:2", True)
    eff.update(Result(g, 80, 60))       # no box for this game
    assert eff.rating("ncaab:1", 2027)[2] == 0.0 and eff.rating("ncaab:1", 2027)[0] > 0


# --- live prediction ------------------------------------------------------------------------------------------------
def test_predict_is_idempotent_and_the_slate_state_is_computed_once(store, monkeypatch):
    calls = []
    real = college.rows_at
    monkeypatch.setattr(college, "rows_at", lambda conn, cutoff: calls.append(cutoff) or real(conn, cutoff))
    college.clear_cache()
    first = college.predict(store, sup.GAME, cutoff=CUTOFF)
    other = add(store, "402", "2026-11-18T01:00:00Z", "3", "4", status="scheduled")
    college.clear_cache()
    calls.clear()
    a = college.predict(store, sup.GAME, cutoff=CUTOFF)
    b = college.predict(store, other, cutoff=CUTOFF)
    c = college.predict(store, sup.GAME, cutoff=CUTOFF)
    assert a == c == first and b["home_win_prob"] != a["home_win_prob"]
    assert calls == [CUTOFF]                                   # one walk for the whole slate


def test_a_new_result_in_the_store_refreshes_the_cached_state(store):
    a = college.predict(store, sup.GAME, cutoff="2026-11-17T22:10:00Z")
    add(store, "204", "2026-11-17T01:00:00Z", "1", "4", 95, 50, available="2026-11-17T22:00:00Z")
    b = college.predict(store, sup.GAME, cutoff="2026-11-17T22:10:00Z")
    assert a != b


def test_predict_contract_used_by_the_adapter(store):
    out = _p(store)
    assert out["model_version"].startswith("ncaab-") and out["model_version"] == college.load_decision()["model_version"]
    assert isinstance(out["home_win_prob"], float) and 0 < out["home_win_prob"] < 1
    assert set(out["markets"]) == {"spread", "total", "team_total_home", "team_total_away"}
    for market, spec in out["markets"].items():
        assert spec["model_version"].startswith("ncaab-")
        pa, pm, pb = spec["probs"]
        assert abs(pa + pm + pb - 1) < 1e-9 and pa > 0 and pb > 0 and pm == 0.0      # half-point own line: no push
        assert spec["line"] % 1 == 0.5                            # own lines are half points: no push
    # the spread line is the home handicap: a home favourite gets a negative line and P(cover) close to a half
    s = out["markets"]["spread"]
    assert (s["line"] < 0) == (s["mean"] > 0) and abs(s["probs"][0] - 0.5) < 0.1
    priced = _p(store, lines={"total": 140.0, "spread": -3.0})
    assert priced["markets"]["total"]["line"] == 140.0 and priced["markets"]["total"]["probs"][1] > 0   # a whole line can push
    assert priced["markets"]["spread"]["line"] == -3.0
    assert priced["markets"]["team_total_home"] == out["markets"]["team_total_home"]


def test_the_real_module_through_the_app_adapter(store, monkeypatch):
    monkeypatch.setattr(ncaab_app, "DECISION_PATH", college.DECISION_PATH)
    model = ncaab_app.CollegeModel(store)
    assert model.version == college.load_decision()["model_version"]
    model.as_of = CUTOFF
    game = PregameGame(sup.GAME, 2027, "regular", sup.GAME_START, "ncaab:1", "ncaab:2", False)
    p = model.predict(game)
    dist = model.distribution(game)
    assert dist.winner() == (p, 0.0, 1 - p)
    own = dist.model_line("total")
    assert abs(sum(dist.market("total", own)) - 1) < 1e-9
    pa, pm, pb = dist.market("total", own + 3.0)                # a bookmaker's line is priced, not refused
    assert pa < dist.market("total", own)[0]
    line, quote = ncaab_app.market_line(model, dist, {"line": -2.5}, "spread")
    assert line == -2.5 and quote == {"line": -2.5}


def test_markets_are_coherent_with_the_score_distribution():
    # spread at 0 equals the winner probability; over + under (+ push) = 1; moving the line moves the probability
    p0 = college.market_probs("spread", 0.0, 4.0, 10.0)
    assert p0[0] == pytest.approx(0.5 * (1 + math.erf(4.0 / 10.0 / math.sqrt(2)))) and p0[1] == 0.0
    a = college.market_probs("total", 140.5, 141.0, 17.0)
    b = college.market_probs("total", 145.5, 141.0, 17.0)
    assert a[0] > 0.5 > b[0] and sum(a) == pytest.approx(1)
    push = college.market_probs("team_total_home", 70.0, 70.0, 10.0)
    assert push[1] == pytest.approx(math.erf(0.05 / math.sqrt(2)), rel=1e-6) and push[0] == pytest.approx(push[2])
    assert college.own_line("spread", 4.2) == -4.5 and college.own_line("total", 141.0) == 141.5


# --- sealed decision ------------------------------------------------------------------------------------------------
def test_a_modified_decision_file_is_refused(store):
    path = college.DECISION_PATH
    d = json.loads(path.read_text())
    d["college_elo_config"]["k"] = 50.0
    path.write_text(json.dumps(d))
    college.clear_cache()
    with pytest.raises(college.FrozenDecisionError):
        college.load_decision()
    with pytest.raises(college.FrozenDecisionError):
        college.predict(store, sup.GAME, cutoff=CUTOFF)


def test_changed_validation_data_is_refused(store, tmp_path):
    sealed_decision(store, college.DECISION_PATH, validation_dataset_version="0" * 16)
    with pytest.raises(college.FrozenDecisionError):
        _p(store)


def test_a_game_with_a_missing_fga_or_fta_has_no_box(store):
    gid = add(store, "500", "2026-11-14T00:00:00Z", "3", "4", 70, 65, box=((60, None), (58, 20)))
    rows, box = college.rows_at(store, CUTOFF)
    assert gid in {r.game.game_id for r in rows} and gid not in box
    assert "ncaab:100" in box


def test_the_committed_decision_file_passes_its_self_check():
    path = college.__dict__.get("COMMITTED_DECISION_PATH")
    assert path is not None
    if not path.exists():
        pytest.skip("no frozen decision committed yet")
    d = college.load_decision(path)
    assert d["selected_on"] == "validation" and d["model_version"].startswith("ncaab-")
    assert set(d["markets"]) == {"spread", "total", "team_total_home", "team_total_away"}
    for k in ("validation_dataset_version", "data_cutoff", "feature_version", "training_command", "frozen_at",
              "validation_report"):
        assert d[k]
