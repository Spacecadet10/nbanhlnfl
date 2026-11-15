"""NFL data-only store: registry guards, ESPN NFL scoreboard parsing, ingestion, replay and coverage (synthetic payloads,
no network). NFL is not served: the API, the live engine and every --sport command must refuse it."""
import json

import pytest

from app import config, db, ingest, live, nfl, sports
from app.sources import espn
from test_storage import event, payload

T0 = "2024-10-01T12:00:00Z"
T1 = "2024-10-08T12:00:00Z"
T2 = "2024-10-09T12:00:00Z"


def nfl_event(eid="401671001", date="2024-10-06T17:00Z", year=2024, stype=2, week=5, status="STATUS_FINAL",
              home=("16", "MIN", "Minnesota Vikings", "23"), away=("20", "NYJ", "New York Jets", "17"), neutral=False,
              country=None, note=None):
    def side(kind, t):
        return {"homeAway": kind, "score": t[3], "team": {"id": t[0], "abbreviation": t[1], "displayName": t[2]},
                "statistics": [{"name": "fieldGoalsMade", "displayValue": "3"}]}
    comp = {"neutralSite": neutral, "status": {"type": {"name": status}},
            "competitors": [side("home", home), side("away", away)]}
    if country:
        comp["venue"] = {"fullName": "Stadium", "address": {"city": "X", "country": country}}
    if note:
        comp["notes"] = [{"type": "event", "headline": note}]
    return {"id": eid, "date": date, "season": {"year": year, "type": stype}, "week": {"number": week},
            "competitions": [comp]}


def nfl_payload(*events) -> bytes:
    return json.dumps({"events": list(events)}, sort_keys=True).encode()


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))

    def make(name="s.sqlite"):
        c = db.connect(tmp_path / name)
        db.migrate(c)
        return c
    return make


def nfl_month(c, body, at, month="202410"):
    return ingest.ingest_nfl_month(c, month, fetch=lambda _m: body, retrieved_at=at)


