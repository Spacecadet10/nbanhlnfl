"""The NBA research path goes through the same two-hour guard and cohort recording as the NHL one (synthetic store)."""
import json
import sqlite3

import pytest

from app import agreement, market_live, near_tipoff, research_live
from test_live import store, T_GAME  # noqa: F401  (fixture)


def test_nba_markets_are_recorded_inside_the_two_hour_window_only(store):
    store.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES "
                  "('refresh', '2026-10-21T22:00:00Z', '2026-10-21T22:00:00Z', 'success')")
    now = "2026-10-21T22:00:00Z"                                   # 90 minutes before T_GAME
    out = near_tipoff.predict_now(store, now=now)
    ids = [f["game_id"] for f in out["forecasts"]]
    assert ids == ["espn:g1"]
    market_live.forecast(store, now=now, policy="near_tipoff", game_ids=ids)
    collect = {}
    winner = agreement.predict(store, now=now, game_ids=ids, collect=collect)
    res = research_live.record(store, game_ids=ids, winner_run=winner, history=collect, now=now)
    got = {r["market"]: r for r in store.execute("SELECT * FROM research_cohorts")}
    assert set(got) == {"winner", "spread", "total", "team_total_home", "team_total_away"}, res
    assert all(r["information_at"] == now and r["tipoff_at"] == T_GAME for r in got.values())
    win = json.loads(got["winner"]["bookmaker"])
    assert win["quote_table"] == "odds_quotes" and win["quote_id"]
    missing = json.loads(got["total"]["missing"])
    assert set(missing) <= {"ridge_points", "gbt_points"} and all(v for v in missing.values())
    row = dict(got["total"])
    row.pop("cohort_id")
    row.update(idempotency_key="early", information_at="2026-10-21T21:29:59Z")
    with pytest.raises(sqlite3.IntegrityError, match="two hours"):
        store.execute(f"INSERT INTO research_cohorts ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
