"""Traceable result corrections (settlement C): every final result a source delivers is kept as an append-only revision;
a correction adds a revision instead of erasing the earlier result, so a forecast's grade under each result stays
traceable. Forecast rows never change. Synthetic stores, no network."""
import sqlite3

import pytest

from app import db
from test_adaptive import CYCLE_AT, TRAIN_AT, store  # noqa: F401  (fixture)
from test_coverage import _settle
from test_live import event, ingest_events  # noqa: F401
from test_live import store as nba_store  # noqa: F401  (fixture)
from test_live_markets import add_upcoming, sealed  # noqa: F401  (fixture)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)
from test_research_live import GID, game  # noqa: F401  (fixture)
from test_canonical_markets import nhl  # noqa: F401  (fixture)

T_GAME = "2026-10-21T23:30:00Z"


def revisions(conn, gid):
    return [dict(r) for r in conn.execute("SELECT * FROM result_revisions WHERE game_id=? ORDER BY revision", (gid,))]


def test_an_nhl_correction_adds_a_revision_and_keeps_the_first_result(game):
    _settle(game, hs=4, vs=1, at="2026-10-13T03:00:00Z")                       # a regulation final
    [r1] = revisions(game, GID)
    assert (r1["revision"], r1["home_score"], r1["away_score"], r1["decision"]) == (1, 4, 1, "REG")
    assert r1["observed_at"] == "2026-10-13T03:00:00Z" and r1["basis"] == "observed" and r1["raw_id"] is not None
    _settle(game, hs=4, vs=1, at="2026-10-13T04:00:00Z")                       # the same result again: no revision
    assert len(revisions(game, GID)) == 1
    _settle(game, hs=1, vs=4, at="2026-10-13T05:00:00Z")                       # the source corrects the score
    r = revisions(game, GID)
    assert [(x["revision"], x["home_score"], x["away_score"], x["observed_at"]) for x in r] == [
        (1, 4, 1, "2026-10-13T03:00:00Z"), (2, 1, 4, "2026-10-13T05:00:00Z")]
    g = game.execute("SELECT home_score, away_score FROM games WHERE game_id=?", (GID,)).fetchone()
    assert (g["home_score"], g["away_score"]) == (1, 4)                         # the current result is the latest revision


def test_a_changed_regulation_decision_alone_is_a_revision(game):
    _settle(game, hs=3, vs=2, at="2026-10-13T03:00:00Z")                       # REG (period 3)
    from test_live_markets import add_upcoming as upd
    upd(game, start="2026-10-12T19:00:00", state=7, hs=3, vs=2, period=4, at="2026-10-13T05:00:00Z")   # now decided in OT
    r = revisions(game, GID)
    assert [x["decision"] for x in r] == ["REG", "OT"] and [x["home_score"] for x in r] == [3, 3]


def test_revisions_are_append_only(game):
    _settle(game, hs=4, vs=1)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        game.execute("UPDATE result_revisions SET home_score=9")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        game.execute("DELETE FROM result_revisions")


def test_an_nba_correction_adds_a_revision(nba_store):
    ingest_events(nba_store, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    ingest_events(nba_store, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 112)], "2026-10-22T09:00:00Z")
    r = revisions(nba_store, "espn:g1")
    assert [(x["revision"], x["home_score"], x["away_score"], x["decision"]) for x in r] == [(1, 110, 100, None), (2, 110, 112, None)]
    assert [x["observed_at"] for x in r] == ["2026-10-22T03:00:00Z", "2026-10-22T09:00:00Z"]


def test_the_migration_backfills_one_revision_per_stored_final_without_inventing_earlier_ones(tmp_path):
    conn = db.connect(tmp_path / "s.sqlite")
    db.migrate(conn, upto=29)
    ingest_events(conn, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100),
                         event("g2", "2026-10-23T23:30:00Z", "3", "4")], "2026-10-22T03:00:00Z")
    db.migrate(conn)
    [r] = [dict(x) for x in conn.execute("SELECT * FROM result_revisions")]
    assert (r["game_id"], r["revision"], r["home_score"], r["away_score"]) == ("espn:g1", 1, 110, 100)
    assert r["basis"] == "backfill"                       # the history starts here; earlier versions are not reconstructed
    assert r["observed_at"] == "2026-10-22T03:00:00Z"


def _availability(conn, gid):
    return dict(conn.execute("SELECT result_first_seen_at, result_available_at, result_availability_basis FROM games "
                             "WHERE game_id=?", (gid,)).fetchone())


