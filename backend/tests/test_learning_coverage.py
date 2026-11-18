"""Learning coverage matrix (app/learning_coverage.py): one row per sport x market with separate states."""
import json

import pytest
from fastapi.testclient import TestClient

from app import learning_coverage as lc, main
from learning_support import add_fit, add_forecast, combiner_detail, make_store, write_agents

FIT_AT, BEFORE, AFTER = "2026-11-17T10:00:00Z", "2026-11-17T09:00:00Z", "2026-11-17T11:00:00Z"


OLD = "0" * 64


@pytest.fixture(autouse=True)
def current_defs(monkeypatch):
    """The current weekly/daily adaptive definitions are computed on the store; tests state them."""
    monkeypatch.setattr(lc, "current_definitions", lambda conn: {"weekly": "d" * 64, "daily": "e" * 64}, raising=False)


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
    assert row(m, "ncaab", "winner")["learning_implemented"] is True and "not scheduled" in " ".join(row(m, "ncaab", "winner")["blocked_reason"])
    for sport, market in (("nfl", "winner"), ("soccer", "1x2")):
        r = row(m, sport, market)
        assert r["learning_implemented"] is True and "not scheduled" in " ".join(r["blocked_reason"]) and "challenger" in " ".join(r["blocked_reason"]).lower()
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


def _store(tmp_path, fits_and_rows):
    data, agents = tmp_path / "data", tmp_path / "agents"
    conn = make_store(data, "nba")
    fits_and_rows(conn)
    conn.close()
    write_agents(agents, odds_env={"NBA_DAILY_AGREEMENT": "1"}, trainer_sports="nba")
    return data, agents


def test_rows_under_a_superseded_definition_are_not_yes(tmp_path):
    def build(conn):
        fit = add_fit(conn, "nba", "combiner", "combiner", "success", finished=FIT_AT, definition=OLD, detail=combiner_detail("winner"))
        add_forecast(conn, "nba", "winner", "forecast", fit, AFTER, definition=OLD)
    m = lc.coverage(*_store(tmp_path, build))
    r = row(m, "nba", "winner")
    assert r["trained"]["state"] == "no"
    assert r["recording_prospectively"]["state"] == "no"
    assert r["forecasting_with_fits"]["state"] == "no"
    assert r["evaluated"]["state"] == "no"


def test_current_daily_definition_counts_and_definitions_are_reported(tmp_path):
    def build(conn):
        fit = add_fit(conn, "nba", "combiner", "combiner", "success", finished=FIT_AT, definition="e" * 64, schedule="daily",
                      detail=combiner_detail("winner"))
        add_forecast(conn, "nba", "winner", "forecast", fit, AFTER, definition="e" * 64)
    m = lc.coverage(*_store(tmp_path, build))
    assert row(m, "nba", "winner")["forecasting_with_fits"]["state"] == "yes"
    assert m["definitions"]["nba"] == {"weekly": "d" * 64, "daily": "e" * 64}


def test_forecast_must_carry_a_fit_of_its_own_definition(tmp_path):
    def build(conn):
        fit = add_fit(conn, "nba", "combiner", "combiner", "success", finished=FIT_AT, definition="d" * 64, detail=combiner_detail("winner"))
        add_forecast(conn, "nba", "winner", "forecast", fit, AFTER, definition="e" * 64)      # row definition differs from its fit's
    assert row(lc.coverage(*_store(tmp_path, build)), "nba", "winner")["forecasting_with_fits"]["state"] == "no"


def test_definition_failure_is_unknown_never_yes(tmp_path, monkeypatch):
    def build(conn):
        fit = add_fit(conn, "nba", "combiner", "combiner", "success", finished=FIT_AT, detail=combiner_detail("winner"))
        add_forecast(conn, "nba", "winner", "forecast", fit, AFTER)
    def boom(conn):
        raise RuntimeError("no members")
    monkeypatch.setattr(lc, "current_definitions", boom)
    m = lc.coverage(*_store(tmp_path, build))
    r = row(m, "nba", "winner")
    for k in ("trained", "recording_prospectively", "forecasting_with_fits", "evaluated"):
        assert r[k]["state"] == "unknown" and "definition unavailable (no members)" in r[k]["detail"]
    assert "definition unavailable" in str(m["definitions"]["nba"])


def test_evaluated_is_unknown_when_games_table_is_missing(tmp_path):
    def build(conn):
        fit = add_fit(conn, "nba", "combiner", "combiner", "success", finished=FIT_AT, detail=combiner_detail("winner"))
        add_forecast(conn, "nba", "winner", "forecast", fit, AFTER)
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DROP TABLE games")
    r = row(lc.coverage(*_store(tmp_path, build)), "nba", "winner")
    assert r["evaluated"]["state"] == "unknown" and "games" in r["evaluated"]["detail"]


