"""Recomputed past-season forecasts of the sealed winner model (step C, Nov 11 2026; proof:
docs/evidence/winner-history-c-2026-11-11/ in the sprint1 checkout). The winner sibling of app/points_history.py.

No training, no tuning: the sealed winner Elo (NBA elo k 15 H 50 carry 0.75 MOV; NHL hockey_elo K 4 H 30 carry 0.75 MOV) is
advanced through results exactly as the near-tipoff writer does (near_tipoff.predict_now: results ordered by availability,
each used only once its result_available_at is at or before the forecast time) and predicts under the live near-tipoff
version. On every canonical live winner forecast this reproduces the displayed % and side exactly (NBA 147/147, NHL 148/148,
Nov 11 copies, forecasting-job and delivery code).

Seasons, on purpose:
- Built: NBA 2021-22 .. 2025-26 (store 2022..2026), NHL 2023-24 .. 2025-26 (store 2024..2026), competitive games only.
- TUNED seasons are stored with season_role 'tuned' and are never read into a record: NBA 2021-22..2023-24 (validation: the
  Elo grid and the choice of Elo as primary, docs/BASELINE_REPORT.md), NHL 2017-18..2023-24 (development tuning, then the
  validation choice of hockey_elo, docs/NHL_BASELINE_REPORT.md). They are shown nowhere and never count toward the record,
  rank or AVOID. Only the one-time final test seasons 2024-25 and 2025-26 (winner settings unchanged since) are 'clean'.
- Forecast time: start minus points_history.LEAD (one fixed time inside the live two-hour window). Like the live writer, no
  forecast is made while an earlier game of either team in the last week has no result available yet (skipped, counted).
- Kept in its own append-only file (winner_<sport>.sqlite under <data dir>/history), never in the live ledgers, and read
  as its own source beside the live record, never merged into it.
"""
import copy
import hashlib
import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Optional

from . import config, coverage, live, near_tipoff
from .points_history import COMPETITIVE, HISTORY_POLICY, SOURCE, HistoryRefused, forecast_time

MARKET = "winner"
HISTORY_SEASONS = {"nba": (2022, 2023, 2024, 2025, 2026), "nhl": (2024, 2025, 2026)}
# Store seasons (end year) on which the winner settings were tuned, or the winner model chosen. Never counted anywhere.
TUNED_SEASONS = {"nba": frozenset({2020, 2021, 2022, 2023, 2024}),
                 "nhl": frozenset(range(2016, 2025))}
LOOKBACK = near_tipoff.RESULT_LOOKBACK

SCHEMA = """
CREATE TABLE IF NOT EXISTS winner_history (
  history_id      INTEGER PRIMARY KEY,
  sport           TEXT NOT NULL CHECK (sport IN ('nba', 'nhl')),
  game_id         TEXT NOT NULL,
  season          INTEGER NOT NULL,
  season_type     TEXT NOT NULL,
  season_role     TEXT NOT NULL CHECK (season_role IN ('clean', 'tuned')),
  source          TEXT NOT NULL CHECK (source = 'history_recomputed'),
  model_version   TEXT NOT NULL,
  history_policy  TEXT NOT NULL,
  forecast_at     TEXT NOT NULL,
  tipoff_at       TEXT NOT NULL,
  results_used    INTEGER NOT NULL,
  dataset_version TEXT NOT NULL,
  home_win_prob   REAL NOT NULL CHECK (home_win_prob BETWEEN 0 AND 1),
  confidence      REAL NOT NULL,
  side            TEXT NOT NULL CHECK (side IN ('a', 'b')),
  computed_at     TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  CHECK (forecast_at < tipoff_at)
);
CREATE INDEX IF NOT EXISTS winner_history_key ON winner_history(sport, model_version, season_role);
CREATE TRIGGER IF NOT EXISTS winner_history_no_update BEFORE UPDATE ON winner_history
BEGIN SELECT RAISE(ABORT, 'winner_history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS winner_history_no_delete BEFORE DELETE ON winner_history
BEGIN SELECT RAISE(ABORT, 'winner_history is append-only'); END;
"""


def path(sport: str, data_dir: Optional[Path] = None) -> Path:
    return Path(data_dir or config.data_dir()) / "history" / f"winner_{sport}.sqlite"