def test_a_game_first_seen_in_progress_is_stamped_when_its_final_was_seen_never_backdated(nba_store):
    """Training audit 2026-10-14 (A), reproduced: a game first met in progress whose final arrived more than 24 h after the
    start was treated as historical backfill and stamped start + 4 h (real instance nhl:2026020007, ~20 h early), so any
    walk 'as of' a time in between consumed a result nobody had yet."""
    from app import live
    tip = "2026-10-21T23:30:00Z"
    ingest_events(nba_store, [event("late1", tip, "7", "8", "STATUS_IN_PROGRESS", 50, 48)], "2026-10-22T00:30:00Z")
    ingest_events(nba_store, [event("late1", tip, "7", "8", "STATUS_FINAL", 101, 99)], "2026-10-23T00:00:00Z")
    a = _availability(nba_store, "espn:late1")
    assert a["result_available_at"] == "2026-10-23T00:00:00Z" and a["result_availability_basis"] == "observed_retrieval"
    assert "espn:late1" not in {x.game.game_id for x in live.available_rows(nba_store, "2026-10-22T12:00:00Z")}


def test_a_regulation_decision_correction_is_only_known_from_its_retrieval_on(game):
    """Training audit 2026-10-14 (B), reproduced: REG -> OT with the same score kept the first availability time, so a walk
    'as of' 05:00 used OT (regulation 2-2) although only REG 3-2 was knowable then. Like a score correction, the corrected
    result is available from the retrieval that delivered it."""
    from app import nhl_eval
    _settle(game, hs=3, vs=2, at="2026-10-13T03:00:00Z")
    from test_live_markets import add_upcoming as upd
    upd(game, start="2026-10-12T19:00:00", state=7, hs=3, vs=2, period=4, at="2026-10-13T09:00:00Z")
    a = _availability(game, GID)
    assert a["result_available_at"] == "2026-10-13T09:00:00Z" and a["result_first_seen_at"] == "2026-10-13T03:00:00Z"
    assert not [r for r in nhl_eval.load_rows(game, "live") if r.game.game_id == GID and r.result_available_at <= "2026-10-13T05:00:00Z"]


def test_a_corrected_result_shows_the_grade_under_each_result_on_every_page(nhl):
    """Settlement C: the grade under each result revision is shown, oldest first; the current grade is the latest one.
    Nothing is stored for it (graded on read), forecast rows never change, and a regraded forecast is counted."""
    from app import coverage
    from test_canonical_markets import AFTER, CYCLE_AT as CYCLE, run as run_cycle
    conn, client = nhl
    run_cycle(conn, CYCLE)
    coverage.record(conn, now=CYCLE)
    _settle(conn, hs=4, vs=1, at="2026-10-13T03:00:00Z")
    _settle(conn, hs=1, vs=4, at="2026-10-13T03:30:00Z")            # the source corrects the final score
    conn.commit()
    rows_before = conn.execute("SELECT count(*) FROM game_forecasts").fetchone()[0]
    client.now = AFTER
    w = next(c for c in client.get(f"/api/games/{GID}", params={"sport": "nhl"}).json()["canonical_markets"] if c["market"] == "winner")
    hist = w["grade_history"]
    assert [(h["revision"], h["home_score"], h["away_score"], h["observed_at"]) for h in hist] == [
        (1, 4, 1, "2026-10-13T03:00:00Z"), (2, 1, 4, "2026-10-13T03:30:00Z")]
    first, second = hist[0]["grade"], hist[1]["grade"]
    assert {first, second} == {"win", "loss"} and w["grade"] == second and w["regraded"] is True
    h = next(r for r in client.get("/api/canonical/forecasts", params={"sport": "nhl"}).json()["rows"] if r["game_id"] == GID)
    assert h["canonical"]["grade_history"] == hist and h["canonical"]["grade"] == second
    perf = client.get("/api/canonical/performance", params={"sport": "nhl"}).json()["competitive"]
    assert perf["regraded"] == 1
    assert conn.execute("SELECT count(*) FROM game_forecasts").fetchone()[0] == rows_before


def test_an_uncorrected_result_has_a_single_grade_and_is_not_regraded(nhl):
    from app import coverage
    from test_canonical_markets import AFTER, CYCLE_AT as CYCLE, run as run_cycle
    conn, client = nhl
    run_cycle(conn, CYCLE)
    coverage.record(conn, now=CYCLE)
    _settle(conn, hs=4, vs=1, at="2026-10-13T03:00:00Z")
    conn.commit()
    client.now = AFTER
    w = next(c for c in client.get(f"/api/games/{GID}", params={"sport": "nhl"}).json()["canonical_markets"] if c["market"] == "winner")
    assert w["regraded"] is False and len(w["grade_history"]) == 1 and w["grade_history"][0]["grade"] == w["grade"]


