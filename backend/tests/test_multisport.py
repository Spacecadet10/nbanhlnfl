"""Multi-sport stores, NHL ingestion, line quotes, market ledger and settlement (synthetic payloads, no network)."""
import json
import sqlite3

import pytest

from app import db, ingest, ledger, markets
from app.sources import espn, nhl_stats
from test_storage import event, payload

T0 = "2024-01-15T12:00:00Z"
T1 = "2024-01-16T02:00:00Z"
T2 = "2024-01-16T06:00:00Z"
T3 = "2024-01-16T10:00:00Z"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))

    def make(name="s.sqlite"):
        c = db.connect(tmp_path / name)
        db.migrate(c)
        return c
    return make


def nba_month(c, body, at):
    return ingest.ingest_month(c, "202401", fetch=lambda _m: body, retrieved_at=at)


# --- NHL fixtures ---------------------------------------------------------------------------------------------
TEAMS = json.dumps({"data": [{"id": 6, "triCode": "BOS", "fullName": "Boston Bruins"},
                             {"id": 16, "triCode": "CHI", "fullName": "Chicago Blackhawks"},
                             {"id": 8, "triCode": "MTL", "fullName": "Montréal Canadiens"}]}).encode()


def nhl_game(gid=2024020001, start="2024-10-11T19:00:00", gtype=2, state=7, sched=1, home=6, away=16, hs=3, vs=2,
             period=3):
    return {"id": gid, "easternStartTime": start, "gameDate": start[:10], "gameScheduleStateId": sched,
            "gameStateId": state, "gameType": gtype, "homeScore": hs, "homeTeamId": home, "period": period,
            "season": 20242025, "visitingScore": vs, "visitingTeamId": away}


def nhl_season(*games) -> bytes:
    return json.dumps({"data": list(games)}, sort_keys=True).encode()


def nhl_ingest(c, body, at, season=2025):
    return ingest.ingest_nhl_season(c, season, fetch=lambda _s: body, retrieved_at=at)