def connect(p: Path, *, create: bool = False) -> Optional[sqlite3.Connection]:
    """The history file (schema ensured when `create`); None when it does not exist and `create` is False."""
    if not create and not p.is_file():
        return None
    if create:
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(p)
        conn.executescript(SCHEMA)
    else:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def season_role(sport: str, season: int) -> str:
    return "tuned" if season in TUNED_SEASONS[sport] else "clean"


class Replayer:
    """The sealed winner model advanced through results in the near-tipoff writer's order. `at(t)` = a copy of the model
    after every result available at t (the state near_tipoff.predict_now builds from scratch at now = t)."""

    def __init__(self, conn: sqlite3.Connection):
        self.eng = live.engine(conn)
        self.rows = sorted(self.eng.load_rows(conn), key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id))
        _, self.model = live.primary_model(conn)
        self.model_version = near_tipoff.model_version(self.model)
        self.i, self.t = 0, ""

    def at(self, t: str):
        if t < self.t:
            raise ValueError("forecast times must be visited in order")
        while self.i < len(self.rows) and self.rows[self.i].result_available_at <= t:
            self.model.update(self.rows[self.i].result)
            self.i += 1
        self.t = t
        return copy.deepcopy(self.model), self.rows[:self.i]


def unavailable_earlier(conn: sqlite3.Connection, g, t: str) -> list:
    """The live missing_result guard for a past game: earlier eligible games of either team that started in the last
    LOOKBACK before t and whose result was not available by t (live refuses while such a game is not final)."""
    types = live.engine(conn).eligible_types
    since = near_tipoff._fmt(near_tipoff._parse(t) - LOOKBACK)
    return [r[0] for r in conn.execute(
        f"SELECT game_id FROM games WHERE (home_team_id IN (?, ?) OR away_team_id IN (?, ?)) AND tipoff_at <= ? AND tipoff_at > ? "
        f"AND game_id != ? AND (status IN ('scheduled', 'in_progress') OR (status = 'final' AND result_available_at > ?)) "
        f"AND season_type IN ({', '.join('?' * len(types))}) ORDER BY tipoff_at",
        (g["home_team_id"], g["away_team_id"], g["home_team_id"], g["away_team_id"], t, since, g["game_id"], t, *types))]


def compute(rep: Replayer, g, t: str) -> dict:
    """The winner forecast of one game at time t, exactly as near_tipoff.predict_now makes it."""
    model, used = rep.at(t)
    if any(r.result_available_at > t for r in used):
        raise HistoryRefused("a result not yet available was used")      # never: Replayer only advances to t
    p = round(model.predict(rep.eng.pregame(g)), 9)
    vec = (p, 0.0, round(1 - p, 9))
    return {"home_win_prob": p, "results_used": len(used), "dataset_version": rep.eng.dataset_version(used),
            "model_version": rep.model_version, "confidence": coverage._confidence(MARKET, vec),
            "side": coverage.pick_side(MARKET, vec)}


