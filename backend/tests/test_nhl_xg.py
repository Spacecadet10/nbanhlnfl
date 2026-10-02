"""NHL shot import and the xG candidate (docs/MODEL_SELECTION.md §12) on a synthetic league (no network)."""
import json
import zlib
import random
import sqlite3

import pytest

from app import backtest, nhl_eval, nhl_shots
from app.models.hockey import GoalsConfig, HockeyEloConfig
from app.models.hockey_xg import XgInputs, hockey_logistic_xg, shot_vector, strength, vectors
from app.sources import nhl_pbp
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)


def payload(api_id: int, home: int, away: int, seed: int, state="OFF") -> bytes:
    rng = random.Random(seed)
    plays = []
    for i in range(40):
        side_home = rng.random() < 0.5
        x = rng.uniform(25, 85) * rng.choice((1, -1))
        dist = abs(89 - abs(x))
        goal = rng.random() < (0.25 if dist < 15 else 0.03)
        plays.append({"sortOrder": i, "typeDescKey": "goal" if goal else rng.choice(("shot-on-goal", "missed-shot")),
                      "periodDescriptor": {"number": 1 + i // 14, "periodType": "REG"}, "timeInPeriod": f"{i % 14}:{i % 60:02d}",
                      "situationCode": rng.choice(("1551", "1451", "1541")),
                      "details": {"xCoord": x, "yCoord": rng.uniform(-30, 30), "zoneCode": "O", "shotType": "wrist",
                                  "eventOwnerTeamId": home if side_home else away, "goalieInNetId": 1}})
    plays.append({"sortOrder": 99, "typeDescKey": "shot-on-goal", "periodDescriptor": {"number": 5, "periodType": "SO"},
                  "details": {"xCoord": 80, "yCoord": 0, "zoneCode": "O", "eventOwnerTeamId": home}})
    return json.dumps({"id": api_id, "gameState": state, "homeTeam": {"id": home}, "awayTeam": {"id": away}, "plays": plays}).encode()


def fake_fetch(conn):
    def f(game_id):
        g = conn.execute("SELECT home_team_id, away_team_id FROM games WHERE game_id=?", (game_id,)).fetchone()
        h, a = int(g[0].split(":")[1]), int(g[1].split(":")[1])
        return payload(int(nhl_pbp.api_id(game_id)), h, a, zlib.crc32(game_id.encode()) % 10_000)
    return f


def test_parse_strength_and_vectors():
    complete, shots = nhl_pbp.parse(json.loads(payload(1, 6, 16, 1)))
    assert complete and len(shots) == 40 and all(s.period < 5 for s in shots)       # shootout attempt dropped
    assert strength("1551", "home") == "ev5" and strength("1451", "home") == "pp" and strength("1451", "away") == "sh"
    assert strength("0651", "away") == "pp" and strength(None, "home") is None
    s = shots[0]
    v = shot_vector(s, False)
    assert v is not None and v[0] >= 0 and 0 <= v[1] <= 90
    assert len(vectors(shots)) == 40


def test_import_records_policy_and_is_append_only(nhl_store):
    out = nhl_shots.import_pending(nhl_store, [2018], limit=5, pause=0, fetch=fake_fetch(nhl_store), log=lambda *_: None)
    assert out["imported"] == 5 and not out["errors"]
    imp = nhl_store.execute("SELECT i.*, g.result_available_at FROM nhl_pbp_imports i JOIN games g USING (game_id)").fetchall()
    assert all(r["policy"] == "historical_backfill" and r["available_at"] == r["result_available_at"] for r in imp)
    assert nhl_store.execute("SELECT count(*) FROM nhl_shots").fetchone()[0] == 5 * 40
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        nhl_store.execute("UPDATE nhl_shots SET goal=1")
    again = nhl_shots.import_pending(nhl_store, [2018], limit=5, pause=0, fetch=fake_fetch(nhl_store), log=lambda *_: None)
    assert {r["game_id"] for r in imp}.isdisjoint(  # completed games are never fetched again
        {g["game_id"] for g in nhl_store.execute("SELECT game_id FROM nhl_pbp_imports WHERE fetch_id > ?", (max(r["fetch_id"] for r in imp),))})
    assert again["imported"] == 5


def test_xg_uses_only_earlier_seasons_and_future_shots_never_change_past_forecasts(nhl_store):
    nhl_shots.import_pending(nhl_store, None, pause=0, fetch=fake_fetch(nhl_store), log=lambda *_: None)
    rows = nhl_eval.load_rows(nhl_store, "development")
    events = nhl_shots.load_events(nhl_store, rows)
    assert len(events) == len(rows)
    ec, gc = HockeyEloConfig(k=4), GoalsConfig()
    base = {p["game_id"]: p["p_home"] for p in backtest.run(hockey_logistic_xg(ec, gc), rows, events)}
    split = "2019-01-01T00:00:00Z"
    past = [r for r in rows if r.cutoff_at < split]
    altered = [e if e.game.tipoff_at < split else e.__class__(e.game, e.available_at, e.observed_at, e.fetch_id,
                                                              tuple(s.__class__(**{**s.__dict__, "goal": not s.goal}) for s in e.shots))
               for e in events]
    got = {p["game_id"]: p["p_home"] for p in backtest.run(hockey_logistic_xg(ec, gc), rows, altered)}
    assert all(got[r.game.game_id] == base[r.game.game_id] for r in past)
    xi = XgInputs()
    first = min(events, key=lambda e: e.game.tipoff_at)
    xi.update_box(first)
    assert xi.unvalued == 1 and xi.fits[first.game.season] is None           # no earlier season: nothing valued
    early = [e for e in events if e.available_at <= rows[0].cutoff_at]
    assert not early                                                           # shots never precede their own cutoff