# --- Store identity -------------------------------------------------------------------------------------------
def test_store_is_claimed_once_and_other_sport_ingestion_is_refused(store):
    c = store()
    assert db.store_sport(c) is None
    nba_month(c, payload(event()), T0)
    assert db.store_sport(c) == "nba"
    with pytest.raises(db.StoreMismatch):
        nhl_ingest(c, nhl_season(nhl_game()), T1)
    assert c.execute("SELECT count(*) FROM games WHERE game_id LIKE 'nhl:%'").fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError, match="set once"):
        c.execute("INSERT INTO store_identity VALUES ('nhl', ?)", (T1,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        c.execute("UPDATE store_identity SET sport='nhl'")


def test_quotes_and_forecasts_of_the_other_sport_are_rejected_by_the_store(store):
    c = store()
    ingest.ingest_nhl_teams(c, fetch=lambda: TEAMS, retrieved_at=T0)
    nhl_ingest(c, nhl_season(nhl_game(state=1, hs=0, vs=0, period=1, start="2024-10-12T19:00:00")), T0)
    f = _forecast(sport="nba", game_id="nhl:2024020001", market="total", line=5.5, p=(0.5, 0, 0.5))
    with pytest.raises(sqlite3.IntegrityError, match="does not match this store"):
        ledger.record_market(c, f)


# --- Revisions: A -> B -> A ----------------------------------------------------------------------------------
def test_identical_content_returning_after_a_correction_is_applied_again(store):
    """Regression for the importer that read the time of the bytes' FIRST retrieval: the third response (identical to
    the first) looked older than the correction and was ignored, leaving the corrected-away value in place."""
    c = store()
    a = payload(event(status="STATUS_FINAL", home=("20", "PHI", "124")))
    b = payload(event(status="STATUS_FINAL", home=("20", "PHI", "125")))
    nba_month(c, a, T1)
    nba_month(c, b, T2)
    assert c.execute("SELECT home_score FROM games").fetchone()[0] == 125
    nba_month(c, a, T3)
    assert c.execute("SELECT home_score FROM games").fetchone()[0] == 124
    assert c.execute("SELECT count(*) FROM raw_responses").fetchone()[0] == 2
    assert c.execute("SELECT count(*) FROM raw_fetches").fetchone()[0] == 3

    # The old behaviour, reproduced in memory on this store: importing the returning bytes at their first retrieval
    # time is ignored as stale, so the correction would have stuck.
    nba_month(c, b, "2024-01-16T12:00:00Z")
    raw_a = c.execute("SELECT raw_id FROM raw_responses ORDER BY raw_id LIMIT 1").fetchone()[0]
    with db.transaction(c):
        ingest.import_raw(c, raw_a)        # no retrieved_at: the old first-retrieval semantics
    assert c.execute("SELECT home_score FROM games").fetchone()[0] == 125


def test_replay_of_a_revision_sequence_is_deterministic(store):
    c = store("live.sqlite")
    a = payload(event(status="STATUS_FINAL", home=("20", "PHI", "124")))
    b = payload(event(status="STATUS_FINAL", home=("20", "PHI", "125")))
    for body, at in ((a, T1), (b, T2), (a, T3)):
        nba_month(c, body, at)
    fresh = store("fresh.sqlite")
    for t in ("raw_responses", "raw_fetches"):
        for r in c.execute(f"SELECT * FROM {t} ORDER BY 1"):
            fresh.execute(f"INSERT INTO {t} VALUES ({', '.join('?' * len(r))})", tuple(r))
    ingest.replay(fresh)
    cols = "game_id, status, home_score, away_score, result_available_at, updated_at"
    assert fresh.execute(f"SELECT {cols} FROM games").fetchall() == c.execute(f"SELECT {cols} FROM games").fetchall()
    assert db.store_sport(fresh) == "nba"


# --- NHL ingestion --------------------------------------------------------------------------------------------
def test_nhl_season_import_decisions_times_and_unplayed_games(store):
    c = store()
    ingest.ingest_nhl_teams(c, fetch=lambda: TEAMS, retrieved_at=T0)
    body = nhl_season(
        nhl_game(2024020001, period=3, hs=3, vs=2),
        nhl_game(2024020002, start="2024-10-12T22:00:00", period=4, hs=2, vs=3),
        nhl_game(2024020003, start="2024-10-13T13:00:00", period=5, hs=4, vs=3),
        nhl_game(2024030417, start="2025-06-20T20:00:00", gtype=3, state=1, sched=2, hs=0, vs=0, period=1),
        nhl_game(2024030111, start="2025-04-21T19:00:00", gtype=3, period=6, hs=1, vs=0),
        nhl_game(2024010001, start="2024-09-21T19:00:00", gtype=1, state=6),
        nhl_game(2024190001, start="2025-02-12T20:00:00", gtype=19, home=8, away=6),
        nhl_game(2025020999, start="2025-10-20T19:00:00", state=1, hs=0, vs=0, period=1, home=99, away=6),
    )
    stats = nhl_ingest(c, body, "2025-07-01T12:00:00Z")
    rows = {r["game_id"]: dict(r) for r in c.execute(
        "SELECT g.*, d.decision FROM games g LEFT JOIN game_decisions d USING (game_id)")}
    assert rows["nhl:2024020001"]["tipoff_at"] == "2024-10-11T23:00:00Z"            # 19:00 EDT
    assert rows["nhl:2024020001"]["decision"] == "REG"
    assert rows["nhl:2024020002"]["decision"] == "OT"
    assert (rows["nhl:2024020003"]["decision"], rows["nhl:2024020003"]["home_score"]) == ("SO", 4)
    assert rows["nhl:2024030111"]["decision"] == "OT"                               # playoff double overtime
    assert rows["nhl:2024030417"]["status"] == "canceled"                           # 'if necessary', never played
    assert rows["nhl:2024010001"]["season_type"] == "preseason"
    assert rows["nhl:2024190001"]["season_type"] == "other"
    assert rows["nhl:2025020999"]["status"] == "scheduled" and rows["nhl:2025020999"]["season"] == 2025
    assert c.execute("SELECT name FROM teams WHERE team_id='nhl:99'").fetchone()[0] == "NHL team 99"  # placeholder
    assert stats["placeholder_teams"] == 1
    assert c.execute("SELECT name FROM teams WHERE team_id='nhl:8'").fetchone()[0] == "Montréal Canadiens"
    assert all(r["result_availability_basis"] == "assumed_tipoff_plus_4h"
               for r in rows.values() if r["status"] == "final")


def test_nhl_reimport_is_idempotent_and_replay_rebuilds_the_same_store(store):
    c = store("nhl.sqlite")
    ingest.ingest_nhl_teams(c, fetch=lambda: TEAMS, retrieved_at=T0)
    sched = nhl_season(nhl_game(state=1, hs=0, vs=0, period=1, start="2024-10-11T19:00:00"))
    final = nhl_season(nhl_game(period=5, hs=3, vs=2))
    nhl_ingest(c, sched, "2024-10-11T12:00:00Z")
    nhl_ingest(c, final, "2024-10-12T02:00:00Z")
    again = nhl_ingest(c, final, "2024-10-12T03:00:00Z")
    assert again["games_inserted"] == again["games_updated"] == 0
    g = c.execute("SELECT * FROM games").fetchone()
    assert g["result_availability_basis"] == "observed_retrieval" and g["result_available_at"] == "2024-10-12T02:00:00Z"
    fresh = store("fresh.sqlite")
    for t in ("raw_responses", "raw_fetches"):
        for r in c.execute(f"SELECT * FROM {t} ORDER BY 1"):
            fresh.execute(f"INSERT INTO {t} VALUES ({', '.join('?' * len(r))})", tuple(r))
    ingest.replay(fresh)
    q = "SELECT g.*, d.decision, d.periods FROM games g JOIN game_decisions d USING (game_id)"
    assert [dict(r) for r in fresh.execute(q)] == [dict(r) for r in c.execute(q)]
    assert db.store_sport(fresh) == "nhl"


def test_same_numeric_ids_in_both_sports_cannot_collide(store):
    nba, nhl = store("nba.sqlite"), store("nhl.sqlite")
    nba_month(nba, payload(event(eid="2024020001")), T0)
    ingest.ingest_nhl_teams(nhl, fetch=lambda: TEAMS, retrieved_at=T0)
    nhl_ingest(nhl, nhl_season(nhl_game(2024020001)), T1)
    assert nba.execute("SELECT game_id FROM games").fetchall()[0][0] == "espn:2024020001"
    assert nhl.execute("SELECT game_id FROM games").fetchall()[0][0] == "nhl:2024020001"


# --- Line quotes -----------------------------------------------------------------------------------------------
def lines_event(eid="1001", date="2024-01-15T18:00Z", home="Team PHI", away="Team HOU", spread=("-4.5", "+4.5"),
                total=("o221.5", "u221.5"), sport_ids=("20", "10")):
    e = event(eid=eid, date=date)
    comp = e["competitions"][0]
    comp["competitors"][0]["team"].update(id=sport_ids[0], displayName=home)
    comp["competitors"][1]["team"].update(id=sport_ids[1], displayName=away)
    comp["odds"] = [{"provider": {"name": "DraftKings"},
                     "moneyline": {"home": {"open": {"odds": "-170"}, "close": {"odds": "-180"}},
                                   "away": {"open": {"odds": "+142"}, "close": {"odds": "+150"}}},
                     "pointSpread": {"home": {"close": {"line": spread[0], "odds": "-110"}},
                                     "away": {"close": {"line": spread[1], "odds": "-110"}}},
                     "total": {"over": {"close": {"line": total[0], "odds": "-105"}},
                               "under": {"close": {"line": total[1], "odds": "-115"}}}}]
    return e


def test_line_quotes_keep_each_retrieval_and_skip_inconsistent_pairs(store):
    c = store()
    a = payload(lines_event())
    b = payload(lines_event(spread=("-5.5", "+5.5")))
    for body, at in ((a, T0), (b, "2024-01-15T13:00:00Z"), (a, "2024-01-15T14:00:00Z")):
        nba_month(c, body, at)
    spreads = c.execute("SELECT line, retrieved_at FROM market_quotes WHERE market='spread' ORDER BY retrieved_at").fetchall()
    assert [tuple(r) for r in spreads] == [(-4.5, T0), (-5.5, "2024-01-15T13:00:00Z"), (-4.5, "2024-01-15T14:00:00Z")]
    q = c.execute("SELECT * FROM market_quotes WHERE market='total' LIMIT 1").fetchone()
    assert (q["line"], q["price_a"], q["price_b"]) == (221.5, -105, -115)
    assert abs(q["p_a_devig"] + q["p_b_devig"] - 1) < 1e-9 and q["quote_time"] is None
    assert ingest.replay(c)["line_quotes"] == 0                     # replay adds nothing
    bad = nba_month(c, payload(lines_event(spread=("-4.5", "+5.5"), total=("o221.5", "u220.5"))), "2024-01-15T15:00:00Z")
    assert bad["lines"]["inconsistent_line"] == 2
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        c.execute("UPDATE market_quotes SET line = 0")


def test_nhl_line_quotes_match_games_by_time_and_names_only(store):
    c = store()
    ingest.ingest_nhl_teams(c, fetch=lambda: TEAMS, retrieved_at=T0)
    nhl_ingest(c, nhl_season(nhl_game(state=1, hs=0, vs=0, period=1, start="2024-10-11T19:00:00", home=8, away=6)), T0)
    body = payload(lines_event(eid="401", date="2024-10-11T23:00Z", home="Montreal Canadiens", away="Boston Bruins",
                               spread=("+1.5", "-1.5"), total=("o6.5", "u6.5")),
                   lines_event(eid="402", date="2024-10-11T23:00Z", home="Boston Bruins", away="Chicago Blackhawks"))
    out = ingest.ingest_nhl_day(c, "20241011", fetch=lambda _d: body, retrieved_at="2024-10-11T20:00:00Z")
    assert out["unmatched"] == 4 and out["inserted"] == 4    # moneyline open+current, spread and total current; per game
    assert {r[0] for r in c.execute("SELECT DISTINCT game_id FROM market_quotes")} == {"nhl:2024020001"}


# --- Market ledger ---------------------------------------------------------------------------------------------
def _forecast(sport="nba", game_id="espn:1001", market="spread", line=-4.5, p=(0.55, 0.0, 0.45), policy="pregame",
              run_type="live", generated="2024-01-15T04:00:00Z", cutoff="2024-01-15T05:00:00Z", quote=None):
    return ledger.MarketForecast(sport, game_id, market, line, None if line is None else ("market" if quote else "model"),
                                 quote, policy, run_type, "m1", "d1", "f1", cutoff, generated, "2024-01-15T18:00:00Z", *p)


def test_market_ledger_is_append_only_idempotent_and_line_specific(store):
    c = store()
    nba_month(c, payload(lines_event()), T0)
    fid, created = ledger.record_market(c, _forecast())
    assert created and ledger.record_market(c, _forecast()) == (fid, False)
    with pytest.raises(ledger.LedgerConflict):
        ledger.record_market(c, _forecast(p=(0.6, 0.0, 0.4)))
    other, created = ledger.record_market(c, _forecast(line=-5.5, p=(0.5, 0.0, 0.5)))
    assert created and other != fid
    row = dict(c.execute("SELECT * FROM market_forecasts WHERE forecast_id=?", (fid,)).fetchone())
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE market_forecasts SET p_a = 0.9")
    c.execute("PRAGMA recursive_triggers = OFF")
    with pytest.raises(sqlite3.IntegrityError, match="conflicting insert"):
        c.execute(f"INSERT OR REPLACE INTO market_forecasts ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                  [*{**row, "p_a": 0.9, "p_b": 0.1}.values()])
    assert c.execute("SELECT p_a FROM market_forecasts WHERE forecast_id=?", (fid,)).fetchone()[0] == 0.55


def test_market_ledger_rejects_bad_vectors_lines_and_late_forecasts(store):
    c = store()
    nba_month(c, payload(lines_event()), T0)
    with pytest.raises(sqlite3.IntegrityError):
        ledger.record_market(c, _forecast(p=(0.6, 0.0, 0.6)))                     # does not sum to 1
    with pytest.raises(sqlite3.IntegrityError):
        ledger.record_market(c, _forecast(market="reg3", line=None, p=(0.4, 0.2, 0.4)))   # NHL-only market
    with pytest.raises(sqlite3.IntegrityError, match="before cutoff"):
        ledger.record_market(c, _forecast(generated="2024-01-15T06:00:00Z"))
    with pytest.raises(sqlite3.IntegrityError, match="before cutoff"):
        ledger.record_market(c, _forecast(cutoff="2024-01-15T19:00:00Z", generated="2024-01-15T18:30:00Z"))
    quote = c.execute("SELECT quote_id FROM market_quotes WHERE market='spread' AND price_label='current'").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="quote does not match"):
        ledger.record_market(c, _forecast(line=-5.5, quote=quote, generated="2024-01-15T13:00:00Z",
                                          cutoff="2024-01-15T17:00:00Z"))
    with pytest.raises(sqlite3.IntegrityError, match="quote does not match"):     # quote retrieved after generation
        ledger.record_market(c, _forecast(line=-4.5, quote=quote, generated="2024-01-15T11:00:00Z",
                                          cutoff="2024-01-15T17:00:00Z"))
    fid, _ = ledger.record_market(c, _forecast(line=-4.5, quote=quote, generated="2024-01-15T13:00:00Z",
                                               cutoff="2024-01-15T17:00:00Z"))
    assert fid


# --- Settlement and distributions ---------------------------------------------------------------------------
@pytest.mark.parametrize("market,line,score,decision,expected", [
    ("winner", None, (3, 2), "SO", "a"),
    ("reg3", None, (3, 2), "SO", "mid"),
    ("reg3", None, (2, 3), "REG", "b"),
    ("reg3", None, (3, 2), None, "unscorable"),
    ("spread", -1.5, (3, 2), "SO", "b"),          # SO win by the credited goal does not cover -1.5
    ("spread", -1.0, (3, 2), "OT", "mid"),
    ("spread", +1.5, (2, 3), "REG", "a"),
    ("total", 5.5, (3, 2), "SO", "b"),
    ("total", 5.0, (3, 2), "SO", "mid"),
    ("team_total_home", 2.5, (3, 2), "REG", "a"),
    ("team_total_away", 2.0, (3, 2), "REG", "mid"),
])
def test_settlement(market, line, score, decision, expected):
    assert markets.settle(market, line, status="final", home=score[0], away=score[1], decision=decision) == expected


def test_settlement_pending_void_and_moved_games():
    assert markets.settle("total", 5.5, status="scheduled", home=None, away=None) == "pending"
    assert markets.settle("total", 5.5, status="postponed", home=None, away=None) == "void"
    assert markets.settle("total", 5.5, status="canceled", home=None, away=None) == "void"
    assert markets.settle("total", 5.5, status="final", home=3, away=2, tipoff_at="2024-01-17T00:00:00Z",
                          forecast_tipoff_at="2024-01-15T00:00:00Z") == "void"
    assert markets.settle("total", 5.5, status="final", home=3, away=2, tipoff_at="2024-01-15T03:00:00Z",
                          forecast_tipoff_at="2024-01-15T00:00:00Z") == "b"


@pytest.mark.parametrize("dist", [markets.poisson_grid(3.2, 2.7, 0.52),
                                  markets.MarginTotalNormal(3.0, 12.5, 224.0, 18.0)])
def test_distributions_are_coherent(dist):
    lines = [x / 2 for x in range(-40, 41)]
    markets.check_consistency(dist, "spread", lines)
    base = 6 if isinstance(dist, markets.ScoreGrid) else 224
    markets.check_consistency(dist, "total", [base + x / 2 for x in range(-20, 21)])
    markets.check_consistency(dist, "team_total_home", [base / 2 + x / 2 for x in range(-10, 11)])
    pa, pmid, pb = dist.winner()
    assert pmid == 0 and abs(pa + pb - 1) < 1e-12 and pa > 0.5            # home favoured in both fixtures
    # the winner equals covering a 0.5-point handicap in a game that cannot end level
    assert abs(dist.market("spread", -0.5)[0] - pa) < 1e-9
    # integer lines carry a push, half lines never do
    assert dist.market("total", base)[1] > 0 and dist.market("total", base + 0.5)[1] == 0
    for market in ("spread", "total", "team_total_home", "team_total_away"):
        line = markets.model_line(dist, market)
        assert line % 1 == 0.5 and dist.market(market, line)[1] == 0


def test_nhl_regulation_three_way_is_consistent_with_the_winner():
    g = markets.poisson_grid(3.0, 3.0, 0.5)
    home, draw, away = g.reg3()
    assert abs(home - away) < 1e-12 and 0.15 < draw < 0.3 and abs(home + draw + away - 1) < 1e-12
    w = g.winner()[0]
    assert abs(w - (home + 0.5 * draw)) < 1e-9       # winner = regulation win + OT/SO share of draws
