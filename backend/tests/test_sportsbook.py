"""The app-versus-sportsbook comparison on a card (app/sportsbook.py): same game, market, side and line only."""
from datetime import date

import pytest

from app import db, ingest, nfl, nfl_live, odds, sportsbook
from app.schemas import NflGameDetail, SportsbookComparison
from test_nfl import nfl_event, nfl_payload
from test_nfl_live import NOW, START, live_store  # noqa: F401  (fixture)
from test_canonical_markets import nhl  # noqa: F401  (fixture)
from test_adaptive import store  # noqa: F401  (fixture)
from test_live_markets import sealed  # noqa: F401  (fixture)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)
from test_research_live import game  # noqa: F401  (fixture)
from test_storage import event

GAME = {"game_id": "nfl:live", "tipoff_at": START}


def _with_moneyline(c, prices=(-150, 130), at="2026-11-02T22:10:00Z"):
    ev = nfl_event(eid="live", date=START, year=2026, status="STATUS_SCHEDULED")
    ev["competitions"][0]["odds"] = event(odds_ml=prices)["competitions"][0]["odds"]
    ingest.ingest_nfl_month(c, "202611", fetch=lambda _: nfl_payload(ev), retrieved_at=at)


def _quote(c, market, line, pa, pb, at="2026-11-02T22:30:00Z"):
    """A saved two-way quote at `line`, de-vigged the way the ingest does."""
    f, r = c.execute("SELECT fetch_id, raw_id FROM market_quotes LIMIT 1").fetchone()
    da, db_ = odds.devig_two_way(pa, pb)
    with db.transaction(c):
        cur = c.execute("INSERT INTO market_quotes(sport,game_id,provider,market,price_label,line,price_a,price_mid,price_b,"
                        "p_a_devig,p_mid_devig,p_b_devig,quote_time,retrieved_at,fetch_id,raw_id) "
                        "VALUES('nfl','nfl:live','Book',?, 'current',?,?,NULL,?,?,NULL,?,?,?,?,?)",
                        (market, line, pa, pb, da, db_, at, at, f, r))
    return cur.lastrowid


def _rec(side="a", line=223.5, source="bookmaker", confidence=0.5, quote_id=None, at="2026-11-02T23:00:00Z"):
    return {"side": side, "line": line, "line_source": source, "confidence": confidence, "market_quote_id": quote_id,
            "generated_at": at}


def test_an_exact_line_gives_the_book_percentage_of_the_picked_side_and_the_gap(live_store):
    c = live_store
    _with_moneyline(c)
    qid = _quote(c, "total", 223.5, -110, -110)
    # pick the over (side a): -110/-110 de-vigs to exactly 0.5; the app says 54.04%
    out = sportsbook.sportsbook_comparison(c, "nfl", GAME, "total", _rec("a", 223.5, confidence=0.5404, quote_id=qid))
    assert isinstance(out, SportsbookComparison) and out.status == "exact"
    assert out.book_probability == 0.5 and out.app_probability == 0.5404
    assert out.gap_pp == 4.0                       # 54.0 - 50.0 from the shown one-decimal numbers
    assert (out.provider, out.price, out.line, out.quote_id) == ("Book", "-110/-110", 223.5, qid)
    # picking the other side uses 1 - p of that side and its own price first
    under = sportsbook.sportsbook_comparison(c, "nfl", GAME, "total", _rec("b", 223.5, confidence=0.4596, quote_id=qid))
    assert under.book_probability == 0.5 and under.gap_pp == -4.0


def test_a_spread_shows_the_picked_teams_own_line(live_store):
    c = live_store
    _with_moneyline(c)
    qid = _quote(c, "spread", 6.5, -110, -110)               # stored as the home line: home +6.5, away -6.5
    home = sportsbook.sportsbook_comparison(c, "nfl", GAME, "spread", _rec("a", 6.5, confidence=0.55, quote_id=qid))
    away = sportsbook.sportsbook_comparison(c, "nfl", GAME, "spread", _rec("b", 6.5, confidence=0.55, quote_id=qid))
    assert (home.line, away.line) == (6.5, -6.5)