def build(conn: sqlite3.Connection, hist: Optional[sqlite3.Connection], *, seasons: tuple, computed_at: str,
          write: bool = True) -> dict:
    """Appends the recomputed winner forecasts of every final competitive game of `seasons` (each in HISTORY_SEASONS),
    tuned seasons flagged. A rerun with unchanged inputs adds nothing; changed inputs add a new row, earlier rows kept."""
    sport = live.engine(conn).sport
    allowed = HISTORY_SEASONS.get(sport, ())
    bad = [s for s in seasons if s not in allowed]
    if bad or not seasons:
        raise HistoryRefused(f"{sport}: seasons {bad or seasons} are not winner history seasons {allowed}")
    games = conn.execute(f"SELECT * FROM games WHERE status='final' AND season IN ({','.join('?' * len(seasons))}) "
                         f"AND season_type IN ({','.join('?' * len(COMPETITIVE))}) ORDER BY tipoff_at, game_id",
                         (*seasons, *COMPETITIVE)).fetchall()
    rep = Replayer(conn)
    out = {"sport": sport, "seasons": list(seasons), "games": len(games), "created": 0, "already_recorded": 0,
           "skipped_missing_result": 0, "model_version": rep.model_version, "season_roles": {"clean": 0, "tuned": 0}}
    for g in sorted(games, key=lambda g: (forecast_time(g["tipoff_at"]), g["game_id"])):
        t = forecast_time(g["tipoff_at"])
        if unavailable_earlier(conn, g, t):
            out["skipped_missing_result"] += 1
            continue
        r = compute(rep, g, t)
        role = season_role(sport, g["season"])
        out["season_roles"][role] += 1
        row = {"sport": sport, "game_id": g["game_id"], "season": g["season"], "season_type": g["season_type"],
               "season_role": role, "source": SOURCE, "model_version": r["model_version"], "history_policy": HISTORY_POLICY,
               "forecast_at": t, "tipoff_at": g["tipoff_at"], "results_used": r["results_used"],
               "dataset_version": r["dataset_version"], "home_win_prob": r["home_win_prob"], "confidence": r["confidence"],
               "side": r["side"], "computed_at": computed_at}
        content = {k: v for k, v in row.items() if k != "computed_at"}
        row["idempotency_key"] = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
        if not write:
            continue
        cur = hist.execute(f"INSERT OR IGNORE INTO winner_history ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                           list(row.values()))
        out["created" if cur.rowcount else "already_recorded"] += 1
    if write:
        hist.commit()
    return out


def history_index(conn: sqlite3.Connection, hist: Optional[sqlite3.Connection], market: str, as_of: str) -> Optional[dict]:
    """{(displayed %, side, SOURCE, model_version, 'none'): {game_id: grade}} of the latest row per game of the CLEAN seasons
    only (season_role 'clean' and not a TUNED season), graded on read like threshold_overview.percentage_index. None
    without a history file or for another market."""
    from .threshold_overview import _percentage_key
    if hist is None or market != MARKET:
        return None
    sport = live.engine(conn).sport
    if sport not in HISTORY_SEASONS:
        return None
    rows = hist.execute("SELECT * FROM winner_history WHERE sport=? AND season_role='clean' ORDER BY history_id",
                        (sport,)).fetchall()
    latest = {}
    for r in rows:
        if r["season"] in TUNED_SEASONS[sport]:
            continue                      # a tuned season never counts, whatever its row says
        latest[(r["game_id"], r["model_version"])] = r
    games = {}
    ids = sorted({r["game_id"] for r in latest.values()})
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for g in conn.execute(f"SELECT * FROM games WHERE game_id IN ({','.join('?' * len(chunk))})", chunk):
            games[g["game_id"]] = g
    out: dict = {}
    for r in latest.values():
        g = games.get(r["game_id"])
        if g is None or not r["forecast_at"] < r["tipoff_at"] or g["season"] in TUNED_SEASONS[sport]:
            continue
        p = r["home_win_prob"]
        grade = coverage.grade(MARKET, None, (p, 0.0, 1 - p), g, as_of, r["tipoff_at"])
        if grade in ("win", "loss", "push") and (not g["result_available_at"] or g["result_available_at"] > as_of):
            grade = "not_yet_available"
        rec = {"confidence": r["confidence"], "side": r["side"], "source": SOURCE, "model_version": r["model_version"],
               "line_source": "none"}
        out.setdefault(_percentage_key(rec), {})[r["game_id"]] = grade
    return out


def main(argv: Optional[list] = None) -> int:
    import argparse
    from .db import utc_now
    ap = argparse.ArgumentParser(description="Append recomputed past-season winner forecasts (no training).")
    ap.add_argument("--sport", choices=sorted(HISTORY_SEASONS), required=True)
    ap.add_argument("--season", type=int, action="append", help="default: every history season of the sport")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--explicit-release", action="store_true",
                    help="required to write next to a protected live store (release decision, not a development run)")
    a = ap.parse_args(argv)
    store = config.data_dir() / f"{a.sport}.sqlite"
    target = path(a.sport)
    if not a.dry_run and config.protected_path(target) and not a.explicit_release:
        raise SystemExit(f"{target} is in a protected live data folder; refusing without --explicit-release")
    conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)       # the store is only read
    conn.row_factory = sqlite3.Row
    hist = None if a.dry_run else connect(target, create=True)
    out = build(conn, hist, seasons=tuple(a.season or HISTORY_SEASONS[a.sport]), computed_at=utc_now(), write=not a.dry_run)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