# --- Registry guards: NFL has a store but is not served ---------------------------------------------------------
def test_nfl_is_registered_for_storage_but_not_served(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    assert "nfl" not in sports.SPORT_IDS and "nfl" not in sports.SPORTS
    with pytest.raises(ValueError):
        sports.get("nfl")
    assert sports.store("nfl") is sports.NFL
    assert config.db_path("nfl") == tmp_path / "nfl.sqlite"
    assert config.db_path("nba").name == "nba.sqlite" and config.db_path("nhl").name == "nhl.sqlite"
    with pytest.raises(ValueError):
        sports.store("mlb")


def test_live_engine_refuses_a_store_of_an_unserved_sport(store):
    c = store()
    nfl_month(c, nfl_payload(nfl_event()), T1)
    assert db.store_sport(c) == "nfl"
    with pytest.raises(sports.UnsupportedSport):
        live.engine(c)


def test_live_engine_keeps_nba_and_nhl_dispatch(store):
    c = store()
    assert live.engine(c) is live.NBA_ENGINE          # unlabelled store = NBA, as before
    db.claim_store(c, "nba")
    assert live.engine(c) is live.NBA_ENGINE
    h = store("h.sqlite")
    db.claim_store(h, "nhl")
    assert live.engine(h).sport == "nhl"


def test_cli_and_api_do_not_accept_nfl_as_a_served_sport(tmp_path, monkeypatch):
    from app import cli
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    with pytest.raises(SystemExit):
        cli.main(["--sport", "nfl", "coverage"])
    from fastapi.testclient import TestClient
    from app.main import app
    r = TestClient(app).get("/api/meta", params={"sport": "nfl"})
    assert r.status_code == 422
    assert not (tmp_path / "nfl.sqlite").exists()


# --- Parsing ------------------------------------------------------------------------------------------------------
def test_parse_nfl_scoreboard_ids_types_neutral_ties_and_all_star_games():
    games = espn.parse_nfl_scoreboard(json.loads(nfl_payload(
        nfl_event(),
        nfl_event(eid="2", neutral=True, home=("3", "CHI", "Chicago Bears", "35"), away=("30", "JAX", "Jaguars", "16")),
        nfl_event(eid="3", home=("33", "BAL", "Ravens", "20"), away=("34", "HOU", "Texans", "20")),     # tie
        nfl_event(eid="4", stype=3, week=4, neutral=True, home=("32", "NFC", "NFC", "76"), away=("31", "AFC", "AFC", "63")),
        nfl_event(eid="5", stype=1, home=("99", "XXX", "Unknown", "0"), away=("1", "ATL", "Falcons", "0")),
        nfl_event(eid="6", stype=2, status="STATUS_SCHEDULED", home=("12", "KC", "Chiefs", "0"),
                  away=("2", "BUF", "Bills", "0")))))
    by = {g.game_id: g for g in games}
    g = by["nfl:401671001"]
    assert (g.season, g.season_type, g.tipoff_at, g.status) == (2024, "regular", "2024-10-06T17:00:00Z", "final")
    assert (g.home.team_id, g.away.team_id, g.home_score, g.away_score) == ("nfl:16", "nfl:20", 23, 17)
    assert g.home_stats == {} and g.away_stats == {} and g.odds == []
    assert by["nfl:2"].neutral_site is True
    assert (by["nfl:3"].home_score, by["nfl:3"].away_score) == (20, 20)
    assert by["nfl:4"].season_type == "other"           # Pro Bowl: AFC/NFC all-star sides, never a team result
    assert by["nfl:5"].season_type == "preseason"
    assert by["nfl:6"].status == "scheduled" and by["nfl:6"].home_score is None


def test_parse_nfl_neutral_sites_missing_the_espn_flag_and_misfiled_old_games():
    """Before 2014 ESPN leaves neutralSite false for London games and Super Bowls (seen 2026-11-01: 2007 and 2013 Wembley,
    Super Bowl XL); its feed for 2004-11 also lists a 1988 game dated 2004-11-06. Both are handled from the feed's own fields."""
    games = {g.game_id: g for g in espn.parse_nfl_scoreboard(json.loads(nfl_payload(
        nfl_event(eid="1", date="2007-10-28T17:00Z", year=2007, country="England"),
        nfl_event(eid="2", date="2008-12-07T21:05Z", year=2008, country="Canada"),
        nfl_event(eid="3", date="2006-02-05T23:30Z", year=2005, stype=3, week=4, country="USA", note="Super Bowl XL"),
        nfl_event(eid="4", date="2025-01-26T20:00Z", year=2024, stype=3, week=3, country="USA", note="AFC Championship"),
        nfl_event(eid="5", date="2024-10-06T17:00Z", year=2024, country="USA"),
        nfl_event(eid="6", date="2004-11-06T05:00Z", year=1988),
        nfl_event(eid="7", date="2003-01-26T23:25Z", year=2002, stype=3, note="Super Bowl XXXVII"),
        nfl_event(eid="8", date="2002-08-05T00:00Z", year=2002, stype=1))))}
    assert games["nfl:1"].neutral_site and games["nfl:2"].neutral_site and games["nfl:3"].neutral_site
    assert not games["nfl:4"].neutral_site and not games["nfl:5"].neutral_site
    assert games["nfl:6"].season_type == "other"                     # season year does not match the date
    assert games["nfl:7"].season_type == "postseason" and games["nfl:7"].neutral_site
    assert games["nfl:8"].season_type == "preseason"


def test_nba_parser_is_unchanged_by_the_nfl_rules():
    """The NBA parser does not apply the NFL venue, Super Bowl or season-date rules."""
    g = espn.parse_scoreboard(json.loads(payload(event(date="2020-08-01T18:00Z"))))[0]
    assert g.game_id == "espn:1001" and g.season_type == "regular" and g.neutral_site is False


# --- Ingestion, store identity, replay ------------------------------------------------------------------------------
def test_ingest_claims_an_nfl_store_and_is_idempotent(store):
    c = store()
    body = nfl_payload(nfl_event(), nfl_event(eid="6", status="STATUS_SCHEDULED", date="2024-10-13T17:00Z"))
    s1 = nfl_month(c, body, T1)
    assert db.store_sport(c) == "nfl" and s1["games_inserted"] == 2
    s2 = nfl_month(c, body, T2)
    assert s2["games_inserted"] == 0 and s2["games_updated"] == 0
    assert c.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM team_game_stats").fetchone()[0] == 0      # no basketball box stats
    assert c.execute("SELECT COUNT(*) FROM odds_quotes").fetchone()[0] == 0
    assert {r[0] for r in c.execute("SELECT team_id FROM teams")} == {"nfl:16", "nfl:20"}
    row = c.execute("SELECT * FROM games WHERE game_id='nfl:401671001'").fetchone()
    assert row["result_availability_basis"] == "assumed_tipoff_plus_4h"      # first seen two days after kickoff


def test_nfl_and_nba_stores_refuse_each_other(store):
    a = store("a.sqlite")
    ingest.ingest_month(a, "202401", fetch=lambda _m: payload(event()), retrieved_at=T0)
    with pytest.raises(db.StoreMismatch):
        nfl_month(a, nfl_payload(nfl_event()), T1)
    b = store("b.sqlite")
    nfl_month(b, nfl_payload(nfl_event()), T1)
    with pytest.raises(db.StoreMismatch):
        ingest.ingest_month(b, "202401", fetch=lambda _m: payload(event()), retrieved_at=T0)


def test_replay_rebuilds_identical_nfl_games(store, tmp_path):
    c = store()
    nfl_month(c, nfl_payload(nfl_event(status="STATUS_SCHEDULED", date="2024-10-06T17:00Z")), "2024-10-05T12:00:00Z")
    nfl_month(c, nfl_payload(nfl_event()), "2024-10-06T21:30:00Z")                 # observed within a day
    before = [dict(r) for r in c.execute("SELECT * FROM games ORDER BY game_id")]
    assert before[0]["result_availability_basis"] == "observed_retrieval"
    fresh = store("replayed.sqlite")
    for r in c.execute("SELECT * FROM raw_responses"):
        cols = r.keys()
        fresh.execute(f"INSERT INTO raw_responses ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(r))
    for r in c.execute("SELECT * FROM raw_fetches"):
        cols = r.keys()
        fresh.execute(f"INSERT INTO raw_fetches ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(r))
    out = ingest.replay(fresh)
    assert out["games_inserted"] == 1 and db.store_sport(fresh) == "nfl"
    after = [dict(r) for r in fresh.execute("SELECT * FROM games ORDER BY game_id")]
    assert after == before


# --- Coverage ---------------------------------------------------------------------------------------------------
def test_coverage_counts_seasons_ties_neutral_and_excluded_games(store):
    c = store()
    nfl_month(c, nfl_payload(
        nfl_event(), nfl_event(eid="2", neutral=True),
        nfl_event(eid="3", home=("33", "BAL", "Ravens", "20"), away=("34", "HOU", "Texans", "20")),
        nfl_event(eid="4", stype=3, neutral=True, home=("32", "NFC", "NFC", "76"), away=("31", "AFC", "AFC", "63")),
        nfl_event(eid="6", status="STATUS_SCHEDULED", date="2024-12-01T18:00Z")), T1)
    rep = nfl.coverage(c)
    assert rep["sport"] == "nfl"
    s = rep["seasons"]["2024"]
    assert s["regular"] == {"games": 4, "final": 3, "scheduled": 1, "ties": 1, "neutral": 1, "teams": 4}
    assert s["other"]["games"] == 1
    assert rep["eligible_final_results"] == 3


def test_coverage_refuses_a_non_nfl_store(store):
    c = store()
    ingest.ingest_month(c, "202401", fetch=lambda _m: payload(event()), retrieved_at=T0)
    with pytest.raises(db.StoreMismatch):
        nfl.coverage(c)
