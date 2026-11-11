"""Recomputed past-season forecasts of the sealed points models for spread, total and team totals (step 3, Nov 11 2026;
proof: docs/evidence/points-history-2026-11-11/ in the sprint1 checkout).

No training, no tuning: the sealed market model (market_live.market_model) is advanced through results exactly as the live
near-tipoff writer does (results ordered by availability, each used only once its result_available_at is at or before the
forecast time), and each market is priced with the live code (latest bookmaker line retrieved by then, else the model's own
line). On every canonical live forecast this reproduces the displayed %, side and line exactly (NBA 147/147, NHL 148/148 per
market, Nov 11 copies).

Limits, on purpose:
- Seasons: only HISTORY_SEASONS. The sealed parameters were chosen on earlier seasons (NBA eta/carry tuned on 2022-23, holdout
  2024; NHL goals model chosen on 2022-24, empty-net constants fitted on 2018-21). Forecasts for those seasons would use
  parameters estimated from the games being forecast; an honest version needs a walk-forward refit, which is training: not done.
- Lines: the store holds no bookmaker lines before October 2026, so past-season rows carry the model's own line
  (line_source 'model'). They are never matched with a bookmaker-line live forecast.
- Forecast time: start minus LEAD (one fixed time inside the live two-hour window). Past-season result availability is the
  store's own record (start + 4 h for back-filled seasons).
- Kept in a separate append-only file (points_<sport>.sqlite under <data dir>/history), never in the live ledgers, and read
  as its own source beside the live record, never merged into it.
"""
import copy
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import config, coverage, live, market_live, markets

SOURCE = "history_recomputed"
POLICY = "near_tipoff"                          # the live policy whose computation is reproduced
LEAD = timedelta(minutes=60)
HISTORY_POLICY = "start-60m"
MARKETS = ("spread", "total", "team_total_home", "team_total_away")
HISTORY_SEASONS = {"nba": (2025, 2026), "nhl": (2025, 2026)}
COMPETITIVE = ("regular", "play_in", "postseason")
FMT = "%Y-%m-%dT%H:%M:%SZ"

SCHEMA = """
CREATE TABLE IF NOT EXISTS points_history (
  history_id      INTEGER PRIMARY KEY,
  sport           TEXT NOT NULL CHECK (sport IN ('nba', 'nhl')),
  game_id         TEXT NOT NULL,
  season          INTEGER NOT NULL,
  season_type     TEXT NOT NULL,
  market          TEXT NOT NULL CHECK (market IN ('spread', 'total', 'team_total_home', 'team_total_away')),
  source          TEXT NOT NULL CHECK (source = 'history_recomputed'),
  model_version   TEXT NOT NULL,
  history_policy  TEXT NOT NULL,
  forecast_at     TEXT NOT NULL,
  tipoff_at       TEXT NOT NULL,
  results_used    INTEGER NOT NULL,
  dataset_version TEXT NOT NULL,
  line            REAL NOT NULL,
  line_source     TEXT NOT NULL CHECK (line_source IN ('bookmaker', 'model')),
  market_quote_id INTEGER,
  p_a             REAL NOT NULL CHECK (p_a BETWEEN 0 AND 1),
  p_mid           REAL NOT NULL CHECK (p_mid BETWEEN 0 AND 1),
  p_b             REAL NOT NULL CHECK (p_b BETWEEN 0 AND 1),
  confidence      REAL NOT NULL,
  side            TEXT NOT NULL CHECK (side IN ('a', 'b')),
  computed_at     TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  CHECK (forecast_at < tipoff_at),
  CHECK (abs(p_a + p_mid + p_b - 1) < 1e-6),
  CHECK (line_source = 'bookmaker' OR market_quote_id IS NULL)
);
CREATE INDEX IF NOT EXISTS points_history_key ON points_history(sport, market, model_version, line_source);
CREATE TRIGGER IF NOT EXISTS points_history_no_update BEFORE UPDATE ON points_history
BEGIN SELECT RAISE(ABORT, 'points_history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS points_history_no_delete BEFORE DELETE ON points_history
BEGIN SELECT RAISE(ABORT, 'points_history is append-only'); END;
"""


class HistoryRefused(RuntimeError):
    pass


def path(sport: str, data_dir: Optional[Path] = None) -> Path:
    return Path(data_dir or config.data_dir()) / "history" / f"points_{sport}.sqlite"


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


def forecast_time(tipoff_at: str) -> str:
    return (datetime.strptime(tipoff_at, FMT).replace(tzinfo=timezone.utc) - LEAD).strftime(FMT)


class Replayer:
    """The sealed market model advanced through results in the live writer's order. `at(t)` = a copy of the model after
    every result available at t (the same state market_live.forecast builds from scratch at now = t)."""

    def __init__(self, conn: sqlite3.Connection):
        self.eng = live.engine(conn)
        self.rows = sorted(self.eng.load_rows(conn), key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id))
        self.model, base = market_live.market_model(conn)
        self.model_version = market_live.model_version(base, POLICY)
        self.i, self.t = 0, ""

    def at(self, t: str):
        if t < self.t:
            raise ValueError("forecast times must be visited in order")
        while self.i < len(self.rows) and self.rows[self.i].result_available_at <= t:
            self.model.update(self.rows[self.i].result)
            self.i += 1
        self.t = t
        return copy.deepcopy(self.model), self.rows[:self.i]


