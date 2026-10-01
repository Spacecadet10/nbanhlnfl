"""NHL models and the preregistered evaluation stages on a synthetic league (no network, no real data)."""
import json
import math
import random

import pytest

from app import backtest, db, ingest, markets, nhl_eval
from app.models.base import PregameGame
from app.models.hockey import GoalsConfig, GoalsPoisson, HockeyElo, HockeyEloConfig, HockeyResult
from test_multisport import TEAMS

TEAM_IDS = (6, 16, 8)


def synthetic_season(season: int, rng: random.Random, strength: dict) -> bytes:
    games, n = [], 0
    start = f"{season - 1}-10-10T19:00:00"
    from datetime import datetime, timedelta
    t0 = datetime.fromisoformat(start)
    for day in range(36):
        h, a = rng.sample(TEAM_IDS, 2)
        lh, la = 3.0 * math.exp(0.1 + strength[h] - strength[a]), 3.0 * math.exp(strength[a] - strength[h])
        gh, ga = _pois(rng, lh), _pois(rng, la)
        period = 3
        if gh == ga:
            period = rng.choice((4, 5))
            gh, ga = (gh + 1, ga) if rng.random() < 0.55 else (gh, ga + 1)
        n += 1
        games.append({"id": int(f"{season - 1}02{n:04d}"), "easternStartTime": (t0 + timedelta(days=day * 4)).isoformat(),
                      "gameScheduleStateId": 1, "gameStateId": 7, "gameType": 2, "homeScore": gh, "homeTeamId": h,
                      "period": period, "season": int(f"{season - 1}{season}"), "visitingScore": ga, "visitingTeamId": a})
    return json.dumps({"data": games}).encode()


def _pois(rng, lam):
    k, p, L = 0, 1.0, math.exp(-lam)
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


