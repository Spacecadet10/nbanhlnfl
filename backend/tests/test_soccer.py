"""European soccer data-only store (app/soccer.py, migration 0034) and its read-only API (/api/soccer/*).

Payload shapes follow real ESPN soccer scoreboards (2026-11 league days; 2024-25 UEFA knockout days with extra time and
shoot-outs). The facts these tests pin down: a draw is a result; extra time hides the 90-minute result; ESPN's competitor
`winner` flag marks the qualifier, never the match result; competitions never mix; repeated ingestion adds nothing; a
corrected result appends a revision; soccer rows never enter another sport's store."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db, ingest, soccer, sports
from app.sources import espn

T1 = "2026-11-07T20:00:00Z"
T2 = "2026-11-07T21:00:00Z"
T3 = "2026-11-07T22:00:00Z"


def side(home_away, tid, abbr, name, score, *, winner=False, shootout=None, aggregate=None):
    c = {"homeAway": home_away, "id": tid, "score": score, "winner": winner,
         "team": {"id": tid, "abbreviation": abbr, "displayName": name}}
    if shootout is not None:
        c["shootoutScore"] = shootout
    if aggregate is not None:
        c["aggregateScore"] = aggregate
    return c


def event(eid="401878758", date="2026-11-07T12:30Z", status="STATUS_FULL_TIME", state="post",
          home=("357", "LEE", "Leeds United", "1"), away=("367", "TOT", "Tottenham Hotspur", "0"),
          season_type=14308, slug="2026-27-english-premier-league", hkw=None, akw=None, series=None, leg=None, notes=()):
    comp = {"status": {"type": {"name": status, "state": state}},
            "competitors": [side("home", *home, **(hkw or {})), side("away", *away, **(akw or {}))],
            "venue": {"fullName": "Elland Road", "address": {"city": "Leeds", "country": "England"}},
            "notes": [{"headline": n} for n in notes]}
    if series is not None:
        comp["series"] = series
    if leg is not None:
        comp["leg"] = {"value": leg, "displayValue": f"{leg}nd Leg"}
    return {"id": eid, "date": date, "season": {"year": 2026, "type": season_type, "slug": slug}, "competitions": [comp]}


def payload(competition="eng.1", *events, type_id=14308, type_name="2026-27 English Premier League"):
    return json.dumps({"leagues": [{"slug": competition, "season": {"type": {"type": type_id, "name": type_name}}}],
                       "events": list(events)}).encode()


# Atlético 1-0 Real Madrid after extra time; Real Madrid through 4-2 on penalties (ESPN: winner flag on Real Madrid).
PENS = event(eid="733606", date="2025-03-12T20:00Z", status="STATUS_FINAL_PEN", season_type=12884, slug="round-of-16",
             home=("1068", "ATM", "Atlético Madrid", "1"), away=("86", "RMA", "Real Madrid", "0"),
             hkw={"winner": False, "shootout": 2, "aggregate": 2.0}, akw={"winner": True, "shootout": 4, "aggregate": 2.0},
             leg=2, notes=["2nd Leg - Tied on aggregate - Real Madrid advance 4-2 on penalties"],
             series={"competitors": [{"id": "1068", "winner": False, "aggregateScore": 2.0},
                                     {"id": "86", "winner": True, "aggregateScore": 2.0}],
                     "completed": True, "title": "Round of 16", "totalCompetitions": 2})
# Manchester United 5-4 Lyon after extra time (7-6 on aggregate).
AET = event(eid="734499", date="2025-04-17T19:00Z", status="STATUS_FINAL_AET", season_type=12889, slug="quarterfinals",
            home=("360", "MAN", "Manchester United", "5"), away=("167", "LYON", "Lyon", "4"), hkw={"winner": True}, leg=2,
            series={"competitors": [{"id": "360", "winner": True, "aggregateScore": 7.0},
                                    {"id": "167", "winner": False, "aggregateScore": 6.0}],
                    "completed": True, "title": "Quarterfinals", "totalCompetitions": 2})
DRAW = event(eid="401879241", date="2026-11-07T15:00Z", home=("370", "FUL", "Fulham", "1"), away=("361", "NEW", "Newcastle", "1"))
LIVE = event(eid="401878757", date="2026-11-07T17:30Z", status="STATUS_FIRST_HALF", state="in",
             home=("393", "NFO", "Nottingham Forest", "0"), away=("382", "MNC", "Manchester City", "0"))
LATER = event(eid="401878759", date="2026-11-08T16:30Z", status="STATUS_SCHEDULED", state="pre",
              home=("364", "LIV", "Liverpool", "0"), away=("359", "ARS", "Arsenal", "0"))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(sport="soccer")
    db.migrate(c)
    yield c
    c.close()


def run(c, competition, body, at=T1, day="20261107"):
    return soccer.ingest_day(c, competition, day, fetch=lambda *_: body, retrieved_at=at)


def row(c, match_id):
    return c.execute("SELECT * FROM soccer_matches WHERE match_id = ?", (match_id,)).fetchone()


# --- Parsing ---
def test_parser_keeps_draws_extra_time_penalties_and_qualification_apart():
    rows = {m.event_id: m for m in espn.parse_soccer_scoreboard(json.loads(payload("uefa.champions", PENS, AET, DRAW)),
                                                                "uefa.champions")}
    pens, aet, draw = rows["733606"], rows["734499"], rows["401879241"]
    assert (pens.status, pens.decided_in, pens.home_score, pens.away_score) == ("final", "penalties", 1, 0)
    assert (pens.home_shootout, pens.away_shootout, pens.aggregate_home, pens.aggregate_away) == (2, 4, 2, 2)
    assert pens.qualified_team_id == "soccer:86" and pens.leg == 2 and pens.stage_label == "Round of 16"
    assert (aet.decided_in, aet.home_shootout, aet.qualified_team_id) == ("extra_time", None, "soccer:360")
    assert (draw.decided_in, draw.home_score, draw.away_score, draw.qualified_team_id) == ("regulation", 1, 1, None)


def test_parser_never_stores_live_scores_or_guesses_unknown_statuses():
    odd = event(eid="9", status="STATUS_SOMETHING_NEW", state="post", home=("1", "AAA", "A", "3"), away=("2", "BBB", "B", "1"))
    rows = {m.event_id: m for m in espn.parse_soccer_scoreboard(json.loads(payload("eng.1", LIVE, LATER, odd)), "eng.1")}
    assert (rows["401878757"].status, rows["401878757"].home_score) == ("in_progress", None)
    assert (rows["401878759"].status, rows["401878759"].decided_in) == ("scheduled", None)
    assert (rows["9"].status, rows["9"].source_status, rows["9"].home_score) == ("other", "STATUS_SOMETHING_NEW", None)


def test_penalty_final_without_shootout_score_keeps_it_unknown():
    no_kicks = json.loads(json.dumps(PENS))
    for c in no_kicks["competitions"][0]["competitors"]:
        c.pop("shootoutScore")
    m = espn.parse_soccer_scoreboard(json.loads(payload("uefa.champions", no_kicks)), "uefa.champions")[0]
    assert (m.decided_in, m.home_shootout, m.away_shootout) == ("penalties", None, None)


def test_payload_of_another_competition_is_rejected_before_anything_is_written(store):
    with pytest.raises(espn.SoccerPayloadMismatch):
        run(store, "esp.1", payload("eng.1", DRAW))
    assert store.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0] == 0
    assert db.store_sport(store) is None


# --- Storage ---
def test_repeated_ingestion_adds_no_rows_but_logs_each_retrieval(store):
    body = payload("eng.1", DRAW, LIVE)
    first, second = run(store, "eng.1", body, T1), run(store, "eng.1", body, T2)
    assert (first["matches_inserted"], second["matches_inserted"], second["matches_updated"]) == (2, 0, 0)
    assert store.execute("SELECT COUNT(*) FROM soccer_matches").fetchone()[0] == 2
    assert store.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0] == 1
    assert store.execute("SELECT COUNT(*) FROM raw_fetches").fetchone()[0] == 2
    assert store.execute("SELECT COUNT(*) FROM soccer_result_revisions").fetchone()[0] == 1
    assert db.store_sport(store) == "soccer"


def test_correction_appends_a_revision_and_never_regresses(store):
    run(store, "eng.1", payload("eng.1", DRAW), T1)
    corrected = event(eid="401879241", date="2026-11-07T15:00Z", home=("370", "FUL", "Fulham", "2"), away=("361", "NEW", "Newcastle", "1"))
    out = run(store, "eng.1", payload("eng.1", corrected), T2)
    assert (out["matches_updated"], out["revisions"]) == (1, 1)
    m = row(store, "soccer:eng.1:401879241")
    assert (m["home_score"], m["result_first_seen_at"], m["result_available_at"]) == (2, T1, T2)
    revs = store.execute("SELECT revision, home_score, observed_at FROM soccer_result_revisions ORDER BY revision").fetchall()
    assert [tuple(r) for r in revs] == [(1, 1, T1), (2, 2, T2)]
    # An unfinished listing later, or an older response, changes nothing.
    unfinished = event(eid="401879241", date="2026-11-07T15:00Z", status="STATUS_SCHEDULED", state="pre",
                       home=("370", "FUL", "Fulham", "0"), away=("361", "NEW", "Newcastle", "0"))
    assert run(store, "eng.1", payload("eng.1", unfinished), T3)["matches_updated"] == 0
    assert run(store, "eng.1", payload("eng.1", DRAW), "2026-11-07T20:30:00Z")["matches_updated"] == 0
    assert row(store, "soccer:eng.1:401879241")["home_score"] == 2
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.execute("UPDATE soccer_result_revisions SET home_score = 9")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.execute("DELETE FROM soccer_result_revisions")


def test_competitions_stay_isolated_and_unique_in_the_database(store):
    same_id = event(eid="401879241", date="2026-11-07T15:00Z", home=("83", "BAR", "Barcelona", "2"), away=("86", "RMA", "Real Madrid", "2"),
                    slug="2026-27-laliga", season_type=14357)
    run(store, "eng.1", payload("eng.1", DRAW))
    run(store, "esp.1", payload("esp.1", same_id, type_id=14357, type_name="2026-27 LALIGA"))
    assert [r[0] for r in store.execute("SELECT match_id FROM soccer_matches ORDER BY 1")] == \
        ["soccer:eng.1:401879241", "soccer:esp.1:401879241"]
    m = row(store, "soccer:eng.1:401879241")
    cols = [k for k in m.keys() if k not in ("match_id",)]
    with pytest.raises(sqlite3.IntegrityError):      # same competition and event under another id
        store.execute(f"INSERT INTO soccer_matches (match_id, {', '.join(cols)}) VALUES ('soccer:eng.1:x', "
                      f"{', '.join('?' * len(cols))})", [m[k] for k in cols])
    with pytest.raises(sqlite3.IntegrityError):      # the id must name its own competition
        store.execute("UPDATE soccer_matches SET competition_id = 'esp.1' WHERE match_id = 'soccer:eng.1:401879241'")
    day = soccer.matches_on(store, "eng.1", __import__("datetime").date(2026, 11, 7))
    assert [x["home"]["name"] for x in day["matches"]] == ["Fulham"]


def test_soccer_rows_never_enter_another_sports_store(tmp_path, monkeypatch, store):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    run(store, "eng.1", payload("eng.1", DRAW))
    with pytest.raises(db.StoreMismatch):
        db.claim_store(store, "nba")
    nba = db.connect(tmp_path / "other.sqlite")
    db.migrate(nba)
    db.claim_store(nba, "nba")
    with pytest.raises(db.StoreMismatch):
        run(nba, "eng.1", payload("eng.1", DRAW))
    m = row(store, "soccer:eng.1:401879241")
    nba.execute("INSERT INTO raw_responses (raw_id, source, endpoint, request_key, content_hash, byte_size, path, "
                "importer_version, first_retrieved_at) VALUES (1, 's', 'e', 'k', 'h', 1, 'p', 'v', ?)", (T1,))
    nba.execute("INSERT INTO teams VALUES ('soccer:370', 'FUL', 'Fulham', ?), ('soccer:361', 'NEW', 'Newcastle', ?)", (T1, T1))
    cols = [k for k in m.keys()]
    with pytest.raises(sqlite3.IntegrityError, match="only in a soccer store"):
        nba.execute(f"INSERT INTO soccer_matches ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [m[k] if k != "last_raw_id" else 1 for k in cols])
    assert "soccer" not in sports.SPORT_IDS
    with pytest.raises(sports.UnsupportedSport):
        sports.get("soccer")


def test_replay_rebuilds_the_same_rows_from_cached_bytes(tmp_path, monkeypatch, store):
    run(store, "uefa.champions", payload("uefa.champions", PENS, AET), T1, day="20250312")
    run(store, "eng.1", payload("eng.1", DRAW, LIVE), T2)
    before = [tuple(r) for r in store.execute("SELECT * FROM soccer_matches ORDER BY match_id")]
    fresh = db.connect(tmp_path / "rebuilt.sqlite")
    db.migrate(fresh)
    db.claim_store(fresh, "soccer")
    for r in store.execute("SELECT * FROM raw_responses ORDER BY raw_id"):
        fresh.execute(f"INSERT INTO raw_responses VALUES ({', '.join('?' * len(r))})", tuple(r))
    for r in store.execute("SELECT * FROM raw_fetches ORDER BY fetch_id"):
        fresh.execute("INSERT INTO raw_fetches VALUES (?, ?, ?)", tuple(r))
    out = soccer.replay(fresh)
    assert out["responses"] == 2 and out["matches_inserted"] == 4
    assert [tuple(r) for r in fresh.execute("SELECT * FROM soccer_matches ORDER BY match_id")] == before
    assert ingest.replay(fresh)["games_inserted"] == 0      # the shared replay never turns soccer into games


def test_views_report_unavailable_regulation_result_and_separate_tie_facts(store):
    run(store, "uefa.champions", payload("uefa.champions", PENS), T1, day="20250312")
    d = soccer.match_detail(store, "soccer:uefa.champions:733606")
    m = d["match"]
    assert (m["regulation_result"], m["end_of_play_result"], m["shootout_winner"]) == (None, "home", "away")
    assert m["qualified_team_id"] == "soccer:86" and m["result_availability_basis"] == "assumed_tipoff_plus_4h"
    assert all(not c["available"] for c in d["capabilities"])
    run(store, "eng.1", payload("eng.1", DRAW), T1)
    draw = soccer.match_detail(store, "soccer:eng.1:401879241")["match"]
    assert (draw["regulation_result"], draw["end_of_play_result"], draw["shootout_winner"]) == ("draw", "draw", None)


# --- API ---
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NBA_API_MODE", "real")
    from app import main
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    return TestClient(main.app)


def _seed():
    c = db.connect(sport="soccer")
    db.migrate(c)
    run(c, "eng.1", payload("eng.1", DRAW, LIVE, LATER), T1)
    run(c, "uefa.champions", payload("uefa.champions", PENS), T1, day="20250312")
    c.close()


def test_api_without_a_store_or_in_demo_mode_says_so(client, monkeypatch):
    r = client.get("/api/soccer/meta")
    assert r.status_code == 503 and "soccer migrate" in r.json()["detail"]
    monkeypatch.setenv("NBA_API_MODE", "demo")
    assert client.get("/api/soccer/meta").status_code == 404


def test_api_serves_one_competition_at_a_time(client):
    _seed()
    meta = client.get("/api/soccer/meta", params={"competition": "eng.1"}).json()
    assert meta["matches_stored"] == 3 and meta["fetched_dates"] == ["2026-11-07"]
    assert {c["id"]: c["matches_stored"] for c in meta["competitions"]}["uefa.champions"] == 1
    assert all(not c["available"] for c in meta["capabilities"])
    day = client.get("/api/soccer/matches", params={"competition": "eng.1", "date": "2026-11-07"}).json()
    assert [m["competition_id"] for m in day["matches"]] == ["eng.1", "eng.1"] and day["fetched"] is True
    assert day["next_date"] == "2026-11-08" and day["matches"][0]["regulation_result"] == "draw"
    assert client.get("/api/soccer/matches", params={"competition": "uefa.champions", "date": "2026-11-07"}).json()["matches"] == []
    unfetched = client.get("/api/soccer/matches", params={"competition": "esp.1", "date": "2026-11-07"}).json()
    assert (unfetched["matches"], unfetched["fetched"]) == ([], False)
    assert client.get("/api/soccer/matches", params={"competition": "mls", "date": "2026-11-07"}).status_code == 422
    detail = client.get("/api/soccer/matches/soccer:uefa.champions:733606").json()
    assert detail["match"]["shootout_winner"] == "away" and detail["result_revisions"][0]["revision"] == 1
    assert "forecast" not in json.dumps(detail["match"])
    assert client.get("/api/soccer/matches/soccer:eng.1:000").status_code == 404
    assert client.get("/api/soccer/matches/espn:401878758").status_code == 404
