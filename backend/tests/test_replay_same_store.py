"""Replay into the SAME, already-populated store after a result A, a correction B and A again (A -> B -> A).

Identical bytes reuse the raw row (raw_cache.store_fetch), so the first retrieval of the game's last raw row is t1, not t3.
Judging "older response" by that time let `replay` re-apply B and A and append permanent duplicates to the append-only
result_revisions (and flip NHL decisions). Replay must change nothing on a store that already holds every retrieval, a
fresh-store replay must still equal live ingestion, and a genuinely later correction must still be applied and recorded."""
import json

import pytest

from app import db, ingest
from test_nfl import nfl_event, nfl_payload
from test_storage import event, payload


def _nhl(hs, vs, period):
    return json.dumps({"data": [{"id": 2026020001, "easternStartTime": "2026-10-12T19:00:00", "gameScheduleStateId": 1,
                                 "gameStateId": 7, "gameType": 2, "homeScore": hs, "homeTeamId": 6, "period": period,
                                 "season": 20262027, "visitingScore": vs, "visitingTeamId": 16}]}).encode()


NBA_T = ("2024-01-16T02:00:00Z", "2024-01-16T08:00:00Z", "2024-01-16T14:00:00Z", "2024-01-16T20:00:00Z")
NFL_T = ("2024-10-06T22:00:00Z", "2024-10-07T04:00:00Z", "2024-10-07T10:00:00Z", "2024-10-07T16:00:00Z")
NHL_T = ("2026-10-13T03:00:00Z", "2026-10-13T09:00:00Z", "2026-10-13T15:00:00Z", "2026-10-13T21:00:00Z")

CASES = {
    "nba_score": ("espn:1001", NBA_T, lambda c, b, at: ingest.ingest_month(c, "202401", fetch=lambda _m: b, retrieved_at=at),
                  payload(event(status="STATUS_FINAL", away=("10", "HOU", "115"))),
                  payload(event(status="STATUS_FINAL", away=("10", "HOU", "118"))),
                  payload(event(status="STATUS_FINAL", away=("10", "HOU", "119")))),
    "nfl_score": ("nfl:401671001", NFL_T, lambda c, b, at: ingest.ingest_nfl_month(c, "202410", fetch=lambda _m: b, retrieved_at=at),
                  nfl_payload(nfl_event()), nfl_payload(nfl_event(away=("20", "NYJ", "New York Jets", "20"))),
                  nfl_payload(nfl_event(away=("20", "NYJ", "New York Jets", "21")))),
    "nhl_score": ("nhl:2026020001", NHL_T, lambda c, b, at: ingest.ingest_nhl_season(c, 2027, fetch=lambda _s: b, retrieved_at=at),
                  _nhl(4, 1, 3), _nhl(4, 2, 3), _nhl(4, 3, 3)),
    "nhl_decision": ("nhl:2026020001", NHL_T, lambda c, b, at: ingest.ingest_nhl_season(c, 2027, fetch=lambda _s: b, retrieved_at=at),
                     _nhl(3, 2, 3), _nhl(3, 2, 4), _nhl(3, 2, 5)),       # REG -> OT -> REG, then SO
}


def _snap(c, gid):
    return ([tuple(r) for r in c.execute("SELECT * FROM games WHERE game_id=?", (gid,))],
            [tuple(r) for r in c.execute("SELECT * FROM result_revisions WHERE game_id=? ORDER BY revision_id", (gid,))],
            [tuple(r) for r in c.execute("SELECT * FROM game_decisions WHERE game_id=?", (gid,))])


