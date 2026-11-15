"""Regressions for the independent review of 66c0138..596d452."""
import json

import pytest
from fastapi.testclient import TestClient

from app import ingest, market_live, nhl_eval
from app.main import api_mode, app
from test_live_markets import add_upcoming, quote, sealed  # noqa: F401
from test_nhl_eval import nhl_store  # noqa: F401

NOW = "2026-10-10T15:00:00Z"


def test_decision_cache_sees_a_corrected_decision(sealed):
    """Finding 1: OT -> SO on a validation game changes no game row but must still fail the sealed-data check."""
    nhl_eval.load_decision(sealed)
    g = sealed.execute("SELECT g.*, d.periods FROM games g JOIN game_decisions d USING (game_id) "
                       "WHERE g.season = 2023 AND d.decision = 'OT' LIMIT 1").fetchone()
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    start = datetime.strptime(g["tipoff_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) \
        .astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%dT%H:%M:%S")
    body = json.dumps({"data": [{"id": int(g["game_id"][4:]), "easternStartTime": start,
                                 "gameScheduleStateId": 1, "gameStateId": 7, "gameType": 2, "homeScore": g["home_score"],
                                 "homeTeamId": int(g["home_team_id"][4:]), "period": 5, "season": 20222023,
                                 "visitingScore": g["away_score"], "visitingTeamId": int(g["away_team_id"][4:])}]}).encode()
    before = sealed.execute("SELECT updated_at FROM games WHERE game_id=?", (g["game_id"],)).fetchone()[0]
    ingest.ingest_nhl_season(sealed, 2023, fetch=lambda _s: body, retrieved_at="2026-09-01T00:00:00Z")
    row = sealed.execute("SELECT g.updated_at, d.decision FROM games g JOIN game_decisions d USING (game_id) "
                         "WHERE game_id=?", (g["game_id"],)).fetchone()
    assert row["updated_at"] == before and row["decision"] == "SO"    # only the decision changed
    with pytest.raises(nhl_eval.NhlEvaluationError, match="changed since sealing"):
        nhl_eval.load_decision(sealed)


def _final(c, period, hs, vs):
    body = json.dumps({"data": [{"id": 2026020001, "easternStartTime": "2026-10-11T19:00:00", "gameScheduleStateId": 1,
                                 "gameStateId": 7, "gameType": 2, "homeScore": hs, "homeTeamId": 6, "period": period,
                                 "season": 20262027, "visitingScore": vs, "visitingTeamId": 16}]}).encode()
    ingest.ingest_nhl_season(c, 2027, fetch=lambda _s: body, retrieved_at="2026-10-12T03:00:00Z")


def test_a_regulation_draw_is_a_loss_for_a_three_way_pick(sealed):
    """Finding 2: an OT game settles reg3 on the draw; the home/away pick lost, it is not a push."""
    add_upcoming(sealed, start="2026-10-11T19:00:00")
    market_live.forecast(sealed, now=NOW)
    _final(sealed, 4, 3, 2)
    reg3 = market_live.score(sealed)["markets"]["reg3"]
    assert (reg3["win"], reg3["loss"], reg3["push"], reg3["pick_accuracy"]) == (0, 1, 0, 0.0)


def test_line_moving_away_and_back_shows_the_current_line(sealed):
    """Finding 3: -1.5 -> -2.5 -> -1.5; the latest forecast must be at -1.5, made at the third time."""
    add_upcoming(sealed, start="2026-10-11T19:00:00")
    quote(sealed, "2026-10-10T14:00:00Z")
    market_live.forecast(sealed, now=NOW)
    quote(sealed, "2026-10-10T15:30:00Z", spread=("-2.5", "+2.5"))
    market_live.forecast(sealed, now="2026-10-10T16:00:00Z")
    quote(sealed, "2026-10-10T16:30:00Z")
    back = market_live.forecast(sealed, now="2026-10-10T17:00:00Z")
    assert [f["status"] for f in back["forecasts"] if f["market"] == "spread"] == ["created"]
    latest = {r["market"]: r for r in market_live.latest(sealed)}["spread"]
    assert (latest["line"], latest["generated_at"]) == (-1.5, "2026-10-10T17:00:00Z")
    again = market_live.forecast(sealed, now="2026-10-10T17:00:00Z")   # an exact retry is still idempotent
    assert again["created"] == 0
    assert [r[0] for r in sealed.execute("SELECT line FROM market_forecasts WHERE market='spread' ORDER BY forecast_id")] \
        == [-1.5, -2.5, -1.5]


def test_bookmaker_comparison_leaves_out_pushes(sealed, monkeypatch):
    """Finding 5: at an integer line a push must not score the two-way bookmaker price at log(0)."""
    monkeypatch.setenv("NBA_API_MODE", "real")
    monkeypatch.delitem(app.dependency_overrides, api_mode, raising=False)
    add_upcoming(sealed, start="2026-10-11T19:00:00")
    quote(sealed, "2026-10-10T14:00:00Z", total=("o5", "u5"))
    market_live.forecast(sealed, now=NOW)
    _final(sealed, 3, 3, 2)                                  # total 5 = push at 5
    perf = TestClient(app).get("/api/market-performance", params={"sport": "nhl", "market": "total"}).json()
    assert perf["record"]["pushes"] == 1 and perf["market_matched"] == 0 and perf["market_log_loss_on_matched"] is None


def test_a_broken_sealed_decision_is_a_503_with_the_reason(sealed, monkeypatch, tmp_path):
    """Finding 6."""
    monkeypatch.setenv("NBA_API_MODE", "real")
    monkeypatch.delitem(app.dependency_overrides, api_mode, raising=False)
    monkeypatch.setattr(nhl_eval, "DECISION_PATH", tmp_path / "missing.json")
    r = TestClient(app).get("/api/meta", params={"sport": "nhl"})
    assert r.status_code == 503 and "Sealed model decision unusable" in r.json()["detail"]
