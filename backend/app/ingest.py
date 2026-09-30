"""Schedule/results/team-stat ingestion. Network fetch -> raw cache -> idempotent import.

Import is a pure function of cached bytes, so `replay` rebuilds the same rows without the network.
"""
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from . import raw_cache
from .db import transaction, utc_now
from .sources import espn

RESULT_ASSUMED_DELAY = timedelta(hours=4)
OBSERVED_WINDOW = timedelta(hours=24)  # results first seen within this window of tipoff count as observed


def _ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def months(start: str, end: str) -> list[str]:
    y, m = int(start[:4]), int(start[4:])
    out = []
    while (y, m) <= (int(end[:4]), int(end[4:])):
        out.append(f"{y}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def ingest_month(conn: sqlite3.Connection, yyyymm: str, *, fetch: Callable[[str], bytes] = espn.fetch_month,
                 retrieved_at: Optional[str] = None, cache_dir: Optional[Path] = None) -> dict:
    return _ingest(conn, espn.month_request(yyyymm)[0], fetch(yyyymm), retrieved_at, cache_dir)


def ingest_day(conn: sqlite3.Connection, yyyymmdd: str, *, fetch: Callable[[str], bytes] = espn.fetch_day,
               retrieved_at: Optional[str] = None, cache_dir: Optional[Path] = None) -> dict:
    return _ingest(conn, espn.day_request(yyyymmdd)[0], fetch(yyyymmdd), retrieved_at, cache_dir)


def _ingest(conn: sqlite3.Connection, request_key: str, body: bytes, retrieved_at: Optional[str],
            cache_dir: Optional[Path]) -> dict:
    retrieved_at = retrieved_at or utc_now()
    with transaction(conn):
        raw_id = raw_cache.store(conn, source=espn.SOURCE, endpoint=espn.ENDPOINT, request_key=request_key,
                                 body=body, retrieved_at=retrieved_at, importer_version=espn.IMPORTER_VERSION,
                                 cache_dir=cache_dir)
        return import_raw(conn, raw_id, cache_dir=cache_dir)


def ingest_range(conn: sqlite3.Connection, start: str, end: str, *, sleep: float = 1.0,
                 log: Callable[[str], None] = print) -> list[dict]:
    results = []
    for i, month in enumerate(months(start, end)):
        if i and sleep:
            time.sleep(sleep)
        try:
            stats = ingest_month(conn, month)
        except Exception as exc:  # keep going; report the failure explicitly
            stats = {"month": month, "error": f"{type(exc).__name__}: {str(exc).splitlines()[0]}"}
        stats["month"] = month
        log(json.dumps(stats))
        results.append(stats)
    return results


def replay(conn: sqlite3.Connection, cache_dir: Optional[Path] = None) -> dict:
    """Re-imports every cached response in retrieval order."""
    totals = {"responses": 0, "games_inserted": 0, "games_updated": 0}
    ids = [r["raw_id"] for r in conn.execute(
        "SELECT raw_id FROM raw_responses WHERE source=? ORDER BY first_retrieved_at, raw_id", (espn.SOURCE,))]
    for raw_id in ids:
        with transaction(conn):
            s = import_raw(conn, raw_id, cache_dir=cache_dir)
        totals["responses"] += 1
        totals["games_inserted"] += s["games_inserted"]
        totals["games_updated"] += s["games_updated"]
    return totals


def import_raw(conn: sqlite3.Connection, raw_id: int, cache_dir: Optional[Path] = None) -> dict:
    """Upserts one cached response. Must run inside a transaction."""
    retrieved_at = conn.execute("SELECT first_retrieved_at FROM raw_responses WHERE raw_id=?",
                                (raw_id,)).fetchone()["first_retrieved_at"]
    games = espn.parse_scoreboard(json.loads(raw_cache.load(conn, raw_id, cache_dir)))
    stats = {"raw_id": raw_id, "games_seen": len(games), "games_inserted": 0, "games_updated": 0, "odds_inserted": 0}
    for g in games:
        for t in (g.home, g.away):
            conn.execute(
                "INSERT INTO teams (team_id, abbr, name, first_seen_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(team_id) DO UPDATE SET abbr=excluded.abbr, name=excluded.name "
                "WHERE teams.abbr IS NOT excluded.abbr OR teams.name IS NOT excluded.name",
                (t.team_id, t.abbr, t.name, retrieved_at))
        outcome = _upsert_game(conn, g, raw_id, retrieved_at)
        if outcome:
            stats[f"games_{outcome}"] += 1
        if g.status == "final":
            for team, is_home, s in ((g.home, 1, g.home_stats), (g.away, 0, g.away_stats)):
                _upsert_stats(conn, g.game_id, team.team_id, is_home, s, raw_id, retrieved_at)
        for o in g.odds:
            key = (g.game_id, o.provider, o.price_label, raw_id)
            if conn.execute("SELECT 1 FROM odds_quotes WHERE game_id=? AND provider=? AND market='moneyline' "
                            "AND price_label=? AND raw_id=?", key).fetchone():
                continue  # already imported from this response (replay/retry)
            conn.execute(
                "INSERT INTO odds_quotes (game_id, provider, market, price_label, home_american, "
                "away_american, home_prob_devig, quote_time, retrieved_at, raw_id) "
                "VALUES (?, ?, 'moneyline', ?, ?, ?, ?, NULL, ?, ?)",
                (g.game_id, o.provider, o.price_label, o.home_american, o.away_american, o.home_prob_devig,
                 retrieved_at, raw_id))
            stats["odds_inserted"] += 1
    return stats