@pytest.mark.parametrize("case", sorted(CASES))
def test_replay_on_a_populated_store_after_a_correction_and_back_changes_nothing(tmp_path, monkeypatch, case):
    gid, (t1, t2, t3, t4), ingest_one, a, b, c_new = CASES[case]
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "store.sqlite")
    db.migrate(conn)
    for body, at in ((a, t1), (b, t2), (a, t3)):
        ingest_one(conn, body, at)
    live = _snap(conn, gid)
    assert len(live[1]) == 3                                   # A, B, A: three genuine versions, in time order

    for _ in range(2):
        totals = ingest.replay(conn)
        assert totals["games_updated"] == 0, totals
        assert _snap(conn, gid) == live                        # no duplicate revision, no flipped row or decision

    # A fresh store rebuilt from the same retrieval log still equals live ingestion.
    fresh = db.connect(tmp_path / "fresh.sqlite")
    db.migrate(fresh)
    for table in ("raw_responses", "raw_fetches"):
        for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1"):
            fresh.execute(f"INSERT INTO {table} VALUES ({', '.join('?' * len(r))})", tuple(r))
    ingest.replay(fresh)
    assert _snap(fresh, gid) == live

    # A genuinely later correction is still applied, recorded and moves availability.
    ingest_one(conn, c_new, t4)
    revisions = conn.execute("SELECT revision, observed_at FROM result_revisions WHERE game_id=? ORDER BY revision",
                             (gid,)).fetchall()
    assert [tuple(r) for r in revisions] == [(1, t1), (2, t2), (3, t3), (4, t4)]
    assert conn.execute("SELECT result_available_at FROM games WHERE game_id=?", (gid,)).fetchone()[0] == t4


@pytest.mark.parametrize("case", sorted(CASES))
def test_two_different_responses_in_the_same_second_replay_without_duplicates(tmp_path, monkeypatch, case):
    # A and B retrieved in the same second: the retrieval log (fetch id) orders them; replaying the log, twice, adds nothing.
    gid, (t1, _t2, _t3, t4), ingest_one, a, b, c_new = CASES[case]
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "store.sqlite")
    db.migrate(conn)
    ingest_one(conn, a, t1)
    ingest_one(conn, b, t1)
    live = _snap(conn, gid)
    assert len(live[1]) == 2                                    # A then B, both genuine, in fetch order
    for _ in range(2):
        assert ingest.replay(conn)["games_updated"] == 0
        assert _snap(conn, gid) == live
    ingest_one(conn, c_new, t4)                                 # a later correction still applies
    assert conn.execute("SELECT COUNT(*) FROM result_revisions WHERE game_id=?", (gid,)).fetchone()[0] == 3


@pytest.mark.parametrize("case", sorted(CASES))
def test_an_older_retrieval_imported_late_never_overwrites_newer_data(tmp_path, monkeypatch, case):
    # A at t1, A again at t3 (no change, so updated_at stays t1), then a retrieval stamped t2 (B) is imported afterwards: it is
    # older than the last retrieval that contained the game and must not regress it to B.
    gid, (t1, t2, t3, _t4), ingest_one, a, b, _c = CASES[case]
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "store.sqlite")
    db.migrate(conn)
    ingest_one(conn, a, t1)
    ingest_one(conn, a, t3)
    live = _snap(conn, gid)
    ingest_one(conn, b, t2)
    assert _snap(conn, gid) == live


def test_a_team_change_on_a_final_is_a_recorded_correction(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "store.sqlite")
    db.migrate(conn)
    t1, t2 = NBA_T[0], NBA_T[1]
    ingest.ingest_month(conn, "202401", fetch=lambda _m: payload(event(status="STATUS_FINAL")), retrieved_at=t1)
    other = payload(event(status="STATUS_FINAL", away=("11", "IND", "115")))
    ingest.ingest_month(conn, "202401", fetch=lambda _m: other, retrieved_at=t2)
    revs = conn.execute("SELECT revision, away_team_id, observed_at FROM result_revisions WHERE game_id='espn:1001' "
                        "ORDER BY revision").fetchall()
    assert [tuple(r)[1:] for r in revs] == [("espn:10", t1), ("espn:11", t2)]
    assert conn.execute("SELECT result_available_at FROM games WHERE game_id='espn:1001'").fetchone()[0] == t2


def test_soccer_same_second_responses_and_late_imports(tmp_path, monkeypatch):
    from app import soccer
    from test_soccer import DRAW, event as sevent, payload as spayload
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(sport="soccer")
    db.migrate(conn)
    b = sevent(eid="401879241", date="2026-11-07T15:00Z", home=("370", "FUL", "Fulham", "2"), away=("361", "NEW", "Newcastle", "1"))
    t = "2026-11-07T17:00:00Z"
    for body in (spayload("eng.1", DRAW), spayload("eng.1", b)):
        soccer.ingest_day(conn, "eng.1", "20261107", fetch=lambda *_a, body=body: body, retrieved_at=t)
    snap = lambda: ([tuple(r) for r in conn.execute("SELECT * FROM soccer_matches")],
                    [tuple(r) for r in conn.execute("SELECT * FROM soccer_result_revisions ORDER BY revision_id")])
    live = snap()
    assert len(live[1]) == 2 and live[0][0][list(conn.execute("SELECT * FROM soccer_matches").fetchone().keys()).index("home_score")] == 2
    for _ in range(2):
        assert soccer.replay(conn)["revisions"] == 0
        assert snap() == live
    soccer.ingest_day(conn, "eng.1", "20261107", fetch=lambda *_a: spayload("eng.1", DRAW), retrieved_at="2026-11-07T16:59:00Z")
    assert snap() == live                                         # an older retrieval imported late changes nothing


