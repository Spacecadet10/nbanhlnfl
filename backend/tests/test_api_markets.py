"""API: explicit sport and market selection, no fallback between sports, markets or policies (synthetic stores)."""
import pytest
from fastapi.testclient import TestClient

from app import db, live, market_live
from app.main import api_mode, app
from test_live_markets import add_upcoming, quote, sealed  # noqa: F401  (fixtures)
from test_nhl_eval import nhl_store  # noqa: F401

DAY = "2026-10-11"


@pytest.fixture
def api(sealed, monkeypatch):
    monkeypatch.setenv("NBA_API_MODE", "real")
    # test_api.py forces demo mode for the whole session at import; these tests need the real routing
    monkeypatch.delitem(app.dependency_overrides, api_mode, raising=False)
    add_upcoming(sealed, gid=2026020001, start="2026-10-11T19:00:00")
    add_upcoming(sealed, gid=2026020002, start="2026-10-11T21:30:00", home=8, away=6)
    quote(sealed, "2026-10-10T14:00:00Z")
    live.predict(sealed, now="2026-10-10T15:00:00Z")
    market_live.forecast(sealed, now="2026-10-10T15:00:00Z")
    return TestClient(app)


def test_meta_describes_the_selected_sport_and_its_markets(api):
    meta = api.get("/api/meta", params={"sport": "nhl"}).json()
    assert meta["sport"] == "nhl" and meta["score_unit"] == "goals"
    assert meta["primary_model"].startswith("hockey_elo-")
    markets = {m["id"]: m for m in meta["markets"]}
    assert set(markets) == {"winner", "reg3", "spread", "total", "team_total_home", "team_total_away"}
    assert all(m["available"] for m in markets.values())
    assert "draws were under-predicted" in markets["reg3"]["note"]
    assert {s["id"]: s["available"] for s in meta["sports"]} == {"nba": False, "nhl": True}


def test_games_carry_only_the_selected_market_and_policy(api):
    body = api.get("/api/games", params={"sport": "nhl", "date": DAY, "market": "spread"}).json()
    assert body["selection"]["market"] == "spread" and body["selection"]["with_forecast"] == 2
    by = {g["game_id"]: g["market_forecast"] for g in body["games"]}
    quoted = by["nhl:2026020001"]
    assert quoted["line"] == -1.5 and quoted["line_source"] == "market" and quoted["quote"]["bookmaker"] == "DraftKings"
    assert [s["label"] for s in quoted["sides"]] == ["BOS -1.5", "CHI +1.5"]
    assert {g["game_id"]: g["home"]["abbr"] for g in body["games"]} == {"nhl:2026020001": "BOS", "nhl:2026020002": "MTL"}
    other = by["nhl:2026020002"]
    assert other["line_source"] == "model" and other["quote"] is None
    assert any("Model-only line" in x for x in other["limitations"])
    assert all(g["slate_pick"] is None for g in body["games"])            # the winner pick is not mixed in
    near = api.get("/api/games", params={"sport": "nhl", "date": DAY, "market": "spread", "policy": "near_tipoff"}).json()
    assert near["selection"]["with_forecast"] == 0 and all(g["market_forecast"] is None for g in near["games"])
    winner = api.get("/api/games", params={"sport": "nhl", "date": DAY}).json()
    assert winner["selection"]["market"] == "winner" and winner["selection"]["with_forecast"] == 2
    assert all(g["market_forecast"] is None for g in winner["games"])


def test_reg3_has_three_sides_and_confidence_filter_counts(api):
    body = api.get("/api/games", params={"sport": "nhl", "date": DAY, "market": "reg3", "min_confidence": 0.99}).json()
    f = body["games"][0]["market_forecast"]
    assert [s["side"] for s in f["sides"]] == ["a", "mid", "b"] and abs(sum(s["probability"] for s in f["sides"]) - 1) < 1e-6
    assert body["selection"]["selected"] == 0 and f["meets_confidence"] is False


def test_game_detail_lists_every_market_once_per_policy(api):
    g = api.get("/api/games/nhl:2026020001", params={"sport": "nhl"}).json()
    assert sorted(f["market"] for f in g["market_forecasts"]) == sorted(
        ["reg3", "spread", "total", "team_total_home", "team_total_away"])
    assert {f["policy"] for f in g["market_forecasts"]} == {"pregame"}


def test_market_history_and_performance(api):
    hist = api.get("/api/market-predictions", params={"sport": "nhl", "market": "total"}).json()
    assert hist["total"] == 2 and hist["record"]["pending"] == 2 and hist["record"]["win_rate"] is None
    perf = api.get("/api/market-performance", params={"sport": "nhl", "market": "total"}).json()
    assert perf["forecasts"] == 2 and perf["record"]["settled"] == 0 and perf["line_sources"] == {"market": 1, "model": 1}
    assert perf["brier_score"] is None and "nothing here is a claim of betting value" in perf["note"].lower()
    assert api.get("/api/market-predictions", params={"sport": "nhl", "market": "winner"}).status_code == 422


def test_no_fallback_between_sports_or_modes(api, monkeypatch, tmp_path):
    # NBA store absent: an NBA request fails clearly instead of showing NHL data
    r = api.get("/api/meta", params={"sport": "nba"})
    assert r.status_code == 503 and "No NBA data store" in r.json()["detail"]
    assert api.get("/api/meta", params={"sport": "mlb"}).status_code == 422
    assert api.get("/api/games", params={"sport": "nhl", "date": DAY, "market": "corners"}).status_code == 422
    # NBA-only market id on the NHL store is fine; reg3 on NBA is undefined (checked against an NBA store below)
    monkeypatch.setenv("NBA_API_MODE", "demo")
    r = api.get("/api/meta", params={"sport": "nhl"})
    assert r.status_code == 404 and "only for the NBA" in r.json()["detail"]
    r = api.get("/api/games", params={"sport": "nba", "date": "2026-10-30", "market": "total"})
    assert r.status_code == 404 and "not part of the demo" in r.json()["detail"]


def test_a_store_holding_the_other_sport_is_refused(api, sealed, monkeypatch, tmp_path):
    other = db.connect(tmp_path / "nba.sqlite")
    db.migrate(other)
    db.claim_store(other, "nhl")       # an NHL-labelled file in the NBA slot
    other.close()
    r = api.get("/api/meta", params={"sport": "nba"})
    assert r.status_code == 503 and "holds nhl data" in r.json()["detail"]
