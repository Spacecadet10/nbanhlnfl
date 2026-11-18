"""scripts/backfill_published_picks.py: a card is matched to its game by stable game_id even when the game's start moved."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import backfill_published_picks as bf

DDL = """CREATE TABLE teams (team_id TEXT, abbr TEXT);
CREATE TABLE games (game_id TEXT, tipoff_at TEXT, home_team_id TEXT, away_team_id TEXT);
CREATE TABLE game_forecasts (forecast_id INTEGER PRIMARY KEY, game_id TEXT, market TEXT, status TEXT, generated_at TEXT,
  tipoff_at TEXT, model_version TEXT, line REAL, p_a REAL, p_mid REAL, p_b REAL, confidence REAL);"""


def db(game_tipoff, forecast_tipoff):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(DDL)
    c.executemany("INSERT INTO teams VALUES (?,?)", [("t1", "FLA"), ("t2", "ND")])
    c.execute("INSERT INTO games VALUES ('ncaab:1', ?, 't1', 't2')", (game_tipoff,))
    c.execute("INSERT INTO game_forecasts VALUES (1,'ncaab:1','winner','forecast','2026-11-17T19:31:07Z',?,'m',NULL,0.8563,0,0.1437,0.8563)",
              (forecast_tipoff,))
    return c


CARD = {"sport": "ncaab", "matchup": "ND @ FLA", "blocks": [("Winner", "FLA win", "85.6")],
        "start_local": bf.datetime(1900, 11, 17, 16, 30)}


def test_a_card_whose_game_start_later_moved_is_matched_by_the_forecast_start_and_flagged():
    store = db("2026-11-17T21:51:00Z", "2026-11-17T21:30:00Z")
    gid, out = bf.reconstruct_point(store, "ncaab", CARD, "2026-11-17T19:31:49Z")
    assert gid == "ncaab:1" and out[0][1] == "unambiguous" and out[0][2]["forecast_id"] == 1
    assert bf.kickoff_moved(store, gid, bf.to_utc(CARD["start_local"], "2026-11-17T19:31:49Z")) is True


def test_an_unmoved_game_is_not_flagged_and_an_unrelated_start_stays_unmatched():
    store = db("2026-11-17T21:30:00Z", "2026-11-17T21:30:00Z")
    gid, out = bf.reconstruct_point(store, "ncaab", CARD, "2026-11-17T19:31:49Z")
    assert gid == "ncaab:1" and bf.kickoff_moved(store, gid, "2026-11-17T21:30:00Z") is False
    other = {**CARD, "start_local": bf.datetime(1900, 11, 17, 18, 0)}
    assert bf.reconstruct_point(store, "ncaab", other, "2026-11-17T19:31:49Z")[0] is None