def compute(conn: sqlite3.Connection, rep: Replayer, g, market: str, t: str) -> dict:
    """One market of one game at time t, exactly as market_live.forecast prices it."""
    if market not in MARKETS:
        raise ValueError(market)
    model, used = rep.at(t)
    if any(r.result_available_at > t for r in used):
        raise HistoryRefused("a result not yet available was used")      # never: Replayer only advances to t
    dist = model.distribution(rep.eng.pregame(g))
    quote = market_live.latest_quote(conn, g["game_id"], market, t)
    line = quote["line"] if quote else markets.model_line(dist, market)
    pa, pm, _ = (round(x, 9) for x in dist.market(market, line))
    p = (pa, pm, round(1 - pa - pm, 9))
    return {"p": p, "line": line, "line_source": "bookmaker" if quote else "model",
            "market_quote_id": quote["quote_id"] if quote else None, "results_used": len(used),
            "dataset_version": rep.eng.dataset_version(used), "model_version": rep.model_version,
            "confidence": coverage._confidence(market, p), "side": coverage.pick_side(market, p)}


def build(conn: sqlite3.Connection, hist: sqlite3.Connection, *, seasons: tuple, computed_at: str,
          write: bool = True) -> dict:
    """Appends the recomputed forecasts of every final competitive game of `seasons` (each in HISTORY_SEASONS). A rerun
    with unchanged inputs adds nothing; changed inputs (a corrected result, a new line) add a new row, earlier rows kept."""
    sport = live.engine(conn).sport
    allowed = HISTORY_SEASONS.get(sport, ())
    bad = [s for s in seasons if s not in allowed]
    if bad or not seasons:
        raise HistoryRefused(f"{sport}: seasons {bad or seasons} are not history seasons {allowed} "
                             "(their parameters were chosen on those games; a fair version needs refitting)")
    games = conn.execute(f"SELECT * FROM games WHERE status='final' AND season IN ({','.join('?' * len(seasons))}) "
                         f"AND season_type IN ({','.join('?' * len(COMPETITIVE))}) ORDER BY tipoff_at, game_id",
                         (*seasons, *COMPETITIVE)).fetchall()
    rep = Replayer(conn)
    out = {"sport": sport, "seasons": list(seasons), "games": len(games), "created": 0, "already_recorded": 0,
           "model_version": rep.model_version, "line_sources": {"bookmaker": 0, "model": 0}}
    for g in sorted(games, key=lambda g: (forecast_time(g["tipoff_at"]), g["game_id"])):
        t = forecast_time(g["tipoff_at"])
        for market in MARKETS:
            r = compute(conn, rep, g, market, t)
            out["line_sources"][r["line_source"]] += 1
            row = {"sport": sport, "game_id": g["game_id"], "season": g["season"], "season_type": g["season_type"],
                   "market": market, "source": SOURCE, "model_version": r["model_version"], "history_policy": HISTORY_POLICY,
                   "forecast_at": t, "tipoff_at": g["tipoff_at"], "results_used": r["results_used"],
                   "dataset_version": r["dataset_version"], "line": r["line"], "line_source": r["line_source"],
                   "market_quote_id": r["market_quote_id"], "p_a": r["p"][0], "p_mid": r["p"][1], "p_b": r["p"][2],
                   "confidence": r["confidence"], "side": r["side"], "computed_at": computed_at}
            content = {k: v for k, v in row.items() if k != "computed_at"}
            row["idempotency_key"] = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
            if not write:
                continue
            cur = hist.execute(f"INSERT OR IGNORE INTO points_history ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                               list(row.values()))
            out["created" if cur.rowcount else "already_recorded"] += 1
    if write:
        hist.commit()
    return out


def history_index(conn: sqlite3.Connection, hist: Optional[sqlite3.Connection], market: str, as_of: str) -> Optional[dict]:
    """{(displayed %, side, SOURCE, model_version, line_source): {game_id: grade}} of the latest row per game, graded on read
    from the store's results, like threshold_overview.percentage_index. None without a history file or for other markets."""
    from .threshold_overview import _percentage_key
    if hist is None or market not in MARKETS:
        return None
    sport = live.engine(conn).sport
    rows = hist.execute("SELECT * FROM points_history WHERE sport=? AND market=? ORDER BY history_id", (sport, market)).fetchall()
    latest = {}
    for r in rows:
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
        if g is None or not r["forecast_at"] < r["tipoff_at"]:
            continue
        p = (r["p_a"], r["p_mid"], r["p_b"])
        grade = coverage.grade(market, r["line"], p, g, as_of, r["tipoff_at"])
        if grade in ("win", "loss", "push") and (not g["result_available_at"] or g["result_available_at"] > as_of):
            grade = "not_yet_available"
        rec = {"confidence": r["confidence"], "side": r["side"], "source": SOURCE, "model_version": r["model_version"],
               "line_source": r["line_source"]}
        out.setdefault(_percentage_key(rec), {})[r["game_id"]] = grade
    return out


def main(argv: Optional[list] = None) -> int:
    import argparse
    from .db import utc_now
    ap = argparse.ArgumentParser(description="Append recomputed past-season points-model forecasts (no training).")
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