def _upsert_game(conn: sqlite3.Connection, g: espn.GameRow, raw_id: int, retrieved_at: str) -> Optional[str]:
    row = conn.execute("SELECT g.*, r.first_retrieved_at AS last_retrieved_at FROM games g "
                       "JOIN raw_responses r ON r.raw_id = g.last_raw_id WHERE game_id=?", (g.game_id,)).fetchone()
    fields = {"season": g.season, "season_type": g.season_type, "tipoff_at": g.tipoff_at,
              "home_team_id": g.home.team_id, "away_team_id": g.away.team_id, "neutral_site": int(g.neutral_site),
              "status": g.status, "home_score": g.home_score, "away_score": g.away_score}
    if g.status == "final":
        observed = _ts(retrieved_at) - _ts(g.tipoff_at) <= OBSERVED_WINDOW
        availability = {
            "result_first_seen_at": retrieved_at,
            "result_available_at": retrieved_at if observed else _fmt(_ts(g.tipoff_at) + RESULT_ASSUMED_DELAY),
            "result_availability_basis": "observed_retrieval" if observed else "assumed_tipoff_plus_4h",
        }
    else:
        availability = {"result_first_seen_at": None, "result_available_at": None, "result_availability_basis": None}

    if row is None:
        cols = {**fields, **availability, "game_id": g.game_id, "first_seen_at": retrieved_at,
                "last_raw_id": raw_id, "updated_at": retrieved_at}
        conn.execute(f"INSERT INTO games ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", list(cols.values()))
        return "inserted"

    # Never regress a final result, and never let an older response overwrite newer information.
    if row["status"] == "final" and g.status != "final":
        return None
    if retrieved_at < row["last_retrieved_at"]:
        return None
    changes = {k: v for k, v in fields.items() if row[k] != v}
    if row["status"] == "final":
        availability = {}  # first observation of a result is kept; availability is not rewritten
    else:
        availability = {k: v for k, v in availability.items() if row[k] != v}
    if not changes and not availability:
        return None
    upd = {**changes, **availability, "last_raw_id": raw_id, "updated_at": retrieved_at}
    conn.execute(f"UPDATE games SET {', '.join(f'{k}=?' for k in upd)} WHERE game_id=?", [*upd.values(), g.game_id])
    return "updated"


def _upsert_stats(conn: sqlite3.Connection, game_id: str, team_id: str, is_home: int, s: dict, raw_id: int,
                  retrieved_at: str) -> None:
    """Stats follow the same ordering rule as games, independently of whether game fields changed:
    a response older than the one that last wrote the row is ignored (ties apply, so replay is stable),
    and a field missing from a newer response keeps its stored value rather than becoming NULL."""
    cols = ["points", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "rebounds", "assists"]
    existing = conn.execute(
        f"SELECT {', '.join('s.' + c for c in cols)}, r.first_retrieved_at AS last_retrieved_at "
        "FROM team_game_stats s JOIN raw_responses r ON r.raw_id = s.last_raw_id WHERE s.game_id=? AND s.team_id=?",
        (game_id, team_id)).fetchone()
    if existing is None:
        conn.execute(f"INSERT INTO team_game_stats (game_id, team_id, is_home, {', '.join(cols)}, last_raw_id) "
                     f"VALUES (?, ?, ?, {', '.join('?' * len(cols))}, ?)",
                     (game_id, team_id, is_home, *[s.get(c) for c in cols], raw_id))
        return
    if retrieved_at < existing["last_retrieved_at"]:
        return
    changes = {c: s[c] for c in cols if s.get(c) is not None and s[c] != existing[c]}
    if changes:
        conn.execute(f"UPDATE team_game_stats SET {', '.join(f'{c}=?' for c in changes)}, last_raw_id=? "
                     "WHERE game_id=? AND team_id=?", (*changes.values(), raw_id, game_id, team_id))