def test_non_dict_fit_markets_do_not_break_the_row(tmp_path):
    def build(conn):
        add_fit(conn, "nba", "combiner", "combiner", "success", finished=FIT_AT, detail={"markets": {"winner": "x"}})
    assert row(lc.coverage(*_store(tmp_path, build)), "nba", "winner")["trained"]["state"] == "no"


def test_base_model_test_is_separate_from_settled_with_fit(env):
    m = lc.coverage(*env)
    assert row(m, "ncaab", "winner")["base_tested"]["state"] == "yes"
    assert row(m, "ncaab", "winner")["evaluated"]["state"] == "no"
    assert row(m, "nba", "winner")["base_tested"]["state"] == "unknown"


def test_ncaab_challengers_are_implemented_never_scheduled_never_serving_and_trained_only_with_a_cache(env):
    data, agents = env
    m = lc.coverage(data, agents)
    r = row(m, "ncaab", "spread")
    assert r["learning_implemented"] is True and r["scheduled"]["state"] == "no" and "no job" in r["scheduled"]["detail"]
    assert r["trained"]["state"] == "no" and r["recording_prospectively"]["state"] == "no" and r["forecasting_with_fits"]["state"] == "no"
    cache = data / "research" / "challengers" / "ncaab"
    cache.mkdir(parents=True)
    (cache / "refit-2026-11-16-abc.json").write_text(json.dumps({"fits": {"all": {"sigmas": {}, "n": 3}}}))
    m = lc.coverage(data, agents)
    assert row(m, "ncaab", "spread")["trained"]["state"] == "yes" and row(m, "ncaab", "spread")["scheduled"]["state"] == "no"
    assert row(m, "ncaab", "spread")["forecasting_with_fits"]["state"] == "no"
    assert row(m, "nfl", "spread")["trained"]["state"] == "no"


def test_soccer_and_nfl_challengers_are_counted_from_the_store_never_scheduled_never_serving(env):
    data, agents = env
    m = lc.coverage(data, agents)
    for sport, market in (("soccer", "goals_ou"), ("soccer", "double_chance"), ("nfl", "spread")):
        r = row(m, sport, market)
        assert r["learning_implemented"] is True and r["scheduled"]["state"] == "no" and "no job" in r["scheduled"]["detail"]
        assert r["recording_prospectively"]["state"] == "no" and r["forecasting_with_fits"]["state"] == "no" and r["evaluated"]["state"] == "no"
    assert row(m, "soccer", "corners")["recording_prospectively"]["state"] == "no" and "skipped" in row(m, "soccer", "corners")["recording_prospectively"]["detail"]
    assert row(m, "soccer", "asian_handicap")["recording_prospectively"]["state"] == "no" and "blocked" in row(m, "soccer", "asian_handicap")["recording_prospectively"]["detail"]
    from app import learning_challengers as ch
    cc = ch.open_store(data / "research" / "challengers.sqlite")
    for pid, gid, mk, line in (("soccer-challengers-v1", "g1", "soccer_goals_total", 2.5), ("soccer-challengers-v1", "g1", "soccer_1x2", None),
                               ("nfl-challengers-v1", "n1", "spread", -3.5)):
        at = "2026-11-18T18:30:00Z" if pid.startswith("nfl") else "2026-11-18T06:00:00Z"
        cc.execute("INSERT INTO challenger_forecasts (policy_id, policy_sha, spec_name, spec_json, spec_hash, definition, game_id, market, line, "
                   "p_a, p_mid, p_b, cutoff_at, tipoff_at, generated_at, dataset_version, model_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (pid, "s", "n", "{}", "h", "d", gid, mk, line, .5, 0, .5, at, "2026-11-18T20:00:00Z", at, "v", "m"))
    cc.close()
    m = lc.coverage(data, agents)
    assert row(m, "soccer", "goals_ou")["recording_prospectively"]["state"] == "yes" and "1 challenger rows" in row(m, "soccer", "goals_ou")["recording_prospectively"]["detail"]
    assert row(m, "soccer", "double_chance")["recording_prospectively"]["state"] == "yes"       # derived from the 1X2 rows
    assert row(m, "nfl", "spread")["recording_prospectively"]["state"] == "yes" and row(m, "nfl", "winner")["recording_prospectively"]["state"] == "no"
    assert row(m, "soccer", "goals_ou")["trained"]["state"] == "no"                              # fitted per cutoff in memory, nothing stored
