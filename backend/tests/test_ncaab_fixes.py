"""Review fixes for the NCAAB serving layer, with the REAL `app/models/college.py` and a sealed decision (synthetic store).

B1 model id carries the sealed decision; S1 decision gate; S5 any line is priced; N1 one dataset hash; N3 malformed probs;
orientation of the spread / totals; probabilities stored with full precision; recovery is not defined for NCAAB."""
import json

import pytest

import ncaab_support as sup
from app import coverage, jobs, market_live, markets, near_tipoff, ncaab, ncaab_app
from app.models import college
from test_ncaab_eval import CUTOFF, SHIPPED, add, sealed_decision, store  # noqa: F401  (store: fixture with a sealed decision)

NOW = sup.NOW


@pytest.fixture
def real(store, monkeypatch):
    monkeypatch.setattr(ncaab_app, "DECISION_PATH", college.DECISION_PATH)
    sup.refreshed(store)
    return store


def _model(conn, cutoff=CUTOFF):
    college.clear_cache()
    model = ncaab_app.CollegeModel(conn)
    model.as_of = cutoff
    return model, model.distribution(ncaab_app.pregame(conn.execute("SELECT * FROM games WHERE game_id=?", (sup.GAME,)).fetchone()))


# --- B1: the stored winner id carries the frozen decision's version -----------------------------------------------------
def test_winner_id_carries_the_decisions_version_and_a_model_change_is_not_a_ledger_conflict(real):
    first = near_tipoff.predict_now(real, now=NOW)
    d = json.loads(college.DECISION_PATH.read_text())
    version = f"{d['model_version']}-fit={d['sha256'][:8]}"
    assert first["created"] == 1 and first["model_version"] == f"{version}-window=2h-policy=near_tipoff"
    assert first["model_version"].startswith("ncaab-college_elo-")
    sealed = json.loads(college.DECISION_PATH.read_text())
    assert f"-fit={sealed['sha256'][:8]}-window=2h" in first["model_version"]      # the fitted values are part of the id
    sealed_decision(real, college.DECISION_PATH, winner_model="college_eff", winner_calibration=[0.96, 0.007])
    second = near_tipoff.predict_now(real, now=NOW)              # same cycle, another sealed model: its own id, no LedgerConflict
    assert second["created"] == 1 and second["model_version"] != first["model_version"]
    assert second["model_version"].startswith("ncaab-college_eff-") and "+platt-fit=" in second["model_version"]
    stored = {r[0] for r in real.execute("SELECT model_version FROM predictions WHERE run_type='live'")}
    assert stored == {first["model_version"], second["model_version"]}


def test_a_refit_with_the_same_config_but_other_fitted_values_gets_its_own_ids(real):
    """Review should-fix 2: sigmas and Platt coefficients change the probabilities, so they must change both ids."""
    first = near_tipoff.predict_now(real, now=NOW)
    m1 = ncaab_app.market_model(real)[1]
    sigmas = {**json.loads(college.DECISION_PATH.read_text())["sigmas"]}
    sigmas["elo_margin"] = sigmas["elo_margin"] + 1.0
    sealed_decision(real, college.DECISION_PATH, sigmas=sigmas)
    second = near_tipoff.predict_now(real, now=NOW)                # same config, refitted spread: no LedgerConflict, new ids
    assert second["created"] == 1 and second["model_version"] != first["model_version"]
    assert ncaab_app.market_model(real)[1] != m1


def test_each_market_forecast_records_the_model_that_ships_for_it(real):
    decision = sealed_decision(real, college.DECISION_PATH, markets={
        "spread": {"model": "college_eff"}, "total": {"model": "running_points"},
        "team_total_home": {"model": "college_eff"}, "team_total_away": {"model": "college_eff"}})
    out = market_live.forecast(real, policy="near_tipoff", now=NOW, game_ids=[sup.GAME])
    assert out["created"] == 4
    rows = {r["market"]: json.loads(r["params"]) for r in real.execute("SELECT market, params FROM market_forecasts")}
    eff, points = college.market_version(decision, "college_eff"), college.market_version(decision, "running_points")
    for market, params in rows.items():
        assert params["market_models"][market] == (points if market == "total" else eff)
    assert eff != points and points == "ncaab-running_points"
    # another market assignment is another market id (a stored forecast is never re-stated under the same id)
    other = sealed_decision(real, college.DECISION_PATH, markets={m: {"model": "college_eff"} for m in SHIPPED})
    again = market_live.forecast(real, policy="near_tipoff", now=NOW, game_ids=[sup.GAME])
    assert again["model_version"] != out["model_version"] and again["created"] == 4 and other["sha256"] != decision["sha256"]


