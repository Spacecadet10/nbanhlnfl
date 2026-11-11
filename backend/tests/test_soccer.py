"""European soccer data-only store (app/soccer.py, migration 0034) and its read-only API (/api/soccer/*).

Payload shapes follow real ESPN soccer scoreboards (2026-11 league days; 2024-25 UEFA knockout days with extra time and
shoot-outs). The facts these tests pin down: a draw is a result; extra time hides the 90-minute result; ESPN's competitor
`winner` flag (match winner on first legs, qualifier on decided ties) is never read; competitions never mix; repeated ingestion adds nothing; a
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


def goal(team, clock, kind="Goal", shootout=False, athlete="9", own=False, pen=False):
    return {"scoringPlay": True, "shootout": shootout, "ownGoal": own, "penaltyKick": pen, "team": {"id": team},
            "clock": {"displayValue": clock}, "type": {"id": "70", "text": kind}, "athletesInvolved": [{"id": athlete}]}


def event(eid="401878758", date="2026-11-07T12:30Z", status="STATUS_FULL_TIME", state="post",
          home=("357", "LEE", "Leeds United", "1"), away=("367", "TOT", "Tottenham Hotspur", "0"),
          season_type=14308, slug="2026-27-english-premier-league", hkw=None, akw=None, series=None, leg=None, notes=(),
          period=None, clock=None, display=None, goals=None):
    st = {"type": {"name": status, "state": state}}
    if period is not None:
        st["period"] = period
    if clock is not None:
        st["clock"] = clock
    if display is not None:
        st["displayClock"] = display
    comp = {"status": st,
            "competitors": [side("home", *home, **(hkw or {})), side("away", *away, **(akw or {}))],
            "venue": {"fullName": "Elland Road", "address": {"city": "Leeds", "country": "England"}},
            "notes": [{"headline": n} for n in notes]}
    if series is not None:
        comp["series"] = series
    if leg is not None:
        comp["leg"] = {"value": leg, "displayValue": f"{leg}nd Leg"}
    if goals is not None:
        comp["details"] = goals
    return {"id": eid, "date": date, "season": {"year": 2026, "type": season_type, "slug": slug}, "competitions": [comp]}


def payload(competition="eng.1", *events, type_id=14308, type_name="2026-27 English Premier League"):
    return json.dumps({"leagues": [{"slug": competition, "season": {"type": {"type": type_id, "name": type_name}}}],
                       "events": list(events)}).encode()


# Atlético 1-0 Real Madrid after extra time; Real Madrid through 4-2 on penalties (ESPN: winner flag on Real Madrid).
PENS = event(eid="733606", date="2025-03-12T20:00Z", status="STATUS_FINAL_PEN", season_type=12884, slug="round-of-16",
             period=5, clock=7200.0, display="120'",                       # as in the real payload
             home=("1068", "ATM", "Atlético Madrid", "1"), away=("86", "RMA", "Real Madrid", "0"),
             hkw={"winner": False, "shootout": 2, "aggregate": 2.0}, akw={"winner": True, "shootout": 4, "aggregate": 2.0},
             leg=2, notes=["2nd Leg - Tied on aggregate - Real Madrid advance 4-2 on penalties"],
             series={"competitors": [{"id": "1068", "winner": False, "aggregateScore": 2.0},
                                     {"id": "86", "winner": True, "aggregateScore": 2.0}],
                     "completed": True, "title": "Round of 16", "totalCompetitions": 2})
# Manchester United 5-4 Lyon after extra time (7-6 on aggregate).
AET = event(eid="734499", date="2025-04-17T19:00Z", status="STATUS_FINAL_AET", season_type=12889, slug="quarterfinals",
            period=4, clock=7200.0, display="120'+3'",                     # as in the real payload
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
    # The hint names a scratch/development data directory, never the live data/ default, and the ESPN terms caveat.
    assert "NBA_DATA_DIR=" in r.json()["detail"] and "not clearly permitted" in r.json()["detail"]
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


# --- Development copies (scripts/dev.sh + app.preview_publish) ---
def test_publisher_republishes_a_soccer_copy_only_when_soccer_data_changes(tmp_path, monkeypatch):
    from datetime import timedelta
    from app import freshness, preview_publish
    from test_freshness import _dev
    live, dev = _dev(tmp_path)
    monkeypatch.setenv("NBA_DATA_DIR", str(live))
    c = db.connect(live / "soccer.sqlite")
    db.migrate(c)
    run(c, "eng.1", payload("eng.1", LIVE), T1)
    rev = freshness.revision(c)
    st = preview_publish.check_once(live, dev, max_lag=timedelta(minutes=10), interval=60)
    assert st["this_check"] == {"soccer": "published"}, st
    reader = sqlite3.connect(dev / "soccer.sqlite")
    assert reader.execute("SELECT status FROM soccer_matches").fetchone()[0] == "in_progress"
    assert preview_publish.check_once(live, dev, max_lag=timedelta(minutes=10), interval=60)["this_check"] == {}
    run(c, "eng.1", payload("eng.1", LIVE), T2)                    # same bytes again: no match changed, but a new retrieval
    assert freshness.revision(c) != rev                            # (fetch-only imports are republished: see below)
    assert preview_publish.check_once(live, dev, max_lag=timedelta(minutes=10), interval=60)["this_check"] == {"soccer": "published"}
    rev = freshness.revision(c)
    finished = event(eid="401878757", date="2026-11-07T17:30Z", home=("393", "NFO", "Nottingham Forest", "1"),
                     away=("382", "MNC", "Manchester City", "1"))
    run(c, "eng.1", payload("eng.1", finished), T3)
    assert freshness.revision(c) != rev
    st = preview_publish.check_once(live, dev, max_lag=timedelta(minutes=10), interval=60)
    assert st["this_check"] == {"soccer": "published"} and st["sports"]["soccer"]["reason"] == "data changed"
    assert reader.execute("SELECT status, home_score FROM soccer_matches").fetchone() == ("final", 1)
    reader.close()
    assert sorted(p.name for p in live.iterdir() if p.name.startswith("soccer.sqlite"))[0] == "soccer.sqlite"


# --- Review fixes (2026-11-07): replay on a populated store, tie facts vs result corrections, real first-leg shape ---
def test_replay_on_the_same_store_after_a_correction_and_back_adds_nothing(store):
    a = payload("eng.1", DRAW)
    b = payload("eng.1", event(eid="401879241", date="2026-11-07T15:00Z", home=("370", "FUL", "Fulham", "2"),
                                away=("361", "NEW", "Newcastle", "1")))
    run(store, "eng.1", a, "2026-11-07T17:00:00Z")
    run(store, "eng.1", b, "2026-11-07T18:00:00Z")
    run(store, "eng.1", a, "2026-11-07T19:00:00Z")             # back to A: same bytes, reused raw row
    before = [tuple(r) for r in store.execute("SELECT * FROM soccer_result_revisions ORDER BY revision_id")]
    assert [r[4] for r in before] == [1, 2, 1]
    out = soccer.replay(store)
    assert (out["matches_updated"], out["revisions"]) == (0, 0)
    assert [tuple(r) for r in store.execute("SELECT * FROM soccer_result_revisions ORDER BY revision_id")] == before
    assert row(store, "soccer:eng.1:401879241")["home_score"] == 1


def _second_leg(completed=True, series=True, kicks=True):
    e = json.loads(json.dumps(PENS))
    comp = e["competitions"][0]
    if not kicks:
        for c in comp["competitors"]:
            c.pop("shootoutScore")
    if not series:
        comp.pop("series")
    else:
        comp["series"]["completed"] = completed
    return e


def test_tie_facts_filled_in_later_are_recorded_but_never_move_result_availability(store):
    first = "2025-03-12T22:30:00Z"
    run(store, "uefa.champions", payload("uefa.champions", _second_leg(completed=False, kicks=False)), first, day="20250312")
    m = row(store, "soccer:uefa.champions:733606")
    assert (m["qualified_team_id"], m["home_shootout"], m["result_available_at"]) == (None, None, first)
    run(store, "uefa.champions", payload("uefa.champions", _second_leg()), "2025-03-13T09:00:00Z", day="20250312")
    m = row(store, "soccer:uefa.champions:733606")
    assert (m["qualified_team_id"], m["home_shootout"], m["away_shootout"]) == ("soccer:86", 2, 4)
    assert (m["result_available_at"], m["result_availability_basis"]) == (first, "observed_retrieval")
    assert store.execute("SELECT COUNT(*) FROM soccer_result_revisions").fetchone()[0] == 2   # traceable, not a correction
    # A later response without the tie block or the kicks keeps what was stored: absence is not removal.
    run(store, "uefa.champions", payload("uefa.champions", _second_leg(series=False, kicks=False)), "2025-03-14T09:00:00Z",
        day="20250312")
    m = row(store, "soccer:uefa.champions:733606")
    assert (m["qualified_team_id"], m["aggregate_home"], m["home_shootout"], m["result_available_at"]) == ("soccer:86", 2, 2, first)
    assert store.execute("SELECT COUNT(*) FROM soccer_result_revisions").fetchone()[0] == 2


def test_score_correction_still_moves_availability(store):
    run(store, "eng.1", payload("eng.1", DRAW), T1)
    fixed = event(eid="401879241", date="2026-11-07T15:00Z", home=("370", "FUL", "Fulham", "0"), away=("361", "NEW", "Newcastle", "1"))
    run(store, "eng.1", payload("eng.1", fixed), T2)
    assert row(store, "soccer:eng.1:401879241")["result_available_at"] == T2


def test_kickoff_of_a_final_match_never_moves(store):
    run(store, "eng.1", payload("eng.1", DRAW), T1)
    moved = event(eid="401879241", date="2026-11-07T16:00Z", home=("370", "FUL", "Fulham", "1"), away=("361", "NEW", "Newcastle", "1"))
    run(store, "eng.1", payload("eng.1", moved), T2)
    assert row(store, "soccer:eng.1:401879241")["kickoff_at"] == "2026-11-07T15:00:00Z"


def test_real_first_leg_shape_has_no_aggregate_or_qualifier_and_ignores_the_winner_flag():
    # Club Brugge 1-3 Aston Villa, 2025-03-04 first leg: ESPN puts the competitor winner flag on the MATCH winner there,
    # series.completed is false and aggregateScore is null.
    leg1 = event(eid="733147", date="2025-03-04T17:45Z", season_type=12884, slug="round-of-16",
                 home=("570", "BRU", "Club Brugge", "1"), away=("362", "AVL", "Aston Villa", "3"), akw={"winner": True}, leg=1,
                 series={"competitors": [{"id": "570", "winner": False, "aggregateScore": None},
                                         {"id": "362", "winner": False, "aggregateScore": None}],
                         "completed": False, "title": "Round of 16", "totalCompetitions": 2})
    m = espn.parse_soccer_scoreboard(json.loads(payload("uefa.champions", leg1)), "uefa.champions")[0]
    assert (m.leg, m.aggregate_home, m.aggregate_away, m.qualified_team_id) == (1, None, None, None)
    # A second leg whose competitor flags disagree with the series: only the series names the qualifier.
    flipped = json.loads(json.dumps(PENS))
    for c in flipped["competitions"][0]["competitors"]:
        c["winner"] = not c["winner"]
    m = espn.parse_soccer_scoreboard(json.loads(payload("uefa.champions", flipped)), "uefa.champions")[0]
    assert m.qualified_team_id == "soccer:86"


def test_leg_is_kept_for_matches_not_yet_played():
    upcoming = event(eid="1", date="2026-03-10T20:00Z", status="STATUS_SCHEDULED", state="pre", leg=2,
                     home=("1", "AAA", "A", "0"), away=("2", "BBB", "B", "0"))
    assert espn.parse_soccer_scoreboard(json.loads(payload("uefa.champions", upcoming)), "uefa.champions")[0].leg == 2


def test_tie_facts_are_kept_only_while_both_teams_are_unchanged(store):
    # A newer response that changes the sides (here: home and away swapped) and omits the tie block must not carry the
    # stored qualifier/aggregate/kicks over in the old orientation, nor break the qualifier CHECK and block the whole day.
    run(store, "uefa.champions", payload("uefa.champions", _second_leg()), T1, day="20250312")
    swapped = _second_leg(series=False, kicks=False)
    comp = swapped["competitions"][0]
    for c in comp["competitors"]:
        c["homeAway"] = "away" if c["homeAway"] == "home" else "home"
    run(store, "uefa.champions", payload("uefa.champions", swapped, DRAW), T2, day="20250312")
    m = row(store, "soccer:uefa.champions:733606")
    assert (m["home_team_id"], m["away_team_id"]) == ("soccer:86", "soccer:1068")
    assert (m["qualified_team_id"], m["aggregate_home"], m["home_shootout"]) == (None, None, None)
    assert row(store, "soccer:uefa.champions:401879241") is not None      # the rest of the day was imported


def test_fetch_only_soccer_imports_are_republished_so_last_imported_advances(tmp_path, monkeypatch):
    # Collection (a scoreboard request) and publication (the development copy) are separate: an import that changes no
    # match -- the same bytes again, or an empty day -- still changes when the source was last asked, so the copy must be
    # republished or its "Last imported" stays behind the live store.
    from datetime import timedelta
    from app import freshness, preview_publish
    from test_freshness import _dev
    live, dev = _dev(tmp_path)
    monkeypatch.setenv("NBA_DATA_DIR", str(live))
    c = db.connect(live / "soccer.sqlite")
    db.migrate(c)
    run(c, "eng.1", payload("eng.1", DRAW), T1)
    preview_publish.check_once(live, dev, max_lag=timedelta(minutes=10), interval=60)
    rev = freshness.revision(c)
    run(c, "eng.1", payload("eng.1", DRAW), T2)                         # same bytes: no match changed
    assert freshness.revision(c) != rev
    st = preview_publish.check_once(live, dev, max_lag=timedelta(minutes=10), interval=60)
    assert st["this_check"] == {"soccer": "published"}, st
    copy = db.connect(dev / "soccer.sqlite")
    assert soccer.last_refresh_at(copy, "eng.1") == T2
    rev = freshness.revision(c)
    run(c, "esp.1", payload("esp.1", type_id=14357, type_name="2026-27 LALIGA"), T3)   # empty day of another competition
    assert freshness.revision(c) != rev
    assert preview_publish.check_once(live, dev, max_lag=timedelta(minutes=10), interval=60)["this_check"] == {"soccer": "published"}
    assert soccer.last_refresh_at(copy, "esp.1") == T3


def test_soccer_revision_part_never_changes_other_stores(tmp_path, monkeypatch):
    from app import freshness
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    nba = db.connect(tmp_path / "nba.sqlite")
    db.migrate(nba)
    db.claim_store(nba, "nba")
    before = freshness.revision(nba)
    nba.execute("INSERT INTO raw_responses (source, endpoint, request_key, content_hash, byte_size, path, importer_version, "
                "first_retrieved_at) VALUES ('espn', 'e', 'k', 'h', 1, 'p', 'v', ?)", (T1,))
    nba.execute("INSERT INTO raw_fetches (raw_id, retrieved_at) VALUES (1, ?)", (T1,))
    assert freshness.revision(nba) == before        # NBA/NHL/NFL fetches never enter the revision (unchanged behaviour)



# --- How a final match ended: status, period, clock, goal minutes and competition context together (audit 2026-11-07) ---
def _ucl(e):
    return espn.parse_soccer_scoreboard(json.loads(payload("uefa.champions", e)), "uefa.champions")[0]


KO = dict(season_type=12884, slug="round-of-16")
SECOND_LEG = dict(season_type=12884, slug="round-of-16", leg=2,
                  series={"competitors": [{"id": "1", "winner": True, "aggregateScore": 3.0},
                                          {"id": "2", "winner": False, "aggregateScore": 2.0}],
                          "completed": True, "title": "Round of 16", "totalCompetitions": 2})


def test_full_time_label_on_a_match_that_went_to_extra_time_is_unknown_not_regulation():
    # 169930 Juventus 2-0 Real Madrid, 2005-03-09: STATUS_FULL_TIME, period 4, clock 122', each goal listed twice.
    juve = event(eid="169930", date="2005-03-09T19:45Z", period=4, clock=7320.0, display="122'", **KO,
                 home=("111", "JUV", "Juventus", "2"), away=("86", "RMA", "Real Madrid", "0"),
                 goals=[goal("111", "76'", athlete="1"), goal("111", "76'", athlete="1"),
                        goal("111", "117'", athlete="2"), goal("111", "117'", athlete="2")])
    m = _ucl(juve)
    assert (m.status, m.decided_in, m.home_score, m.away_score) == ("final", "unknown", 2, 0)
    assert "period 4" in m.decision_note and "STATUS_FULL_TIME" in m.decision_note
    # 416637 Ajax 2-1 Dnipro, 2015-03-19 UEL: STATUS_FULL_TIME, period 4, clock 120'+4', goals 60', 97', 117'.
    ajax = event(eid="416637", date="2015-03-19T20:05Z", period=4, clock=7200.0, display="120'+4'", season_type=12889,
                 slug="round-of-16", home=("139", "AJA", "Ajax Amsterdam", "2"), away=("3768", "DNIP", "Dnipro", "1"),
                 goals=[goal("3768", "60'"), goal("139", "97'"), goal("139", "117'", kind="Goal - Header")])
    m = espn.parse_soccer_scoreboard(json.loads(payload("uefa.europa", ajax)), "uefa.europa")[0]
    assert m.decided_in == "unknown"


def test_false_extra_time_labels_are_unknown():
    # Oct 2001 league matches labelled STATUS_FINAL_AET (period 2, clock 0): leagues have no extra time.
    league = event(eid="20644", date="2001-10-14T18:00Z", status="STATUS_FINAL_AET", period=2, clock=0.0, display="0'",
                   home=("83", "BAR", "Barcelona", "2"), away=("86", "RMA", "Real Madrid", "1"),
                   goals=[goal("83", "20'"), goal("86", "37'"), goal("83", "53'")])
    m = espn.parse_soccer_scoreboard(json.loads(payload("esp.1", league, type_id=1, type_name="x")), "esp.1")[0]
    assert (m.decided_in, m.home_score) == ("unknown", 2) and "extra time is not played" in m.decision_note
    # 21220, UEFA group stage 2001-10-23, STATUS_FINAL_AET with period 2 and no extra-time evidence.
    group = event(eid="21220", date="2001-10-23T18:45Z", status="STATUS_FINAL_AET", period=2, clock=0.0, display="0'",
                  season_type=1, slug="group-stage", home=("1", "AAA", "A", "1"), away=("2", "BBB", "B", "1"),
                  goals=[goal("1", "13'"), goal("2", "19'")])
    assert _ucl(group).decided_in == "unknown"
    # A knockout AET label without any period, clock or goal evidence of extra time is not trusted either.
    bare = event(eid="3", date="2001-03-10T18:45Z", status="STATUS_FINAL_AET", period=2, clock=0.0, **KO,
                 home=("1", "AAA", "A", "1"), away=("2", "BBB", "B", "0"), goals=[goal("1", "13'")])
    assert _ucl(bare).decided_in == "unknown"


def test_consistent_labels_keep_their_meaning():
    reg = event(eid="1", period=2, clock=5700.0, display="90'+5'", home=("1", "AAA", "A", "2"), away=("2", "BBB", "B", "1"),
                goals=[goal("1", "12'"), goal("2", "45'+2'"), goal("1", "90'+4'")])            # 90+ stoppage is not extra time
    assert espn.parse_soccer_scoreboard(json.loads(payload("eng.1", reg)), "eng.1")[0].decided_in == "regulation"
    old = event(eid="2", date="2005-10-02T15:00Z", period=1, clock=5580.0, display="93'",           # 2005 rows: period 1, 93'
                home=("1", "AAA", "A", "1"), away=("2", "BBB", "B", "0"), goals=[goal("1", "19'")])
    assert espn.parse_soccer_scoreboard(json.loads(payload("eng.1", old)), "eng.1")[0].decided_in == "regulation"
    aet = event(eid="734499", status="STATUS_FINAL_AET", period=4, clock=7200.0, display="120'+3'", **SECOND_LEG,
                home=("360", "MAN", "Man Utd", "5"), away=("167", "LYON", "Lyon", "4"),
                goals=[goal("360", "10'"), goal("360", "45'+1'"), goal("167", "71'"), goal("167", "77'"),
                       goal("167", "104'"), goal("167", "109'"), goal("360", "114'"), goal("360", "120'"), goal("360", "120'+1'")])
    assert _ucl(aet).decided_in == "extra_time"
    pens = json.loads(json.dumps(PENS))
    pens["competitions"][0]["status"].update(period=5, clock=7200.0, displayClock="120'")
    assert _ucl(pens).decided_in == "penalties"


def test_goal_dedup_drops_only_verified_duplicates():
    # Distinct goals by the same team in the same minute (different scorers) are both kept; the evidence stays usable.
    two = event(eid="5", period=2, clock=5700.0, home=("1", "AAA", "A", "2"), away=("2", "BBB", "B", "0"),
                goals=[goal("1", "33'", athlete="7"), goal("1", "33'", athlete="8")])
    assert espn.soccer_goal_minutes(two["competitions"][0], 2) == [(33, False), (33, False)]
    # Exact copies are dropped only when that makes the goals add up to the final score.
    dup = event(eid="6", period=2, home=("1", "AAA", "A", "1"), away=("2", "BBB", "B", "0"),
                goals=[goal("1", "50'"), goal("1", "50'")])
    assert espn.soccer_goal_minutes(dup["competitions"][0], 1) == [(50, False)]
    # Copies that still do not add up (or unparseable clocks) make the goal evidence unusable, never guessed.
    bad = event(eid="7", period=2, home=("1", "AAA", "A", "3"), away=("2", "BBB", "B", "0"),
                goals=[goal("1", "50'"), goal("1", "50'")])
    assert espn.soccer_goal_minutes(bad["competitions"][0], 3) is None
    odd = event(eid="8", period=2, home=("1", "AAA", "A", "1"), away=("2", "BBB", "B", "0"), goals=[goal("1", "HT")])
    assert espn.soccer_goal_minutes(odd["competitions"][0], 1) is None
    assert espn.soccer_goal_minutes(event(eid="9")["competitions"][0], 1) is None          # no goal list at all


# --- Result and tie-fact integrity (audit 2026-11-07) ---
def _match_row(c):
    return row(c, "soccer:uefa.champions:733606")


def _ingest_ucl(c, e, at):
    return run(c, "uefa.champions", payload("uefa.champions", e), at, day="20250312")


def test_a_result_correction_never_carries_old_tie_facts_forward(store):
    # Penalties (1-0, kicks 2-4, Real through, agg 2-2, note) corrected to "won in extra time" by a response that omits the
    # shoot-out, the tie block and the note: none of the old facts may survive next to the new result.
    _ingest_ucl(store, _second_leg(), T1)
    corrected = json.loads(json.dumps(PENS))
    comp = corrected["competitions"][0]
    comp["status"]["type"]["name"] = "STATUS_FINAL_AET"
    comp["competitors"][0]["score"] = "2"
    for c in comp["competitors"]:
        c.pop("shootoutScore")
    comp.pop("series"); comp["notes"] = []
    _ingest_ucl(store, corrected, T2)
    m = _match_row(store)
    assert (m["decided_in"], m["home_score"], m["away_score"]) == ("extra_time", 2, 0)
    assert (m["home_shootout"], m["qualified_team_id"], m["aggregate_home"], m["note"]) == (None, None, None, None)
    assert m["result_available_at"] == T2


def test_omitted_note_and_leg_are_kept_without_a_correction(store):
    _ingest_ucl(store, _second_leg(), T1)
    later = _second_leg(series=True, kicks=False)       # the 2-match series still shows it is a second leg
    comp = later["competitions"][0]
    comp["notes"] = []
    comp.pop("leg")
    comp["series"]["totalCompetitions"] = 2
    _ingest_ucl(store, later, T2)
    m = _match_row(store)
    assert m["note"] == "2nd Leg - Tied on aggregate - Real Madrid advance 4-2 on penalties" and m["leg"] == 2
    assert (m["home_shootout"], m["qualified_team_id"]) == (2, "soccer:86")


def test_decision_only_and_shootout_only_corrections_move_availability(store):
    _ingest_ucl(store, _second_leg(), T1)
    kicks = _second_leg()
    kicks["competitions"][0]["competitors"][0]["shootoutScore"] = 3          # 2-4 corrected to 3-4
    _ingest_ucl(store, kicks, T2)
    assert (_match_row(store)["home_shootout"], _match_row(store)["result_available_at"]) == (3, T2)


def test_tie_facts_learned_later_get_their_own_availability(store):
    # Result and aggregate seen right after the match (t1); the qualifier and the kicks arrive at t2. Tie-fact availability
    # moves to t2 -- never backdated -- while the result stays known from t1.
    t1, t2, t3 = "2025-03-12T22:30:00Z", "2025-03-13T09:00:00Z", "2025-03-13T10:00:00Z"
    _ingest_ucl(store, _second_leg(completed=False, kicks=False), t1)
    m = _match_row(store)
    assert (m["result_available_at"], m["tie_facts_available_at"], m["qualified_team_id"]) == (t1, t1, None)
    _ingest_ucl(store, _second_leg(), t2)
    m = _match_row(store)
    assert (m["result_available_at"], m["tie_facts_available_at"], m["qualified_team_id"]) == (t1, t2, "soccer:86")
    _ingest_ucl(store, _second_leg(), t3)                                         # nothing new: stays t2
    assert _match_row(store)["tie_facts_available_at"] == t2


def test_a_team_change_on_a_final_is_recorded_even_with_the_same_score(store):
    run(store, "eng.1", payload("eng.1", DRAW), T1)
    other = event(eid="401879241", date="2026-11-07T15:00Z", home=("370", "FUL", "Fulham", "1"), away=("362", "AVL", "Aston Villa", "1"))
    run(store, "eng.1", payload("eng.1", other), T2)
    revs = store.execute("SELECT revision, home_team_id, away_team_id, observed_at FROM soccer_result_revisions "
                         "WHERE match_id = 'soccer:eng.1:401879241' ORDER BY revision").fetchall()
    assert [tuple(r) for r in revs] == [(1, "soccer:370", "soccer:361", T1), (2, "soccer:370", "soccer:362", T2)]
    assert row(store, "soccer:eng.1:401879241")["result_available_at"] == T2


def test_unknown_endings_are_stored_with_their_reason_and_never_graded(store):
    juve = event(eid="169930", date="2005-03-09T19:45Z", period=4, clock=7320.0, display="122'", **KO,
                 home=("111", "JUV", "Juventus", "2"), away=("86", "RMA", "Real Madrid", "0"),
                 goals=[goal("111", "76'"), goal("111", "117'")])
    run(store, "uefa.champions", payload("uefa.champions", juve), T1, day="20050309")
    m = row(store, "soccer:uefa.champions:169930")
    assert (m["status"], m["decided_in"], m["home_score"]) == ("final", "unknown", 2) and "period 4" in m["decision_note"]
    view = soccer.match_detail(store, "soccer:uefa.champions:169930")["match"]
    assert (view["regulation_result"], view["decided_in"]) == (None, "unknown") and "period 4" in view["decision_note"]


def test_a_malformed_same_team_match_is_rejected_and_recorded_without_losing_the_day(store):
    bad = event(eid="99", home=("1", "AAA", "A", "1"), away=("1", "AAA", "A", "0"))
    out = run(store, "eng.1", payload("eng.1", bad, DRAW), T1)
    assert (out["matches_inserted"], out["rejected"]) == (1, 1)
    assert row(store, "soccer:eng.1:401879241") is not None and row(store, "soccer:eng.1:99") is None
    rej = store.execute("SELECT competition_id, event_id, reason, retrieved_at FROM soccer_rejections").fetchall()
    assert [tuple(r) for r in rej] == [("eng.1", "99", "home and away are the same team (soccer:1)", T1)]
    run(store, "eng.1", payload("eng.1", bad, DRAW), T1)                           # the same retrieval replayed: once
    assert store.execute("SELECT COUNT(*) FROM soccer_rejections").fetchone()[0] == 1


# --- Parser review follow-ups (2026-11-07): evidence the first classifier ignored ---
def test_full_time_claim_with_shootout_evidence_is_unknown():
    e = event(eid="1", period=2, clock=5400.0, hkw={"shootout": 4}, akw={"shootout": 2}, home=("1", "AAA", "A", "1"),
              away=("2", "BBB", "B", "1"), goals=[goal("1", "10'"), goal("2", "20'"), goal("1", "120'", shootout=True)], **SECOND_LEG)
    m = _ucl(e)
    assert (m.decided_in, m.home_shootout) == ("unknown", None) and "shoot-out" in m.decision_note


def test_modern_extra_time_minutes_without_plus_count_as_extra_time_evidence():
    # Inter 4-3 Barcelona 2025 had its only extra-time goal at 99'; relabelled FULL_TIME with period 2 it must not pass.
    e = event(eid="733619", date="2025-05-06T19:00Z", period=2, clock=5400.0, home=("1", "AAA", "A", "4"),
              away=("2", "BBB", "B", "3"), goals=[goal("1", "21'"), goal("1", "45'+1'"), goal("2", "54'"), goal("2", "60'"),
                                                  goal("2", "87'"), goal("1", "90'+3'"), goal("1", "99'")], **SECOND_LEG)
    e["season"]["year"] = 2024
    assert _ucl(e).decided_in == "unknown"
    # A card at 107' is extra-time evidence even when the goal list does not add up to the score (169930 had both).
    card = {"scoringPlay": False, "yellowCard": True, "team": {"id": "1"}, "clock": {"displayValue": "107'"}, "type": {"id": "94"}}
    e2 = event(eid="169930", date="2005-03-09T19:45Z", period=2, clock=5400.0, home=("1", "AAA", "A", "2"),
               away=("2", "BBB", "B", "0"), goals=[goal("1", "76'"), card], **SECOND_LEG)
    e2["season"]["year"] = 2004
    assert _ucl(e2).decided_in == "unknown"
    # 2001-02 data writes regulation stoppage as 91'-95' without '+': not extra-time evidence there.
    old = event(eid="21350", date="2001-10-20T18:00Z", period=2, clock=0.0, home=("1", "AAA", "A", "1"), away=("2", "BBB", "B", "0"),
                goals=[goal("1", "93'")])
    old["season"]["year"] = 2001
    assert espn.parse_soccer_scoreboard(json.loads(payload("esp.1", old, type_id=1, type_name="x")), "esp.1")[0].decided_in == "regulation"


def test_extra_time_only_in_second_legs_of_two_legged_ties_or_finals():
    first_leg = event(eid="1", status="STATUS_FINAL_AET", period=4, clock=7200.0, home=("1", "AAA", "A", "1"), away=("2", "BBB", "B", "0"),
                      goals=[goal("1", "100'")], season_type=12884, slug="round-of-16", leg=1,
                      series={"competitors": [], "completed": False, "totalCompetitions": 2})
    assert _ucl(first_leg).decided_in == "unknown"
    uel_group = event(eid="2", status="STATUS_FINAL_AET", period=4, clock=7200.0, home=("1", "AAA", "A", "1"), away=("2", "BBB", "B", "0"),
                      goals=[goal("1", "100'")], season_type=2532, slug="2009-first-round")
    assert espn.parse_soccer_scoreboard(json.loads(payload("uefa.europa", uel_group)), "uefa.europa")[0].decided_in == "unknown"
    final = event(eid="3", status="STATUS_FINAL_AET", period=4, clock=7200.0, home=("1", "AAA", "A", "1"), away=("2", "BBB", "B", "0"),
                  goals=[goal("1", "100'")], season_type=12881, slug="final")
    assert _ucl(final).decided_in == "extra_time"
    second = event(eid="4", status="STATUS_FINAL_AET", period=4, clock=7200.0, home=("1", "AAA", "A", "1"), away=("2", "BBB", "B", "0"),
                   goals=[goal("1", "100'")], **SECOND_LEG)
    assert _ucl(second).decided_in == "extra_time"


def test_a_final_status_name_on_an_unfinished_match_is_not_a_result():
    # A record named STATUS_FULL_TIME but carrying the first-half type id 25, state 'in' and a 19' clock is not a final.
    e = event(eid="401878757", status="STATUS_FULL_TIME", state="in", period=1, clock=1140.0, home=("1", "AAA", "A", "2"),
              away=("2", "BBB", "B", "1"), goals=[goal("1", "5'"), goal("2", "9'")])
    e["competitions"][0]["status"]["type"].update(id="25", completed=False)
    m = espn.parse_soccer_scoreboard(json.loads(payload("eng.1", e)), "eng.1")[0]
    assert (m.status, m.home_score) == ("other", None)


def test_goal_dedup_requires_every_team_to_add_up():
    # Two copies of one home goal removed would leave 2 goals for a 1-1 match, but both belong to the home team: unusable.
    e = event(eid="5", period=2, home=("1", "AAA", "A", "1"), away=("2", "BBB", "B", "1"),
              goals=[goal("1", "50'"), goal("1", "50'"), goal("1", "60'")])
    assert espn.soccer_goal_minutes(e["competitions"][0], 2) is None


def test_replaying_the_last_retrieval_reclassifies_rows_stored_with_the_old_status_only_rule(store, monkeypatch):
    # A row imported before the classifier existed kept ESPN's FULL_TIME as 'regulation'. Replaying the retrieval it was
    # last seen in re-derives the ending from those same cached bytes (no new source data), records the change as a
    # revision, and a second replay changes nothing.
    juve = event(eid="169930", date="2005-03-09T19:45Z", period=4, clock=7320.0, display="122'", **KO,
                 home=("111", "JUV", "Juventus", "2"), away=("86", "RMA", "Real Madrid", "0"),
                 goals=[goal("111", "76'"), goal("111", "117'")])
    with monkeypatch.context() as mp:                    # the first importer: the status name alone decided the ending
        mp.setattr(espn, "_soccer_ending", lambda name, *_a, **_k: (espn.SOCCER_FINAL[name], None))
        run(store, "uefa.champions", payload("uefa.champions", juve), T1, day="20050309")
    assert row(store, "soccer:uefa.champions:169930")["decided_in"] == "regulation"
    avail = row(store, "soccer:uefa.champions:169930")["result_available_at"]
    out = soccer.replay(store)
    m = row(store, "soccer:uefa.champions:169930")
    assert (out["reclassified"], m["decided_in"], m["result_available_at"]) == (1, "unknown", avail)
    assert "period 4" in m["decision_note"]
    revs = store.execute("SELECT decided_in FROM soccer_result_revisions WHERE match_id = 'soccer:uefa.champions:169930' "
                         "ORDER BY revision").fetchall()
    assert [r[0] for r in revs] == ["regulation", "unknown"]
    again = soccer.replay(store)
    assert (again["reclassified"], again["revisions"]) == (0, 0)


def test_importer_version_names_the_classifying_parser():
    assert espn.SOCCER_IMPORTER_VERSION == "espn-soccer-scoreboard-2"


def test_reclassification_is_labelled_and_not_counted_as_a_source_version(store, monkeypatch):
    juve = event(eid="169930", date="2005-03-09T19:45Z", period=4, clock=7320.0, display="122'", **KO,
                 home=("111", "JUV", "Juventus", "2"), away=("86", "RMA", "Real Madrid", "0"),
                 goals=[goal("111", "76'"), goal("111", "117'")])
    with monkeypatch.context() as mp:
        mp.setattr(espn, "_soccer_ending", lambda name, *_a, **_k: (espn.SOCCER_FINAL[name], None))
        run(store, "uefa.champions", payload("uefa.champions", juve), T1, day="20050309")
    soccer.replay(store)
    revs = store.execute("SELECT decided_in, basis FROM soccer_result_revisions WHERE match_id='soccer:uefa.champions:169930' "
                         "ORDER BY revision").fetchall()
    assert [tuple(r) for r in revs] == [("regulation", "observed"), ("unknown", "reclassified")]
    detail = soccer.match_detail(store, "soccer:uefa.champions:169930")
    assert [r["basis"] for r in detail["result_revisions"]] == ["observed", "reclassified"]


def test_a_soccer_team_change_after_migration_is_recorded_when_old_revisions_have_no_teams(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(sport="soccer")
    db.migrate(c)
    run(c, "eng.1", payload("eng.1", DRAW), T1)
    # Simulate a revision recorded before 0035 (no teams): the only way to get one in a migrated store.
    c.execute("INSERT INTO soccer_result_revisions (match_id, revision, decided_in, home_score, away_score, observed_at, raw_id) "
              "VALUES ('soccer:eng.1:401879241', 2, 'regulation', 1, 1, ?, 1)", (T1,))
    other = event(eid="401879241", date="2026-11-07T15:00Z", home=("370", "FUL", "Fulham", "1"), away=("362", "AVL", "Aston Villa", "1"))
    run(c, "eng.1", payload("eng.1", other), T2)
    last = c.execute("SELECT revision, away_team_id FROM soccer_result_revisions ORDER BY revision DESC LIMIT 1").fetchone()
    assert tuple(last) == (3, "soccer:362")


def test_migrated_tie_fact_availability_matches_a_rebuild_from_the_retrieval_log(tmp_path, monkeypatch):
    # A historical second leg imported before 0035: the result and its tie facts arrived in the same (late) retrieval, so
    # a rebuild gives them the result's assumed availability (kickoff + 4 h). The migration must give the same time, not
    # the retrieval date.
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    from app import raw_cache
    c = db.connect(tmp_path / "old.sqlite")
    db.migrate(c, upto=34)
    db.claim_store(c, "soccer")
    body = payload("uefa.champions", PENS)
    raw_id, _f = raw_cache.store_fetch(c, source=espn.SOCCER_SOURCE, endpoint="e", request_key="competition=uefa.champions&dates=20250312",
                                       body=body, retrieved_at=T1, importer_version="espn-soccer-scoreboard-1")
    for t in (("soccer:1068", "ATM", "Atlético Madrid"), ("soccer:86", "RMA", "Real Madrid")):
        c.execute("INSERT INTO teams VALUES (?, ?, ?, ?)", (*t, T1))
    c.execute("INSERT INTO soccer_matches (match_id, competition_id, event_id, season, season_type_id, stage, stage_label, kickoff_at, "
              "home_team_id, away_team_id, status, source_status, decided_in, home_score, away_score, home_shootout, away_shootout, "
              "leg, aggregate_home, aggregate_away, qualified_team_id, note, first_seen_at, result_first_seen_at, result_available_at, "
              "result_availability_basis, last_raw_id, updated_at) VALUES ('soccer:uefa.champions:733606', 'uefa.champions', "
              "'733606', 2026, 12884, 'round-of-16', 'Round of 16', '2025-03-12T20:00:00Z', 'soccer:1068', 'soccer:86', 'final', "
              "'STATUS_FINAL_PEN', 'penalties', 1, 0, 2, 4, 2, 2, 2, 'soccer:86', ?, ?, ?, '2025-03-13T00:00:00Z', "
              "'assumed_tipoff_plus_4h', ?, ?)",
              ("2nd Leg - Tied on aggregate - Real Madrid advance 4-2 on penalties", T1, T1, raw_id, T1))
    c.execute("INSERT INTO soccer_result_revisions (match_id, revision, decided_in, home_score, away_score, home_shootout, "
              "away_shootout, aggregate_home, aggregate_away, qualified_team_id, observed_at, raw_id) VALUES "
              "('soccer:uefa.champions:733606', 1, 'penalties', 1, 0, 2, 4, 2, 2, 'soccer:86', ?, ?)", (T1, raw_id))
    db.migrate(c)
    migrated = c.execute("SELECT tie_facts_available_at FROM soccer_matches").fetchone()[0]
    fresh = db.connect(tmp_path / "fresh.sqlite")
    db.migrate(fresh)
    db.claim_store(fresh, "soccer")
    for table in ("raw_responses", "raw_fetches"):
        for r in c.execute(f"SELECT * FROM {table} ORDER BY 1"):
            fresh.execute(f"INSERT INTO {table} VALUES ({', '.join('?' * len(r))})", tuple(r))
    soccer.replay(fresh)
    rebuilt = fresh.execute("SELECT tie_facts_available_at FROM soccer_matches").fetchone()[0]
    assert migrated == rebuilt == "2025-03-13T00:00:00Z"


def test_capability_text_does_not_deny_the_live_soccer_models():
    # Live club and international soccer forecasts run in the production release; this build only lacks them.
    text = " ".join(reason for _, _, reason in soccer.CAPABILITIES)
    assert "No soccer model exists" not in text
    assert "No soccer forecast has been recorded" not in text
    assert "production" in dict((i, r) for i, _, r in soccer.CAPABILITIES)["forecasts"]
