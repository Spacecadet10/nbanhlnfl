"""NHL starting-goalie snapshots: source status preserved, unknowns recorded, append-only per retrieval, NHL store only."""
import sqlite3

import pytest

from app import db, ingest
from app.sources import espn
from test_live_markets import add_upcoming
from test_multisport import TEAMS, lines_event, nhl_game, nhl_ingest, nhl_season, store  # noqa: F401
from test_storage import payload


def board(home=("Boston Bruins", "123", "J. One", "confirmed", "Confirmed"), away=None, status="STATUS_SCHEDULED",
          date="2024-10-11T23:00Z"):
    e = lines_event(eid="401", date=date, home="Montreal Canadiens", away="Boston Bruins")
    comp = e["competitions"][0]
    comp["status"]["type"]["name"] = status
    for c, g in zip(comp["competitors"], (home, away)):
        if g:
            c["probables"] = [{"name": "probableStartingGoalie", "playerId": int(g[1]),
                               "athlete": {"id": g[1], "fullName": g[2]}, "status": {"type": g[3], "name": g[4]}}]
    return payload(e)


@pytest.fixture
def nhl(store):
    c = store()
    ingest.ingest_nhl_teams(c, fetch=lambda: TEAMS, retrieved_at="2024-10-01T00:00:00Z")
    nhl_ingest(c, nhl_season(nhl_game(state=1, hs=0, vs=0, period=1, start="2024-10-11T19:00:00", home=8, away=6)),
               "2024-10-02T00:00:00Z")
    return c


def day(c, body, at):
    return ingest.ingest_nhl_day(c, "20241011", fetch=lambda _d: body, retrieved_at=at)


def test_status_is_kept_and_a_missing_goalie_is_none_listed(nhl):
    out = day(nhl, board(home=("x", "123", "Sam Montembeault", "expected", "Expected")), "2024-10-11T15:00:00Z")
    assert out["goalies"]["inserted"] == 2 and out["goalies"]["by_status"] == {"expected": 1, "none_listed": 1}
    rows = {r["side"]: dict(r) for r in nhl.execute("SELECT * FROM goalie_snapshots")}
    assert (rows["home"]["source_status"], rows["home"]["player_name"], rows["home"]["source_status_label"]) == \
        ("expected", "Sam Montembeault", "Expected")
    assert rows["away"]["source_status"] == "none_listed" and rows["away"]["espn_player_id"] is None
    assert rows["home"]["retrieved_at"] == "2024-10-11T15:00:00Z"


def test_every_retrieval_is_kept_including_a_return_to_earlier_content(nhl):
    a = board(home=("x", "123", "Sam Montembeault", "expected", "Expected"))
    b = board(home=("x", "456", "Jakub Dobes", "confirmed", "Confirmed"))
    for body, at in ((a, "2024-10-11T15:00:00Z"), (b, "2024-10-11T18:00:00Z"), (a, "2024-10-11T21:00:00Z")):
        day(nhl, body, at)
    home = [tuple(r) for r in nhl.execute("SELECT retrieved_at, player_name, source_status FROM goalie_snapshots "
                                          "WHERE side='home' ORDER BY retrieved_at")]
    assert home == [("2024-10-11T15:00:00Z", "Sam Montembeault", "expected"),
                    ("2024-10-11T18:00:00Z", "Jakub Dobes", "confirmed"),
                    ("2024-10-11T21:00:00Z", "Sam Montembeault", "expected")]
    as_of = {r["side"]: r["player_name"] for r in nhl.execute(ingest.GOALIE_AS_OF_SQL, ("2024-10-11T19:00:00Z",))}
    assert as_of["home"] == "Jakub Dobes"              # nothing retrieved later is visible earlier
    assert ingest.rebuild_goalies(nhl)["inserted"] == 0   # replay adds nothing
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        nhl.execute("UPDATE goalie_snapshots SET source_status='confirmed'")


def test_started_games_are_not_recorded(nhl):
    out = day(nhl, board(status="STATUS_IN_PROGRESS"), "2024-10-11T23:30:00Z")
    assert out["goalies"]["listings"] == 0
    assert nhl.execute("SELECT count(*) FROM goalie_snapshots").fetchone()[0] == 0


def test_goalie_rows_are_refused_in_the_nba_store(store):
    from test_multisport import nba_month
    from test_storage import event
    c = store("nba.sqlite")
    nba_month(c, payload(event()), "2024-01-15T12:00:00Z")
    raw = c.execute("SELECT raw_id FROM raw_responses").fetchone()[0]
    fetch = c.execute("SELECT fetch_id FROM raw_fetches").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="NHL store"):
        c.execute("INSERT INTO goalie_snapshots (game_id, side, fetch_id, raw_id, retrieved_at, source_status) "
                  "VALUES ('espn:1001', 'home', ?, ?, '2024-01-15T12:00:00Z', 'none_listed')", (fetch, raw))


def test_parser_reads_the_real_field_names():
    listings = espn.parse_goalies({"events": [{"id": "1", "date": "2026-10-02T22:30Z", "competitions": [{
        "status": {"type": {"name": "STATUS_SCHEDULED"}},
        "competitors": [
            {"homeAway": "home", "team": {"displayName": "Detroit Red Wings"}, "probables": [{
                "name": "probableStartingGoalie", "playerId": 2590824, "athlete": {"id": "2590824", "fullName": "John Gibson"},
                "status": {"id": "101", "name": "Expected", "type": "expected"}}]},
            {"homeAway": "away", "team": {"displayName": "New York Rangers"}, "probables": []}]}]}]})
    assert [(x.side, x.status, x.player_name) for x in listings] == [("home", "expected", "John Gibson"),
                                                                     ("away", "none_listed", None)]


def test_the_as_of_lookup_used_by_game_detail_ignores_later_listings(nhl):
    day(nhl, board(home=("x", "123", "Sam Montembeault", "expected", "Expected")), "2024-10-11T15:00:00Z")
    day(nhl, board(home=("x", "456", "Jakub Dobes", "confirmed", "Confirmed")), "2024-10-11T18:00:00Z")
    from app.ingest import GOALIE_AS_OF_SQL
    rows = nhl.execute(f"SELECT * FROM ({GOALIE_AS_OF_SQL}) WHERE game_id = ?",
                       ("2024-10-11T16:00:00Z", "nhl:2024020001")).fetchall()
    assert {r["side"]: r["player_name"] for r in rows} == {"home": "Sam Montembeault", "away": None}
