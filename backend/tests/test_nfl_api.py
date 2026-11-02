"""Read-only NFL API (/api/nfl/*): schedule/results, matchup details and honest capability flags from the data-only NFL
store. NFL stays outside the served sports: no forecast, odds, injury or metric is ever returned for it."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import db, ingest, nfl
from test_nfl import nfl_event, nfl_payload

AT = "2024-10-20T12:00:00Z"


def _ingest(month_events, at=AT, month="202410"):
    c = db.connect(sport="nfl")
    db.migrate(c)
    ingest.ingest_nfl_month(c, month, fetch=lambda _m: nfl_payload(*month_events), retrieved_at=at)
    c.close()


EVENTS = [
    # Week 5: MIN 23-17 NYJ (Sunday 1 p.m. ET), a Monday-night game at 00:15 UTC that belongs to Monday Eastern
    nfl_event(eid="1", date="2024-10-06T17:00Z"),
    nfl_event(eid="2", date="2024-10-08T00:15Z", home=("12", "KC", "Kansas City Chiefs", "26"),
              away=("15", "NO", "New Orleans Saints", "13")),
    # Week 6: a tie and the rematch-style second MIN game
    nfl_event(eid="3", date="2024-10-13T17:00Z", home=("20", "NYJ", "New York Jets", "20"),
              away=("16", "MIN", "Minnesota Vikings", "20")),
    nfl_event(eid="4", date="2024-10-13T20:25Z", status="STATUS_POSTPONED", home=("12", "KC", "Kansas City Chiefs", "0"),
              away=("2", "BUF", "Buffalo Bills", "0")),
    # Week 8 (future at AT): scheduled, neutral site
    nfl_event(eid="5", date="2024-10-27T13:30Z", status="STATUS_SCHEDULED", neutral=True, country="England"),
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NBA_API_MODE", "real")
    from app import main
    # test_api.py overrides api_mode to demo for the whole session at import; these tests choose the mode via the env.
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    return TestClient(main.app)


def test_missing_nfl_store_is_an_honest_503_and_creates_nothing(client, tmp_path):
    r = client.get("/api/nfl/meta")
    assert r.status_code == 503 and "No NFL data store" in r.json()["detail"]
    assert not (tmp_path / "nfl.sqlite").exists()


def test_demo_mode_has_no_nfl_data(client, monkeypatch):
    monkeypatch.setenv("NBA_API_MODE", "demo")
    r = client.get("/api/nfl/games", params={"date": "2024-10-06"})
    assert r.status_code == 404 and "demo" in r.json()["detail"].lower()


def test_a_store_of_another_sport_at_the_nfl_path_is_refused(client, tmp_path):
    c = db.connect(tmp_path / "nfl.sqlite")
    db.migrate(c)
    db.claim_store(c, "nba")
    c.close()
    r = client.get("/api/nfl/meta")
    assert r.status_code == 503 and "nba" in r.json()["detail"]


def test_meta_lists_every_forecast_capability_as_unavailable(client):
    _ingest(EVENTS)
    m = client.get("/api/nfl/meta").json()
    assert m["sport"] == "nfl" and m["mode"] == "real" and m["available"] is True
    assert m["seasons"] == [2024] and m["games_stored"] == 5
    assert m["last_refresh_at"] == "2024-10-20T12:00:00Z"
    caps = {c["id"]: c for c in m["capabilities"]}
    assert set(caps) >= {"forecasts", "odds", "injuries", "performance", "history", "research"}
    assert all(c["available"] is False and c["reason"] for c in caps.values())
    assert m["default_date"] in m["game_dates"]


def test_default_date_is_the_next_game_day_or_the_last_one():
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    dates = ["2024-10-06", "2024-10-07", "2024-10-13", "2024-10-27"]
    assert nfl.default_date(dates, date(2024, 10, 8)) == "2024-10-13"
    assert nfl.default_date(dates, date(2024, 10, 13)) == "2024-10-13"
    assert nfl.default_date(dates, date(2025, 1, 1)) == "2024-10-27"
    assert nfl.default_date([], date(2025, 1, 1)) is None


def test_games_on_an_eastern_date_with_ties_postponements_and_navigation(client):
    _ingest(EVENTS)
    d = client.get("/api/nfl/games", params={"date": "2024-10-13"}).json()
    assert d["date"] == "2024-10-13" and d["prev_date"] == "2024-10-07" and d["next_date"] == "2024-10-27"
    by = {g["game_id"]: g for g in d["games"]}
    assert set(by) == {"nfl:3", "nfl:4"}
    tie = by["nfl:3"]
    assert (tie["status"], tie["home_score"], tie["away_score"], tie["winner"]) == ("final", 20, 20, "tie")
    assert tie["home"] == {"team_id": "nfl:20", "abbr": "NYJ", "name": "New York Jets"}
    assert tie["result_availability_basis"] == "assumed_tipoff_plus_4h"      # first seen a week after kickoff
    post = by["nfl:4"]
    assert post["status"] == "postponed" and post["winner"] is None and post["home_score"] is None
    monday = client.get("/api/nfl/games", params={"date": "2024-10-07"}).json()["games"]
    assert [g["game_id"] for g in monday] == ["nfl:2"]          # 00:15 UTC Tuesday = Monday night Eastern
    assert client.get("/api/nfl/games", params={"date": "2024-10-08"}).json()["games"] == []
    for g in d["games"]:
        assert not {"prediction", "forecast", "odds", "market", "probability"} & set(g)


def test_game_detail_has_only_earlier_results_for_context(client):
    _ingest(EVENTS)
    d = client.get("/api/nfl/games/nfl:3").json()
    assert d["game"]["game_id"] == "nfl:3" and d["game"]["winner"] == "tie"
    assert [g["game_id"] for g in d["head_to_head"]] == ["nfl:1"]      # earlier MIN-NYJ meeting only, not itself
    assert [g["game_id"] for g in d["home_recent"]] == ["nfl:1"]       # NYJ's finals before this kickoff
    assert [g["game_id"] for g in d["away_recent"]] == ["nfl:1"]
    future = client.get("/api/nfl/games/nfl:5").json()
    assert future["game"]["neutral_site"] is True and future["game"]["status"] == "scheduled"
    assert [g["game_id"] for g in future["head_to_head"]] == ["nfl:3", "nfl:1"]   # newest first, finals only
    assert all(c["available"] is False for c in future["capabilities"])
    assert client.get("/api/nfl/games/nfl:999").status_code == 404
    assert client.get("/api/nfl/games/espn:1").status_code == 404


def test_served_endpoints_still_refuse_nfl(client):
    _ingest(EVENTS)
    assert client.get("/api/games", params={"sport": "nfl", "date": "2024-10-06"}).status_code == 422
    assert client.get("/api/meta", params={"sport": "nfl"}).status_code == 422


def test_bad_date_is_rejected(client):
    _ingest(EVENTS)
    assert client.get("/api/nfl/games", params={"date": "2024-13-45"}).status_code == 422