@pytest.fixture
def nhl_store(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(tmp_path / "nhl.sqlite")
    db.migrate(c)
    ingest.ingest_nhl_teams(c, fetch=lambda: TEAMS, retrieved_at="2015-09-01T00:00:00Z")
    rng = random.Random(7)
    strength = {6: 0.25, 16: -0.2, 8: 0.0}
    for season in range(2016, 2027):
        body = synthetic_season(season, rng, strength)
        ingest.ingest_nhl_season(c, season, fetch=lambda _s, b=body: b, retrieved_at=f"{season}-07-01T00:00:00Z")
    yield c
    c.close()


def test_regulation_score_removes_the_deciding_goal():
    g = PregameGame("nhl:1", 2025, "regular", "2025-01-01T00:00:00Z", "nhl:6", "nhl:16", False)
    assert HockeyResult(g, 3, 2, "SO").regulation == (2, 2)
    assert HockeyResult(g, 2, 3, "OT").regulation == (2, 2)
    assert HockeyResult(g, 4, 1, "REG").regulation == (4, 1)
    assert HockeyResult(g, 4, 1, None).regulation is None


def test_goals_model_learns_strength_and_ignores_unknown_decisions():
    m = GoalsPoisson(GoalsConfig(eta=0.05, carry=1.0))
    g = PregameGame("nhl:1", 2025, "regular", "2025-01-01T00:00:00Z", "nhl:6", "nhl:16", False)
    base = m.rates(g)
    for _ in range(30):
        m.update(HockeyResult(g, 5, 1, "REG"))
    lh, la = m.rates(g)
    assert lh > base[0] and la < base[1] and m.predict(g) > 0.6
    before = (dict(m.att), dict(m.dfn))
    m.update(HockeyResult(g, 3, 2, None))
    assert (dict(m.att), dict(m.dfn)) == before


def test_relocated_franchise_keeps_its_rating():
    elo = HockeyElo(HockeyEloConfig(k=20), {"nhl:59": "nhl:53"})
    ari = PregameGame("nhl:1", 2024, "regular", "2024-01-01T00:00:00Z", "nhl:53", "nhl:6", False)
    for _ in range(5):
        elo.update(HockeyResult(ari, 5, 1, "REG"))
    uta = PregameGame("nhl:2", 2024, "regular", "2024-03-01T00:00:00Z", "nhl:59", "nhl:6", False)
    assert elo.predict(uta) == elo.predict(ari)


def test_hockey_elo_does_not_change_the_nba_elo_version():
    from app.models.elo import Elo, EloConfig
    assert backtest.model_version(Elo(EloConfig(k=15, home_advantage=50, carry=0.75, mov=True))) == \
        "elo-carry=0.75-home_advantage=50-k=15-mov=True"


def test_load_rows_isolates_seasons_and_refuses_the_nba_store(nhl_store, tmp_path):
    dev = nhl_eval.load_rows(nhl_store, "development")
    assert {r.game.season for r in dev} == set(nhl_eval.LOADABLE["development"])
    val = nhl_eval.load_rows(nhl_store, "validation")
    assert max(r.game.season for r in val) == 2024
    nba = db.connect(tmp_path / "nba.sqlite")
    db.migrate(nba)
    db.claim_store(nba, "nba")
    with pytest.raises(nhl_eval.NhlEvaluationError):
        nhl_eval.load_rows(nba, "development")
    with pytest.raises(nhl_eval.NhlEvaluationError):
        nhl_eval.tune(val)                                      # tuning never reads validation seasons


def test_walk_forward_uses_only_results_available_before_each_cutoff(nhl_store):
    rows = nhl_eval.load_rows(nhl_store, "development")
    seen = []

    class Spy(GoalsPoisson):
        def update(self, result):
            seen.append(result.game.tipoff_at)
            super().update(result)

        def predict(self, game):
            assert all(t < game.tipoff_at for t in seen)
            return super().predict(game)
    preds = backtest.run(Spy(), rows, extra=nhl_eval._goals_extra)
    assert len(preds) == len(rows) and all("lambda_home" in p for p in preds)


def test_validation_runs_once_seals_and_test_is_logged_once(nhl_store, tmp_path):
    path = tmp_path / "decision.json"
    report = nhl_eval.evaluate_validation(nhl_store, decision_path=path, out_dir=tmp_path / "val", log=lambda _m: None)
    d = json.loads(path.read_text())
    assert d["winner_model"] in ("hockey_elo", "goals_poisson") and d["sha256"]
    assert set(report["markets"]) == {"reg3", "spread -1.5", "spread +1.5", "total 5.5", "total 6.5"}
    for m in report["markets"].values():
        s = m["goals_poisson"]
        assert s["n"] > 0 and abs(sum(s["mean_predicted_a_mid_b"]) - 1) < 1e-3
    with pytest.raises(nhl_eval.NhlEvaluationError, match="already been run"):
        nhl_eval.evaluate_validation(nhl_store, decision_path=path, log=lambda _m: None)
    nhl_eval.evaluate_test(nhl_store, decision_path=path, out_dir=tmp_path / "test", log=lambda _m: None)
    assert len(nhl_eval.test_access_path().read_text().splitlines()) == 1
    with pytest.raises(nhl_eval.NhlEvaluationError, match="already accessed"):
        nhl_eval.evaluate_test(nhl_store, decision_path=path, log=lambda _m: None)
    tampered = {**d, "winner_model": "goals_poisson" if d["winner_model"] == "hockey_elo" else "hockey_elo"}
    path.write_text(json.dumps(tampered))
    with pytest.raises(nhl_eval.NhlEvaluationError, match="modified"):
        nhl_eval.load_decision(nhl_store, path)


def test_decision_refuses_changed_validation_data(nhl_store, tmp_path):
    path = tmp_path / "decision.json"
    nhl_eval.evaluate_validation(nhl_store, decision_path=path, out_dir=tmp_path / "val", log=lambda _m: None)
    g = nhl_store.execute("SELECT * FROM games WHERE season = 2023 AND status='final' LIMIT 1").fetchone()
    body = json.dumps({"data": [{"id": int(g["game_id"][4:]), "easternStartTime": "2022-10-10T19:00:00",
                                 "gameScheduleStateId": 1, "gameStateId": 7, "gameType": 2, "homeScore": 9,
                                 "homeTeamId": int(g["home_team_id"][4:]), "period": 3, "season": 20222023,
                                 "visitingScore": 0, "visitingTeamId": int(g["away_team_id"][4:])}]}).encode()
    ingest.ingest_nhl_season(nhl_store, 2023, fetch=lambda _s: body, retrieved_at="2026-09-01T00:00:00Z")
    with pytest.raises(nhl_eval.NhlEvaluationError, match="changed since sealing"):
        nhl_eval.load_decision(nhl_store, path)


def test_poisson_market_vectors_match_settlement_frequencies():
    """Monte Carlo check: simulated final scores settled with markets.settle match the grid's probabilities."""
    rng = random.Random(3)
    grid = markets.poisson_grid(3.3, 2.6, 0.55)
    n, counts = 40000, {"spread": [0, 0, 0], "total": [0, 0, 0], "reg3": [0, 0, 0]}
    idx = {"a": 0, "mid": 1, "b": 2}
    for _ in range(n):
        h, a = _pois(rng, 3.3), _pois(rng, 2.6)
        dec = "REG"
        if h == a:
            dec = "OT"
            h, a = (h + 1, a) if rng.random() < 0.55 else (h, a + 1)
        for market, line in (("spread", -1.5), ("total", 6.0), ("reg3", None)):
            counts[market][idx[markets.settle(market, line, status="final", home=h, away=a, decision=dec)]] += 1
    for market, line in (("spread", -1.5), ("total", 6.0), ("reg3", None)):
        for got, want in zip((c / n for c in counts[market]), grid.market(market, line)):
            assert abs(got - want) < 0.01