# --- Review 2 (2026-10-14) findings, reproduced by the reviewer; each asserts the corrected behaviour ------------------
@pytest.mark.parametrize("first_status", ["STATUS_POSTPONED", "STATUS_SCHEDULED"])
def test_a_historical_game_first_seen_years_later_unfinished_keeps_the_assumed_time(tmp_path, first_status):
    """Finding 1: a backfill (retrieved in 2026) that lists a 2022 game unfinished, then final, is not 'tracked live': its
    result keeps the assumed start + 4 h, so a rebuilt store keeps the sealed validation fingerprints."""
    conn = db.connect(tmp_path / "s.sqlite")
    db.migrate(conn)
    ingest_events(conn, [event("pp", "2022-01-05T00:00:00Z", "1", "2", first_status, 0, 0, 2022)], "2026-09-30T00:00:00Z",
                  month="202201")
    ingest_events(conn, [event("pp", "2022-03-01T00:00:00Z", "1", "2", "STATUS_FINAL", 101, 99, 2022)], "2026-09-30T00:05:00Z",
                  month="202203")
    a = _availability(conn, "espn:pp")
    assert a["result_available_at"] == "2022-03-01T04:00:00Z" and a["result_availability_basis"] == "assumed_tipoff_plus_4h"


def _playoff(c, period, at, gid=2026030001):
    import json
    from app import ingest
    body = json.dumps({"data": [{"id": gid, "easternStartTime": "2026-10-12T19:00:00", "gameScheduleStateId": 1,
                                 "gameStateId": 7, "gameType": 3, "homeScore": 3, "homeTeamId": 6, "period": period,
                                 "season": 20262027, "visitingScore": 2, "visitingTeamId": 16}]}).encode()
    ingest.ingest_nhl_season(c, 2027, fetch=lambda _s: body, retrieved_at=at)


def test_a_periods_only_restatement_moves_nothing(game):
    """Finding 2: double OT restated as triple OT is still 'OT': no revision, availability unchanged."""
    _playoff(game, 5, "2026-10-13T03:00:00Z")
    _playoff(game, 6, "2026-10-13T09:00:00Z")
    assert [r["decision"] for r in revisions(game, "nhl:2026030001")] == ["OT"]
    assert _availability(game, "nhl:2026030001")["result_available_at"] == "2026-10-13T03:00:00Z"


def test_a_first_decision_arriving_after_the_score_is_not_a_correction(nhl):
    """Finding 3: final without a period (decision unknown), then REG: the decision became known, nothing was corrected."""
    from app import coverage
    from test_canonical_markets import CYCLE_AT as CYCLE, run as run_cycle
    conn, client = nhl
    run_cycle(conn, CYCLE)
    coverage.record(conn, now=CYCLE)
    add_upcoming(conn, start="2026-10-12T19:00:00", state=7, hs=4, vs=1, period=0, at="2026-10-13T03:00:00Z")
    add_upcoming(conn, start="2026-10-12T19:00:00", state=7, hs=4, vs=1, period=3, at="2026-10-13T09:00:00Z")
    conn.commit()
    client.now = "2026-10-13T10:00:00Z"
    c = next(x for x in client.get(f"/api/games/{GID}", params={"sport": "nhl"}).json()["canonical_markets"] if x["market"] == "reg3")
    assert [(h["decision"], h["change"]) for h in c["grade_history"]] == [(None, "first"), ("REG", "decision_known")]
    assert c["regraded"] is False
    assert client.get("/api/canonical/performance", params={"sport": "nhl", "market": "reg3"}).json()["competitive"]["regraded"] == 0


def test_a_decision_correction_is_a_correction(game):
    """REG -> OT with the same score is a correction of the result (not only a score correction)."""
    from app import coverage
    _settle(game, hs=3, vs=2, at="2026-10-13T03:00:00Z")
    add_upcoming(game, start="2026-10-12T19:00:00", state=7, hs=3, vs=2, period=4, at="2026-10-13T09:00:00Z")
    g = game.execute("SELECT * FROM games WHERE game_id=?", (GID,)).fetchone()
    h = coverage.grade_history("reg3", None, (0.5, 0.2, 0.3), g, "2026-10-13T10:00:00Z", g["tipoff_at"], revisions(game, GID))
    assert [(x["change"], x["grade"]) for x in h] == [("first", "win"), ("correction", "loss")]


def test_grade_history_only_shows_results_known_by_the_requested_time(nhl):
    """Finding 7: a revision observed after `as_of` is not in that time's history."""
    from app import coverage
    from test_canonical_markets import CYCLE_AT as CYCLE, run as run_cycle
    conn, _ = nhl
    run_cycle(conn, CYCLE)
    coverage.record(conn, now=CYCLE)
    _settle(conn, hs=4, vs=1, at="2026-10-13T03:00:00Z")
    _settle(conn, hs=1, vs=4, at="2026-10-13T09:00:00Z")
    g = conn.execute("SELECT * FROM games WHERE game_id=?", (GID,)).fetchone()
    c = coverage.canonical(conn, [g], "winner", "2026-10-13T04:00:00Z")[GID]
    assert [h["observed_at"] for h in c["grade_history"]] == ["2026-10-13T03:00:00Z"] and c["regraded"] is False
