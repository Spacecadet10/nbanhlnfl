"""GET /api/pick-colours (app/pick_colours.py) on the synthetic stores of all five sports. No network, no live data."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import coverage, pick_colours as pc, pick_snapshots as ps, threshold_overview as to
from test_canonical import AFTER, G1, record_now
from test_live import T_GAME, client, event, ingest_events, store  # noqa: F401  (fixtures)
from test_near_tipoff import NOW, ready  # noqa: F401  (fixture)
from test_nfl_live import NOW as NFL_NOW, live_store  # noqa: F401  (fixture)
from test_pick_snapshots import START, T, hist, pick, rep
from test_adaptive import CYCLE_AT  # noqa: F401
from test_canonical_markets import AFTER as NHL_AFTER, NHL_MARKETS
from test_canonical_markets import nhl  # noqa: F401  (fixture)
from test_coverage import _settle
from test_live_markets import add_upcoming, sealed  # noqa: F401  (fixture)
from test_ncaab_app_delivery import NOW as NCAAB_NOW
from test_ncaab_app_delivery import store as ncaab_store  # noqa: F401  (fixture)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)
from test_research_live import game, run  # noqa: F401  (fixture)
from test_soccer_live import TOMORROW, live  # noqa: F401  (fixture)
from test_soccer_record import MATCH, forecast, settle, stamp  # noqa: F401  (fixture)



def test_the_filters_narrow_rows_and_totals_but_never_the_reconciliation(monkeypatch):
    ps.observe("nba", rep(pick("g1"), pick("g2", fid=2, pct=0.57, h=hist(pct="57.0", w=0, l=2)), pick("g3", fid=3)), T)
    from test_pick_snapshots import item
    items = [item("g1"), item("g2", fid="2", grade="loss", pct="57.0"), item("g3", fid="3", grade="push")]
    built = {"as_of": "2026-11-26T05:00:00Z", "ledger_rows": 0, "views": ps.classify("nba", items, ps.load("nba"), [], lambda s, i: i["grade"]), "recovered_only": [], "upcoming": 0,
             "tracking_start": "2026-11-25T20:00:00Z", "backfilled_from": None}
    monkeypatch.setattr(pc, "build", lambda sport, now: built)
    full = pc.report(sport="nba")
    assert full["summary"]["live"]["green"]["predictions"] == 2 and full["summary"]["live"]["red"]["loss"] == 1
    green = pc.report(sport="nba", colour="green")
    assert [r["game_id"] for r in green["rows"]] == ["g3", "g1"] and green["summary"] == full["summary"]   # colour filters the list only
    one = pc.report(sport="nba", percentage="57.0")
    assert one["summary"]["total"]["predictions"] == 1 and one["rows"][0]["game_id"] == "g2"
    assert pc.report(sport="nba", market="total")["summary"]["total"]["predictions"] == 0
    assert pc.report(sport="nba", date_from="2026-11-26")["summary"]["total"]["predictions"] == 0
    for r in (full, green, one):
        assert r["reconciliation"]["live_forecasts"]["predictions"] == 3 and r["reconciliation"]["adds_up"] is True
    assert pc.report(sport="nba", limit=1)["rows_total"] == 3 and len(pc.report(sport="nba", limit=1)["rows"]) == 1
    assert {b["market"]: b["groups"]["green"]["predictions"] for b in full["by_market"]} == {"winner": 2}
    assert [(b["percentage"], b["all"]["predictions"]) for b in full["by_percentage"]] == [("60.0", 2), ("57.0", 1)]


def at_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def serve(monkeypatch, conn, sport):
    """The report reads the fixture's own store file (a fresh read-only connection, as in production)."""
    conn.commit()
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    real = pc.db.connect

    def readonly(s):
        c = sqlite3.connect(path, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only = ON")
        return c
    from pathlib import Path
    monkeypatch.setattr(pc.db, "connect_readonly", readonly)
    monkeypatch.setattr(pc.config, "db_path", lambda s="nba": Path(path) if s == sport else Path("/nonexistent"))


def check(sport, report, markets, now_dt, settle, after, monkeypatch, conn, grades=("win", "loss", "push")):
    """Save a real report's picks, settle the game, and read the report: every market keeps its saved colour and is graded, and a
    second look after the result changes nothing that was saved."""
    for p in report["picks"]:
        h = p["percentage_history"]
        h = h.model_dump(mode="json") if hasattr(h, "model_dump") else dict(h)
        p["percentage_history"] = {**h, "wins": 3, "losses": 0}                # a green bucket at this moment
    serve(monkeypatch, conn, sport)
    assert {p["market"] for p in report["picks"]} == set(markets)
    assert ps.observe(sport, {**report, "sport": sport}, now_dt, store=conn) == len(report["picks"])
    saved = ps.load(sport)
    settle()
    conn.commit()
    r = pc.report(sport=sport, now=after)
    rows = {(x["game_id"], x["market"]): x for x in r["rows"]}
    for s in saved:
        x = rows[(s["game_id"], s["market"])]
        assert (x["colour"], x["support"]["wins"], x["side"], x["line"], x["percentage"]) == ("green", 3, s["side"], s["line"], s["percentage"])
        assert x["grade"] in grades, (x["market"], x["grade"])
    assert r["summary"]["live"]["green"]["predictions"] == len(saved) and r["reconciliation"]["adds_up"] is True
    assert {m["market"] for m in r["by_market"]} >= set(markets)
    late = {**report, "sport": sport, "picks": [{**p, "percentage_history": {**p["percentage_history"], "losses": 99}} for p in report["picks"]]}
    assert ps.observe(sport, late, at_dt(after), store=conn) == 0                # started: nothing more is ever written
    assert ps.load(sport) == saved
    return r


def test_nba_every_market(ready, client, monkeypatch):  # noqa: F811
    from app.providers.real import RealProvider
    from app import sports
    record_now(ready)
    rep_ = RealProvider(ready, NOW).ranked_picks(datetime(2026, 10, 21).date())
    r = check("nba", rep_, sports.get("nba").markets if len(rep_["picks"]) > 1 else {p["market"] for p in rep_["picks"]}, at_dt(NOW),
              lambda: ingest_events(ready, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z"), AFTER,
              monkeypatch, ready)
    canon = client
    canon.now = AFTER
    h = canon.get("/api/canonical/forecasts").json()["rows"]
    want = next(x for x in h if x["game_id"] == G1)["canonical"]["grade"]
    assert next(x for x in r["rows"] if x["market"] == "winner")["grade"] == want


def test_nhl_every_market_including_the_three_way(nhl, monkeypatch):  # noqa: F811
    from app.providers.real import RealProvider
    conn, _ = nhl
    run(conn, CYCLE_AT)
    coverage.record(conn, now=CYCLE_AT)
    conn.commit()
    rep_ = RealProvider(conn, CYCLE_AT).ranked_picks(datetime(2026, 10, 12).date())
    check("nhl", rep_, NHL_MARKETS, at_dt(CYCLE_AT), lambda: _settle(conn, hs=4, vs=1), NHL_AFTER, monkeypatch, conn)


def test_ncaab_every_market(ncaab_store, monkeypatch):  # noqa: F811
    from app.providers.real import RealProvider
    from ncaab_support import GAME
    rep_ = RealProvider(ncaab_store, NCAAB_NOW).ranked_picks(datetime(2026, 11, 17).date())

    def settle():
        ncaab_store.execute("UPDATE games SET status='final', home_score=80, away_score=70, result_available_at='2026-11-18T03:00:00Z', "
                            "result_availability_basis='observed_retrieval' WHERE game_id=?", (GAME,))
    r = check("ncaab", rep_, ("winner", "spread", "total", "team_total_home", "team_total_away"), at_dt(NCAAB_NOW), settle,
              "2026-11-18T05:00:00Z", monkeypatch, ncaab_store)
    priced = {x["market"]: x["price"] for x in r["rows"]}
    assert priced["winner"] is not None and priced["team_total_home"] is None        # a stored moneyline; no team-total prices exist


def test_nfl_every_market_and_a_tie_is_void(live_store, monkeypatch):  # noqa: F811
    from app import nfl_live, record_basis, sports
    from app.nfl_live import Views
    nfl_live.record(live_store, now=NFL_NOW)
    rep_ = Views(live_store, NFL_NOW).ranked_picks(datetime(2026, 11, 2).date())
    record_basis.decorate_report(live_store, "nfl", rep_, {})

    def settle():
        live_store.execute("UPDATE games SET status='final', home_score=20, away_score=20, result_available_at='2026-11-03T04:00:00Z' "
                           "WHERE game_id='nfl:live'")
    r = check("nfl", rep_, sports.NFL.markets, at_dt(NFL_NOW), settle, "2026-11-03T05:00:00Z", monkeypatch, live_store,
              grades=("win", "loss", "push", "void"))
    assert next(x for x in r["rows"] if x["market"] == "winner")["grade"] == "void"
    assert pc.report(sport="nfl", now="2026-11-03T03:59:59Z")["summary"]["total"]["unsettled"] == 5      # the result is not available yet


def test_soccer_every_market_shown_and_a_draw_no_bet_draw_is_a_push(forecast, monkeypatch):  # noqa: F811
    from app import soccer_record
    s = stamp()
    kick = forecast.execute("SELECT kickoff_at FROM soccer_matches WHERE match_id=?", (MATCH,)).fetchone()[0]
    picks = [p for p in soccer_record.all_picks(forecast, s) if p["game_id"] == MATCH]
    soccer_record.attach(forecast, picks, s, published={})
    after = (at_dt(kick) + timedelta(hours=4)).strftime(ps.FMT)
    r = check("soccer", {"sport": "soccer", "as_of": s, "picks": picks}, {p["market"] for p in picks}, at_dt(s),
              lambda: settle(forecast, 1, 1), after, monkeypatch, forecast, grades=("win", "loss", "push", "pending", "half_win", "half_loss"))
    assert next(x for x in r["rows"] if x["market"] == "draw_no_bet")["grade"] == "push"
    assert r["summary"]["live"]["green"]["push"] >= 1


def test_a_forecast_that_is_not_the_saved_pick_is_unscorable_never_a_win(ready, monkeypatch):  # noqa: F811
    record_now(ready)
    f = ready.execute("SELECT * FROM game_forecasts WHERE market='winner'").fetchone()
    other = "b" if coverage.pick_side("winner", (f["p_a"], f["p_mid"], f["p_b"])) == "a" else "a"
    go = pc._regrade(ready, "nba", AFTER)
    row = {"game_id": f["game_id"], "market": "winner", "forecast_id": str(f["forecast_id"]), "line": None}
    assert go({**row, "side": other}, None) == "unscorable"
    assert go({**row, "side": {"a": "b", "b": "a"}[other], "line": 3.5}, None) == "unscorable"


def test_the_endpoint_validates_its_filters_and_serves_every_sport(store, client):  # noqa: F811
    assert client.get("/api/pick-colours?sport=cricket").status_code == 422
    assert client.get("/api/pick-colours?sport=nba&view=best").status_code == 422
    assert client.get("/api/pick-colours?sport=nba&date_from=2026-11-25&date_to=2026-11-24").status_code == 422
    assert client.get("/api/pick-colours?sport=nba&market=corners_total").status_code == 404
    body = client.get("/api/pick-colours?sport=nba").json()
    assert body["sport"] == "nba" and body["tracking_start"] is None and body["reconciliation"]["adds_up"] is True
    both = client.get("/api/pick-colours?sport=all").json()
    assert {s["sport"] for s in both["sports"]} == {"nba", "nhl", "nfl", "ncaab", "soccer"}
    assert any(s["status"] == "no_store" for s in both["sports"])        # a missing store is named, never silently zero
