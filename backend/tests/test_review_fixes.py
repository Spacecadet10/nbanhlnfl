"""Regressions for the independent review of the multi-sport foundations (commit 4d28c3f)."""
import sqlite3

import pytest

from app import db, ingest, ledger
from app.sources import nhl_stats
from test_multisport import TEAMS, _forecast, lines_event, nba_month, nhl_game, nhl_ingest, nhl_season, store  # noqa: F401
from test_storage import payload


def _copy_cache(src, dst):
    for t in ("raw_responses", "raw_fetches"):
        for r in src.execute(f"SELECT * FROM {t} ORDER BY 1"):
            dst.execute(f"INSERT INTO {t} VALUES ({', '.join('?' * len(r))})", tuple(r))


def test_replay_matches_nhl_quotes_as_live_ingestion_did(store):
    """Finding 1: quotes matched live against the game's original start must survive replay after the game moves."""
    c = store("live.sqlite")
    ingest.ingest_nhl_teams(c, fetch=lambda: TEAMS, retrieved_at="2024-10-01T00:00:00Z")
    nhl_ingest(c, nhl_season(nhl_game(state=1, hs=0, vs=0, period=1, start="2024-10-11T19:00:00", home=8, away=6)),
               "2024-10-02T00:00:00Z")
    body = payload(lines_event(eid="401", date="2024-10-11T23:00Z", home="Montreal Canadiens", away="Boston Bruins"))
    assert ingest.ingest_nhl_day(c, "20241011", fetch=lambda _d: body, retrieved_at="2024-10-11T20:00:00Z")["inserted"] == 4
    nhl_ingest(c, nhl_season(nhl_game(state=1, hs=0, vs=0, period=1, start="2024-10-20T19:00:00", home=8, away=6)),
               "2024-10-12T00:00:00Z")
    fresh = store("fresh.sqlite")
    _copy_cache(c, fresh)
    ingest.replay(fresh)
    cols = "quote_id, game_id, market, price_label, line, fetch_id, retrieved_at"
    assert fresh.execute(f"SELECT {cols} FROM market_quotes ORDER BY 1").fetchall() == \
        c.execute(f"SELECT {cols} FROM market_quotes ORDER BY 1").fetchall()


def test_backtest_forecasts_cannot_use_quotes_retrieved_after_the_cutoff(store):
    """Finding 2."""
    c = store()
    nba_month(c, payload(lines_event()), "2024-01-15T12:00:00Z")
    quote = c.execute("SELECT quote_id FROM market_quotes WHERE market='spread'").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="quote does not match"):
        ledger.record_market(c, _forecast(run_type="backtest", quote=quote, cutoff="2024-01-15T05:00:00Z",
                                          generated="2026-09-30T00:00:00Z"))


def test_integer_and_float_lines_are_one_forecast(store):
    """Finding 3."""
    c = store()
    nba_month(c, payload(lines_event()), "2024-01-15T12:00:00Z")
    fid, created = ledger.record_market(c, _forecast(line=-4, p=(0.5, 0.05, 0.45)))
    assert created and ledger.record_market(c, _forecast(line=-4.0, p=(0.5, 0.05, 0.45))) == (fid, False)
    zid, _ = ledger.record_market(c, _forecast(line=0.0, p=(0.5, 0.05, 0.45)))
    assert ledger.record_market(c, _forecast(line=-0.0, p=(0.5, 0.05, 0.45))) == (zid, False)


def test_preseason_and_exhibition_shootouts_are_shootouts():
    """Finding 4."""
    assert nhl_stats.decision(1, 5) == "SO" and nhl_stats.decision(2, 5) == "SO" and nhl_stats.decision(3, 5) == "OT"


def test_replay_refuses_a_cache_mixing_nba_injury_reports_with_nhl_sources(store):
    """Finding 5: the injury source belongs to the NBA store."""
    c = store()
    ingest.ingest_nhl_teams(c, fetch=lambda: TEAMS, retrieved_at="2024-10-01T00:00:00Z")
    from app import raw_cache
    from app.sources import nba_injury
    raw_cache.store_fetch(c, source=nba_injury.SOURCE, endpoint="x", request_key="k", body=b"%PDF",
                          retrieved_at="2024-10-01T01:00:00Z", importer_version="t", suffix=".pdf")
    with pytest.raises(db.StoreMismatch):
        ingest.replay(c)


def test_forecast_tipoff_must_be_a_timestamp_and_match_live_games(store):
    """Finding 6."""
    c = store()
    nba_month(c, payload(lines_event()), "2024-01-15T12:00:00Z")
    bad = _forecast()
    with pytest.raises(sqlite3.IntegrityError, match="scheduled start"):
        ledger.record_market(c, ledger.MarketForecast(**{**bad.__dict__, "tipoff_at": "tomorrow"}))
    with pytest.raises(sqlite3.IntegrityError, match="scheduled start"):
        ledger.record_market(c, ledger.MarketForecast(**{**bad.__dict__, "tipoff_at": "2024-01-16T18:00:00Z"}))
    assert ledger.record_market(c, bad)[1]