# --- S1: no valid sealed decision, no winner forecast, no market forecast, no card -------------------------------------------
def test_without_a_valid_decision_nothing_is_forecast(real):
    path = college.DECISION_PATH
    d = json.loads(path.read_text())
    d["college_elo_config"]["k"] = 99.0                         # modified after sealing: the digest no longer matches
    path.write_text(json.dumps(d))
    assert ncaab_app.decision() == {} and "digest" in ncaab_app.decision_issue()
    with pytest.raises(ncaab_app.CollegeModelUnavailable):
        ncaab_app.primary(real)
    with pytest.raises(Exception):
        near_tipoff.predict_now(real, now=NOW)
    assert real.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0
    from app.market_live import MarketModelUnavailable
    with pytest.raises(MarketModelUnavailable):
        ncaab_app.market_model(real)
    out = jobs.predict_now(real, now=NOW)
    assert out["status"] == "failed" and "decision" in out["error"].lower()


def test_cards_need_a_valid_decision_and_a_store(real, tmp_path):
    from datetime import datetime, timezone
    from app import telegram as tg
    assert ncaab_app.card_issue() is None                       # the scratch store exists in the data directory
    college.DECISION_PATH.write_text("{}")
    assert "NCAAB decision" in ncaab_app.card_issue()

    class Bot:
        def call(self, method, body, **kwargs):
            assert method == "getUpdates", f"{method} must not be called"
            return []
    with tg.ledger(tmp_path / "receipts.sqlite") as conn:
        out = tg.run_once(Bot(), {"TELEGRAM_CHAT_ID": "123"}, conn, now=datetime(2026, 11, 17, 22, 30, tzinfo=timezone.utc))
    assert out["sports"]["ncaab"]["status"] == "inactive" and "NCAAB decision" in out["sports"]["ncaab"]["reason"]
    assert "NCAAB decision" in out["sports"]["ncaab"]["reason"] and "failed" not in out["sports"]["ncaab"].values()