@pytest.mark.parametrize("case", sorted(CASES))
def test_rows_stored_before_migration_0035_replay_without_duplicates(tmp_path, monkeypatch, case):
    # A store at 0034 (no last-seen columns) with two different responses in the same second, then migrated: the backfill
    # from recorded facts must stop the first replay after the migration from re-applying them.
    gid, (t1, _t2, _t3, _t4), ingest_one, a, b, _c = CASES[case]
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "store.sqlite")
    db.migrate(conn, upto=34)
    ingest_one(conn, a, t1)
    ingest_one(conn, b, t1)
    assert db.migrate(conn) == ["0035_result_integrity.sql"]
    seen = conn.execute("SELECT last_seen_at, last_seen_fetch_id FROM games WHERE game_id=?", (gid,)).fetchone()
    assert tuple(seen) == (t1, 2)
    live = _snap(conn, gid)
    assert ingest.replay(conn)["games_updated"] == 0 and _snap(conn, gid) == live


def test_a_team_change_leaves_no_phantom_box_score_row(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "store.sqlite")
    db.migrate(conn)
    ingest.ingest_month(conn, "202401", fetch=lambda _m: payload(event(status="STATUS_FINAL")), retrieved_at=NBA_T[0])
    other = payload(event(status="STATUS_FINAL", away=("11", "IND", "115")))
    ingest.ingest_month(conn, "202401", fetch=lambda _m: other, retrieved_at=NBA_T[1])
    teams = [r[0] for r in conn.execute("SELECT team_id FROM team_game_stats WHERE game_id='espn:1001' ORDER BY 1")]
    home, away = conn.execute("SELECT home_team_id, away_team_id FROM games WHERE game_id='espn:1001'").fetchone()
    assert sorted(teams) == sorted([home, away]) and "espn:10" not in teams


def test_an_unlogged_retrieval_orders_before_a_logged_one_in_the_same_second():
    row = {"last_seen_at": "2024-01-16T02:00:00Z", "last_seen_fetch_id": None, "updated_at": "2024-01-16T02:00:00Z"}
    assert ingest.is_newer_retrieval("2024-01-16T02:00:00Z", 7, row)
    assert not ingest.is_newer_retrieval("2024-01-16T02:00:00Z", None, row)
    row = {"last_seen_at": "2024-01-16T02:00:00Z", "last_seen_fetch_id": 7, "updated_at": "2024-01-16T02:00:00Z"}
    assert not ingest.is_newer_retrieval("2024-01-16T02:00:00Z", 7, row)          # the same retrieval: replay is a no-op
    assert not ingest.is_newer_retrieval("2024-01-16T02:00:00Z", 6, row)
    assert ingest.is_newer_retrieval("2024-01-16T02:00:01Z", None, row)


def test_a_team_change_after_migration_is_recorded_even_when_the_last_revision_has_no_teams(tmp_path, monkeypatch):
    # Every revision written before 0035 has NULL teams; a later team change with the same score must still append one.
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "store.sqlite")
    db.migrate(conn, upto=34)
    ingest.ingest_month(conn, "202401", fetch=lambda _m: payload(event(status="STATUS_FINAL")), retrieved_at=NBA_T[0])
    db.migrate(conn)
    assert conn.execute("SELECT home_team_id FROM result_revisions").fetchone()[0] is None
    other = payload(event(status="STATUS_FINAL", away=("11", "IND", "115")))
    ingest.ingest_month(conn, "202401", fetch=lambda _m: other, retrieved_at=NBA_T[1])
    revs = conn.execute("SELECT revision, away_team_id, observed_at FROM result_revisions ORDER BY revision").fetchall()
    assert [tuple(r) for r in revs] == [(1, None, NBA_T[0]), (2, "espn:11", NBA_T[1])]
