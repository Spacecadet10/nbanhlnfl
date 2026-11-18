"""Learning coverage matrix (app/learning_coverage.py): one row per sport x market with separate states."""
import json

import pytest
from fastapi.testclient import TestClient

from app import learning_coverage as lc, main
from learning_support import add_fit, add_forecast, combiner_detail, make_store, write_agents

FIT_AT, BEFORE, AFTER = "2026-11-17T10:00:00Z", "2026-11-17T09:00:00Z", "2026-11-17T11:00:00Z"


@pytest.fixture
def env(tmp_path):
    data, agents = tmp_path / "data", tmp_path / "agents"
    conn = make_store(data, "nba")
    fit = add_fit(conn, "nba", "combiner", "combiner", "success", finished=FIT_AT, detail=combiner_detail("winner", "spread"))
    add_forecast(conn, "nba", "winner", "forecast", fit, AFTER)                    # after the fit, carries it
    add_forecast(conn, "nba", "spread", "unavailable", fit, AFTER)                 # recorded, but no forecast
    add_forecast(conn, "nba", "total", "forecast", fit, BEFORE)                    # generated before the fit finished
    conn.close()
    write_agents(agents, odds_env={"NBA_DAILY_AGREEMENT": "1"}, trainer_sports="nba")
    return data, agents


def row(m, sport, market):
    return next(r for r in m["rows"] if r["sport"] == sport and r["market"] == market)


def test_every_sport_and_market_has_a_row_with_separate_states(env):
    m = lc.coverage(*env)
    sports = {r["sport"] for r in m["rows"]}
    assert sports == {"nba", "nhl", "ncaab", "nfl", "soccer"}
    markets = {r["market"] for r in m["rows"] if r["sport"] == "soccer"}
    assert {"1x2", "double_chance", "draw_no_bet", "goals_ou", "team_goals", "btts", "corners", "corner_handicap",
            "asian_handicap", "intl_1x2"} <= markets
    assert {r["market"] for r in m["rows"] if r["sport"] == "nhl"} == {"winner", "reg3", "spread", "total", "team_total_home", "team_total_away"}
    for k in ("implemented", "learning_implemented", "trained", "scheduled", "recording_prospectively", "forecasting_with_fits",
              "evaluated", "improved_prospectively", "blocked_reason"):
        assert k in m["rows"][0]


def test_forecasting_requires_status_forecast_and_a_fit_finished_before_generation(env):
    m = lc.coverage(*env)
    assert row(m, "nba", "winner")["forecasting_with_fits"]["state"] == "yes"
    assert row(m, "nba", "spread")["forecasting_with_fits"]["state"] == "no"          # unavailable row only
    assert row(m, "nba", "spread")["recording_prospectively"]["state"] == "yes"       # but recorded after the fit
    assert row(m, "nba", "total")["forecasting_with_fits"]["state"] == "no"           # generated before the fit finished
    assert row(m, "nba", "total")["recording_prospectively"]["state"] == "no"


def test_trained_comes_from_the_combiner_fit_markets_and_scheduled_from_the_plist(env):
    m = lc.coverage(*env)
    assert row(m, "nba", "winner")["trained"]["state"] == "yes" and row(m, "nba", "total")["trained"]["state"] == "no"
    assert row(m, "nba", "winner")["scheduled"]["state"] == "yes"
    assert row(m, "nhl", "winner")["scheduled"]["state"] == "no"      # plist covers only nba here


def test_improved_prospectively_is_never_established(env):
    assert {r["improved_prospectively"]["state"] for r in lc.coverage(*env)["rows"]} == {"not_established"}


def test_missing_store_or_table_is_unknown_not_false(tmp_path, env):
    data, agents = env
    r = row(lc.coverage(data, agents), "nhl", "winner")           # no nhl store at all
    assert r["trained"]["state"] == "unknown" and "missing" in r["trained"]["detail"]
    import sqlite3
    c = sqlite3.connect(data / "nba.sqlite", isolation_level=None)
    for t in ("DROP TRIGGER adaptive_forecasts_no_delete",):
        c.execute(t)
    c.execute("DROP TABLE adaptive_forecasts")
    c.close()
    r = row(lc.coverage(data, agents), "nba", "winner")
    assert r["recording_prospectively"]["state"] == "unknown" and "table missing" in r["recording_prospectively"]["detail"]


def test_blocked_reasons_come_from_the_reference_file(env):
    m = lc.coverage(*env)
    ref = json.loads((lc.REFERENCE).read_text())
    assert row(m, "soccer", "asian_handicap")["blocked_reason"]
    assert any("deep" in x.lower() for x in row(m, "nba", "winner")["blocked_reason"])
    assert any("CF" in x or "challenger" in x.lower() for x in row(m, "nhl", "winner")["blocked_reason"])
    assert row(m, "ncaab", "winner")["learning_implemented"] is False and row(m, "ncaab", "winner")["blocked_reason"]
    assert ref["sports"]


def test_learning_is_not_implemented_outside_nba_nhl_so_nothing_is_trained_or_scheduled(env):
    m = lc.coverage(*env)
    for s in ("ncaab", "nfl", "soccer"):
        for r in (x for x in m["rows"] if x["sport"] == s):
            assert r["trained"]["state"] == "no" and r["scheduled"]["state"] == "no" and r["forecasting_with_fits"]["state"] == "no"


def test_endpoint_and_no_write(env, monkeypatch):
    data, agents = env
    before = (data / "nba.sqlite").read_bytes()
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    monkeypatch.setenv("LEARNING_LAUNCHAGENTS_DIR", str(agents))
    r = TestClient(main.app).get("/api/learning/coverage")
    assert r.status_code == 200 and len(r.json()["rows"]) > 20
    assert (data / "nba.sqlite").read_bytes() == before