def test_cards_are_refused_visibly_without_an_ncaab_store(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    assert ncaab_app.card_issue() == "No NCAAB store in this data directory"


# --- S5: any line is priced; the sealed fallback never errors ---------------------------------------------------------------
def test_the_distribution_has_what_markets_model_line_and_checks_need(real):
    model, dist = _model(real)
    for market in college.LINE_MARKETS:
        line = markets.model_line(dist, market)                  # reads dist.median: it used to raise AttributeError
        assert line == dist.model_line(market)
        lines = [line + k for k in (-4.0, -1.0, 0.0, 2.0, 5.5)]  # whole lines push, half lines do not
        markets.check_consistency(dist, market, lines)
        for other in lines:
            assert sum(dist.market(market, other)) == pytest.approx(1)
    assert dist.market("total", 140.0)[1] > 0 and dist.market("total", 140.5)[1] == 0


def test_the_sealed_fallback_prices_a_quote_at_another_line_or_says_why_not(real):
    sup.add_quote(real, sup.GAME, "total", 151.5, -110, -110, "2026-11-17T22:00:00Z")
    out = coverage.record(real, now=NOW)
    rows = {r["market"]: r for r in real.execute("SELECT * FROM game_forecasts WHERE game_id=?", (sup.GAME,))}
    assert set(rows) == {"winner", "spread", "total", "team_total_home", "team_total_away"}
    for market, r in rows.items():
        assert r["status"] in ("forecast", "missing"), (market, dict(r))
        assert r["status"] == "forecast", (market, r["reason"], r["fallback_reason"])
    assert rows["total"]["line"] == 151.5 and rows["total"]["line_source"] == "bookmaker"
    assert rows["winner"]["model_version"].startswith("ncaab-college_elo-") and rows["spread"]["source"] != "primary_recorded"
    assert out is not None


# --- N1: one dataset hash ------------------------------------------------------------------------------------------------
def test_forecasts_record_the_dataset_hash_the_data_module_and_the_model_compute(real):
    eng = ncaab_app.engine()
    rows = eng.load_rows(real)
    raw = ncaab.load_rows(real, cutoff="9999-12-31T00:00:00Z")
    assert [r.game.game_id for r in rows] == [r["game_id"] for r in raw]
    assert eng.dataset_version(rows) == ncaab.dataset_version(raw) == college.rows_version(rows)
    out = near_tipoff.predict_now(real, now=NOW)
    assert out["dataset_version"] == ncaab.dataset_version(raw)
    real.execute("UPDATE ncaab_game_context SET both_d1 = 0, away_conference_id = NULL WHERE game_id = 'ncaab:100'")
    real.commit()
    assert "ncaab:100" not in {r.game.game_id for r in eng.load_rows(real)}      # the data module's eligibility, not a copy


# --- N3 -------------------------------------------------------------------------------------------------------------------
def test_a_malformed_probability_dict_is_a_value_error():
    with pytest.raises(ValueError):
        ncaab_app._probs({"x": 0.5, "y": 0.5})
    assert ncaab_app._probs({"over": 0.4, "push": 0.1, "under": 0.5}) == (0.4, 0.1, 0.5)
    assert ncaab_app._probs((0.3, 0.7)) == (0.3, 0.0, 0.7)


# --- orientation with the real module ------------------------------------------------------------------------------------------
@pytest.fixture
def favourite(real):
    """Team 1 (home tonight) has beaten everyone by 25-30; team 2 has lost to everyone by as much."""
    n = 0
    for day, (home, away) in enumerate([("1", "3"), ("1", "4"), ("3", "2"), ("4", "2"), ("1", "3"), ("4", "2"), ("1", "4"), ("3", "2")]):
        n += 1
        add(real, f"7{n:02d}", f"2026-11-{6 + day:02d}T00:00:00Z", home, away, 90, 62 if home == "1" else 60)
    return real


def test_a_strong_home_favourite_has_a_negative_spread_line_and_orientation_is_monotonic(favourite):
    model, dist = _model(favourite)
    spec = dist.pred["markets"]["spread"]
    assert spec["mean"] > 5 and spec["line"] < 0 and dist.model_line("spread") == spec["line"]
    assert dist.winner()[0] > 0.7
    assert abs(dist.market("spread", spec["line"])[0] - 0.5) < 0.05           # p(home covers at its own line) about a half
    lines = [spec["line"] + k for k in (-10.0, -5.0, -1.0, 1.0, 5.0, 10.0)]
    covers = [dist.market("spread", line)[0] for line in lines]
    assert covers == sorted(covers) and covers[0] < 0.2 and covers[-1] > 0.8     # a bigger handicap is easier for the home team
    assert dist.market("spread", 0.5)[0] > dist.winner()[0] - 0.05            # +0.5 handicap covers whenever the favourite wins


@pytest.mark.parametrize("market", ["total", "team_total_home", "team_total_away"])
def test_over_under_orientation_of_totals_and_team_totals(favourite, market):
    model, dist = _model(favourite)
    own = dist.model_line(market)
    assert abs(dist.market(market, own)[0] - 0.5) < 0.05                       # p(over) about a half at its own line
    overs = [dist.market(market, own + k)[0] for k in (-12.0, -6.0, -1.0, 1.0, 6.0, 12.0)]
    assert overs == sorted(overs, reverse=True) and overs[0] > 0.6 > 0.4 > overs[-1]    # a higher line is harder to go over
    assert dist.market(market, own)[2] == pytest.approx(1 - dist.market(market, own)[0])
    assert dist.median(market) == pytest.approx(dist.pred["markets"][market]["mean"])


# --- full precision ------------------------------------------------------------------------------------------------------------
def test_probabilities_are_stored_and_served_with_full_precision(tmp_path, monkeypatch):
    conn = sup.make_store(tmp_path, monkeypatch)
    sup.seed(conn)
    sup.add_game(conn, "404", "2026-11-17T23:45:00Z", "3", "4")
    fake = sup.install_college(monkeypatch)
    given = {"ncaab:401": 0.573, "ncaab:404": 0.574}
    real_predict = fake.predict

    def predict(conn_, game_id, *, cutoff, lines=None):
        out = real_predict(conn_, game_id, cutoff=cutoff, lines=lines)
        return {**out, "home_win_prob": given.get(game_id, out["home_win_prob"])}
    monkeypatch.setattr(fake, "predict", predict)
    out = near_tipoff.predict_now(conn, now=NOW)
    assert {f["game_id"]: f["home_win_prob"] for f in out["forecasts"]} == given
    stored = {r["game_id"]: r["home_win_prob"] for r in conn.execute(
        "SELECT game_id, home_win_prob FROM predictions WHERE model_version LIKE '%near_tipoff'")}
    assert stored == given
    jobs.predict_now(conn, now=NOW)
    conn.commit()
    from fastapi.testclient import TestClient
    from app import db, main
    from app.providers.real import RealProvider
    path = conn.execute("PRAGMA database_list").fetchone()["file"]

    def provider(mode, sport):
        c = db.connect(path, check_same_thread=False)
        try:
            yield RealProvider(c, now=NOW)
        finally:
            c.close()
    monkeypatch.setattr(main, "_provider", provider)
    body = TestClient(main.app).get("/api/games", params={"sport": "ncaab", "date": "2026-11-17", "policy": "canonical"}).json()
    served = {g["game_id"]: g["canonical"]["record"]["home_win_prob"] for g in body["games"]}
    assert served == given


# --- B2: no recovery for NCAAB without touching other sports -------------------------------------------------------------------
def test_ncaab_has_no_recovery_and_the_cycle_still_returns_it(real, monkeypatch):
    monkeypatch.setattr(jobs, "_forecast_due", lambda conn: {"status": "skipped"})
    out = jobs.forecast_due(real)
    assert out["recovery"]["status"] == "not_defined" and out["adaptive_recovery"]["status"] == "not_defined"