def test_a_winner_uses_the_latest_moneyline_at_or_before_the_forecast(live_store):
    c = live_store
    _with_moneyline(c, (-150, 130))
    home = sportsbook.sportsbook_comparison(c, "nfl", GAME, "winner", _rec("a", None, "none", 0.7))
    away = sportsbook.sportsbook_comparison(c, "nfl", GAME, "winner", _rec("b", None, "none", 0.55))
    h, a = odds.devig_two_way(-150, 130)
    assert home.status == "exact" and home.book_probability == pytest.approx(h) and home.price == "-150/+130"
    assert away.book_probability == pytest.approx(a) and away.price == "+130/-150"
    late = sportsbook.sportsbook_comparison(c, "nfl", GAME, "winner", _rec("a", None, "none", 0.7, at="2026-11-02T22:00:00Z"))
    assert late.status == "no_quote" and late.book_probability is None and late.gap_pp is None


def test_a_quote_at_another_line_is_never_used(live_store):
    c = live_store
    _with_moneyline(c)
    _quote(c, "total", 224.5, -130, 110)
    out = sportsbook.sportsbook_comparison(c, "nfl", GAME, "total", _rec("a", 223.5, confidence=0.6))
    assert out.status == "no_quote" and out.book_probability is None and out.gap_pp is None and out.reason
    # even when the forecast points at that other line's quote id
    other = c.execute("SELECT quote_id FROM market_quotes WHERE line=224.5").fetchone()[0]
    out = sportsbook.sportsbook_comparison(c, "nfl", GAME, "total", _rec("a", 223.5, confidence=0.6, quote_id=other))
    assert out.status == "no_quote" and out.book_probability is None


def test_a_quote_retrieved_after_the_forecast_is_not_used(live_store):
    c = live_store
    _with_moneyline(c)
    _quote(c, "total", 223.5, -110, -110, at="2026-11-02T23:30:00Z")
    assert sportsbook.sportsbook_comparison(c, "nfl", GAME, "total", _rec("a", 223.5, confidence=0.6)).status == "no_quote"


def test_a_model_line_has_nothing_to_compare(live_store):
    c = live_store
    out = sportsbook.sportsbook_comparison(c, "nfl", GAME, "spread", _rec("a", -3.5, "model", 0.58))
    assert out.status == "model_line" and out.book_probability is None and out.gap_pp is None and out.app_probability == 0.58


def test_team_totals_and_regulation_three_way_have_no_quote_source(live_store):
    c = live_store
    for market in ("team_total_home", "team_total_away", "reg3"):
        out = sportsbook.sportsbook_comparison(c, "nfl", GAME, market, _rec("a", 20.5, "bookmaker", 0.6))
        assert out.status == "unsupported" and out.book_probability is None


def test_nfl_games_and_detail_carry_the_comparison(live_store, monkeypatch):
    c = live_store
    _with_moneyline(c, (-110, -110))
    assert nfl_live.record(c, now=NOW)["recorded"] == 5
    monkeypatch.setattr(db, "utc_now", lambda: NOW)
    game = nfl.games_on(c, date(2026, 11, 2))["games"][0]
    by = {f["market"]: f for f in game["forecasts"]}
    assert by["winner"]["sportsbook"]["status"] == "exact" and by["winner"]["sportsbook"]["book_probability"] == 0.5
    assert by["spread"]["sportsbook"]["status"] == "model_line"
    assert by["team_total_home"]["sportsbook"]["status"] == "unsupported"
    detail = NflGameDetail(**nfl.game_detail(c, "nfl:live"))
    assert {f.market: f.sportsbook.status for f in detail.game.forecasts}["winner"] == "exact"


def test_nhl_game_detail_carries_a_comparison_per_market(nhl):
    from app import coverage
    from test_canonical_markets import CYCLE_AT, GID
    from test_research_live import run
    conn, client = nhl
    run(conn, CYCLE_AT)
    coverage.record(conn, now=CYCLE_AT)
    conn.commit()
    detail = client.get(f"/api/games/{GID}", params={"sport": "nhl"}).json()
    got = {c["market"]: c["sportsbook"] for c in detail["canonical_markets"]}
    assert got["reg3"]["status"] == "unsupported" and got["team_total_home"]["status"] == "unsupported"
    w = got["winner"]                  # the synthetic store saved a moneyline before the forecast
    assert w["status"] == "exact" and 0 < w["book_probability"] < 1 and w["provider"]
    assert w["gap_pp"] == round(round(w["app_probability"] * 100, 1) - round(w["book_probability"] * 100, 1), 1)
    assert all(v["status"] != "exact" or v["book_probability"] is not None for v in got.values())
    assert all(v["app_probability"] > 0 for v in got.values())
