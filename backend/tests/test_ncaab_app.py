"""NCAAB through the shared production paths: engine, two-hour forecasts, market forecasts and the canonical record
(synthetic scratch store and a stand-in model; no network, no live data)."""
import sys

import pytest

from app import jobs, live, near_tipoff, ncaab_app, sports
from ncaab_support import GAME, GAME_START, NOW, add_game, add_quote, install_college, make_store, refreshed, seed


@pytest.fixture
def store(tmp_path, monkeypatch):
    conn = make_store(tmp_path, monkeypatch)
    seed(conn)
    yield conn
    conn.close()


@pytest.fixture
def college(monkeypatch):
    return install_college(monkeypatch)


def test_ncaab_is_a_served_sport_with_its_own_engine_and_markets(store):
    assert "ncaab" in sports.SPORT_IDS and sports.get("ncaab").id_prefix == "ncaab:"
    eng = live.engine(store)
    assert eng.sport == "ncaab" and eng.eligible_types == ("regular", "postseason")
    assert [r.game.game_id for r in eng.load_rows(store)] == ["ncaab:300", "ncaab:301"]
    from app import markets
    assert {m: markets.spec("ncaab", m).line_step for m in sports.NCAAB.markets} == {
        "winner": None, "spread": 0.5, "total": 0.5, "team_total_home": 0.5, "team_total_away": 0.5}


def test_two_hour_forecast_is_stored_with_the_cycles_information_time(store, college):
    out = near_tipoff.predict_now(store, now=NOW)
    assert out["created"] == 1 and out["refused"] == []             # the non-D-I game ('other') is never forecast
    f = out["forecasts"][0]
    assert f["game_id"] == GAME and f["minutes_before_tipoff"] == 60.0 and 0 < f["home_win_prob"] < 1
    (row,) = store.execute("SELECT * FROM predictions WHERE model_version LIKE '%policy=near_tipoff'").fetchall()
    assert (row["cutoff_at"], row["generated_at"]) == (GAME_START, NOW)
    assert row["feature_version"] == "ncaab-pregame-ids-v1+tipoff-cutoff+two-hour-v1"
    assert row["model_version"].startswith("ncaab-") and row["model_version"].endswith("-window=2h-policy=near_tipoff")
    assert out["limitation"] == ncaab_app.LIMITATION
    assert college.calls[0][:2] == (GAME, NOW)                        # the model read the store at the cycle's clock


def test_a_result_available_after_the_cycle_never_moves_the_forecast(store, college):
    add_game(store, "500", "2026-11-17T18:00:00Z", "1", "4", "final", 90, 60, available="2026-11-17T22:45:00Z")
    before = college.predict(store, GAME, cutoff=NOW)["home_win_prob"]
    assert near_tipoff.predict_now(store, now=NOW, write=False)["forecasts"][0]["home_win_prob"] == before
    assert college.predict(store, GAME, cutoff="2026-11-17T22:45:00Z")["home_win_prob"] > before


def test_stale_results_and_unfinished_earlier_games_refuse_the_forecast(store, college):
    store.execute("DELETE FROM job_runs")
    store.commit()
    refreshed(store, "2026-11-17T21:29:00Z")
    out = near_tipoff.predict_now(store, now=NOW)
    assert out["forecasts"] == [] and out["refused"][0]["reason"] == "stale_results"
    refreshed(store)
    add_game(store, "600", "2026-11-17T20:00:00Z", "1", "4")             # team 1 played earlier today: no final result yet
    refused = {r["game_id"]: r["reason"] for r in near_tipoff.predict_now(store, now=NOW)["refused"]}
    assert refused[GAME] == "missing_result"


def test_every_market_is_forecast_at_the_two_hour_moment(store, college):
    out = jobs.predict_now(store, now=NOW)
    assert out["status"] == "success", out
    rows = {r["market"]: r for r in store.execute("SELECT * FROM market_forecasts WHERE policy='near_tipoff'")}
    assert set(rows) == {"spread", "total", "team_total_home", "team_total_away"}
    assert {r["line_source"] for r in rows.values()} == {"model"} and rows["spread"]["line"] == -3.5
    assert all(r["sport"] == "ncaab" and r["model_version"].endswith("-window=2h-policy=near_tipoff") for r in rows.values())
    canon = {r["market"]: r for r in store.execute("SELECT * FROM game_forecasts WHERE game_id=?", (GAME,))}
    assert set(canon) == set(sports.NCAAB.markets) and {r["status"] for r in canon.values()} == {"forecast"}
    assert canon["winner"]["source"] == "primary_recorded"


def test_a_bookmaker_line_is_used_only_when_the_model_can_price_it(store, college):
    add_quote(store, GAME, "total", 142.5, -110, -110, "2026-11-17T22:00:00Z")
    jobs.predict_now(store, now=NOW)
    row = store.execute("SELECT * FROM market_forecasts WHERE market='total'").fetchone()
    assert (row["line"], row["line_source"]) == (142.5, "market") and row["market_quote_id"] is not None
    assert (GAME, NOW, {"total": 142.5}) in college.calls


def test_a_model_without_repricing_keeps_its_own_line_and_labels_it(tmp_path, monkeypatch):
    conn = make_store(tmp_path, monkeypatch)
    seed(conn)
    install_college(monkeypatch, with_lines=False)
    add_quote(conn, GAME, "total", 142.5, -110, -110, "2026-11-17T22:00:00Z")
    jobs.predict_now(conn, now=NOW)
    row = conn.execute("SELECT * FROM market_forecasts WHERE market='total'").fetchone()
    assert (row["line"], row["line_source"], row["market_quote_id"]) == (141.5, "model", None)


def test_no_model_installed_records_no_forecast_and_never_invents_one(store, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.models.college", None)       # import fails: the model is not installed
    out = jobs.predict_now(store, now=NOW)
    assert store.execute("SELECT COUNT(*) FROM predictions WHERE model_version LIKE '%near_tipoff'").fetchone()[0] == 0
    assert store.execute("SELECT COUNT(*) FROM market_forecasts").fetchone()[0] == 0
    assert out["status"] in ("failed", "skipped")
