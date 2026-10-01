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
from .sources import espn, sdv_team_box

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


POSSESSION_FIELDS = ("points", "fga", "fta", "oreb", "dreb", "tov")


def ingest_team_box(conn: sqlite3.Connection, season: int, *, historical_backfill: bool = False,
                    fetch: Callable[[int], bytes] = sdv_team_box.fetch_season, retrieved_at: Optional[str] = None,
                    cache_dir: Optional[Path] = None) -> dict:
    """Downloads (digest-verified) one sportsdataverse team box season file, caches it raw and imports it.
    `historical_backfill` must be requested explicitly (see import_team_box_fetch); the default is observed availability."""
    policy = sdv_team_box.HISTORICAL_BACKFILL if historical_backfill else sdv_team_box.OBSERVED
    body = fetch(season)
    retrieved_at = retrieved_at or utc_now()
    with transaction(conn):
        _, fetch_id = raw_cache.store_fetch(conn, source=sdv_team_box.SOURCE,
                                            endpoint=sdv_team_box.DOWNLOAD.format(name=sdv_team_box.file_name(season)),
                                            request_key=sdv_team_box.request_key(season, policy), body=body,
                                            retrieved_at=retrieved_at, importer_version=sdv_team_box.IMPORTER_VERSION,
                                            cache_dir=cache_dir)
        return {"season": season, "policy": policy, **import_team_box_fetch(conn, fetch_id, cache_dir=cache_dir)}


def import_team_box_fetch(conn: sqlite3.Connection, fetch_id: int, cache_dir: Optional[Path] = None) -> dict:
    """Appends one observation per (game, team) for this retrieval event. Must run inside a transaction.

    Observations are keyed by fetch (raw_fetches), not by file content: identical bytes downloaded again after a
    correction (12 → 13 → 12) are a new observation at their own time. Nothing is updated: a correction is a new row,
    so import order cannot change the result and replay is deterministic. Availability:
    - `observed_retrieval` (default): available_at = this file's retrieval time, however late it arrives.
    - `historical_backfill_assumed`: only under an explicit historical-backfill import AND only for games whose result
      was itself imported retrospectively (`assumed_tipoff_plus_4h`); available_at = that assumed result time.
      Games whose results we observed live always use the retrieval time.
    Rows are matched by ESPN game id and checked against the stored home/away teams; non-franchise (All-Star) rows,
    unknown or unfinished games and side mismatches are counted, never guessed. Scoreboard FGA/points differences are
    reported only."""
    raw = conn.execute("SELECT f.raw_id, f.retrieved_at, r.request_key FROM raw_fetches f "
                       "JOIN raw_responses r ON r.raw_id = f.raw_id WHERE f.fetch_id=?", (fetch_id,)).fetchone()
    raw_id, observed_at = raw["raw_id"], raw["retrieved_at"]
    backfill = sdv_team_box.policy_of(raw["request_key"]) == sdv_team_box.HISTORICAL_BACKFILL
    rows = sdv_team_box.parse(raw_cache.load(conn, raw_id, cache_dir))
    stats = _observation_stats(fetch_id, raw_id, len(rows))
    for b in rows:
        if not b.franchise:
            stats["skipped_non_franchise"] += 1
            continue
        _record_observation(conn, b.game_id, b, fetch_id, raw_id, observed_at, backfill, stats)
    return stats


def _record_observation(conn: sqlite3.Connection, game_id: str, b, fetch_id: int, raw_id: int, observed_at: str,
                        backfill: bool, stats: dict) -> None:
    """Appends one team line as an observation of `fetch_id`, after matching it to a stored final game. Shared by
    every possession source; `b` has team_id, is_home and POSSESSION_FIELDS."""
    g = conn.execute("SELECT status, home_team_id, away_team_id, result_available_at, result_availability_basis "
                     "FROM games WHERE game_id=?", (game_id,)).fetchone()
    if g is None:
        stats["skipped_unknown_game"] += 1
        return
    if g["status"] != "final":
        stats["skipped_not_final"] += 1
        return
    if b.team_id != (g["home_team_id"] if b.is_home else g["away_team_id"]):
        stats["skipped_side_mismatch"] += 1
        return
    board = conn.execute("SELECT fga, points FROM team_game_stats WHERE game_id=? AND team_id=?",
                         (game_id, b.team_id)).fetchone()
    if board and ((board["fga"] is not None and b.fga is not None and board["fga"] != b.fga)
                  or (b.points is not None and board["points"] != b.points)):
        stats["scoreboard_mismatch"] += 1
    if backfill and g["result_availability_basis"] == "assumed_tipoff_plus_4h" \
            and g["result_available_at"] <= observed_at:
        available_at, basis = g["result_available_at"], "historical_backfill_assumed"
        stats["assumed_availability"] += 1
    else:
        available_at, basis = observed_at, "observed_retrieval"
    values = {f: getattr(b, f) for f in POSSESSION_FIELDS}
    if conn.execute("SELECT 1 FROM team_possession_observations WHERE game_id=? AND team_id=? AND fetch_id=?",
                    (game_id, b.team_id, fetch_id)).fetchone():
        stats["already_recorded"] += 1
        return
    prev = conn.execute(
        f"SELECT {', '.join(POSSESSION_FIELDS)} FROM team_possession_observations WHERE game_id=? AND team_id=? "
        "AND observed_at < ? ORDER BY observed_at DESC, fetch_id DESC LIMIT 1",
        (game_id, b.team_id, observed_at)).fetchone()
    if prev and any(prev[f] != v for f, v in values.items()):
        stats["changed_vs_previous"] += 1
    cols = {"game_id": game_id, "team_id": b.team_id, "fetch_id": fetch_id, "raw_id": raw_id, "is_home": b.is_home,
            **values, "observed_at": observed_at, "available_at": available_at, "availability_basis": basis}
    conn.execute(f"INSERT INTO team_possession_observations ({', '.join(cols)}) "
                 f"VALUES ({', '.join('?' * len(cols))})", list(cols.values()))
    stats["recorded"] += 1


def _observation_stats(fetch_id: int, raw_id: int, rows_seen: int) -> dict:
    return {"fetch_id": fetch_id, "raw_id": raw_id, "rows_seen": rows_seen, "recorded": 0, "already_recorded": 0,
            "changed_vs_previous": 0, "assumed_availability": 0, "skipped_non_franchise": 0,
            "skipped_unknown_game": 0, "skipped_not_final": 0, "skipped_side_mismatch": 0, "scoreboard_mismatch": 0}


def ingest_box_summary(conn: sqlite3.Connection, game_id: str, *, fetch: Callable[[str], bytes] = espn.fetch_summary,
                       retrieved_at: Optional[str] = None, cache_dir: Optional[Path] = None) -> dict:
    """In-season refresh: fetches one game's ESPN summary, caches it raw and appends its two team lines as
    observations whose observed_at and available_at are this retrieval's time (never backdated)."""
    body = fetch(game_id)
    retrieved_at = retrieved_at or utc_now()
    with transaction(conn):
        _, fetch_id = raw_cache.store_fetch(conn, source=espn.SUMMARY_SOURCE, endpoint=espn.SUMMARY_ENDPOINT,
                                            request_key=espn.summary_request(game_id)[0], body=body,
                                            retrieved_at=retrieved_at, importer_version=espn.SUMMARY_IMPORTER_VERSION,
                                            cache_dir=cache_dir)
        return {"game_id": game_id, **import_box_summary_fetch(conn, fetch_id, cache_dir=cache_dir)}


def import_box_summary_fetch(conn: sqlite3.Connection, fetch_id: int, cache_dir: Optional[Path] = None) -> dict:
    """Appends the observations of one summary retrieval. Must run inside a transaction. A summary that ESPN does not
    mark completed, or whose game id differs from the request, records nothing and is counted."""
    raw = conn.execute("SELECT f.raw_id, f.retrieved_at, r.request_key FROM raw_fetches f "
                       "JOIN raw_responses r ON r.raw_id = f.raw_id WHERE f.fetch_id=?", (fetch_id,)).fetchone()
    box = espn.parse_summary_box(json.loads(raw_cache.load(conn, raw["raw_id"], cache_dir)))
    stats = {**_observation_stats(fetch_id, raw["raw_id"], len(box.lines)), "skipped_not_completed": 0,
             "skipped_wrong_game": 0, "complete_lines": 0}
    if box.game_id != "espn:" + raw["request_key"].split("=", 1)[1]:
        stats["skipped_wrong_game"] = len(box.lines) or 1
        return stats
    if not box.completed:
        stats["skipped_not_completed"] = len(box.lines) or 1
        return stats
    for line in box.lines:
        _record_observation(conn, box.game_id, line, fetch_id, raw["raw_id"], raw["retrieved_at"], False, stats)
        stats["complete_lines"] += all(getattr(line, f) is not None for f in ("points", "fga", "fta", "oreb", "tov"))
    return stats


# Values as known at a point in time: per (game, team), the most recent retrieval among those available by
# `as_of`. Later corrections are invisible before their own availability. Parameters: (as_of,).
POSSESSION_AS_OF_SQL = """
    SELECT * FROM (
        SELECT o.*, ROW_NUMBER() OVER (PARTITION BY o.game_id, o.team_id
                                       ORDER BY o.observed_at DESC, o.fetch_id DESC) AS rn
        FROM team_possession_observations o WHERE o.available_at <= ?)
    WHERE rn = 1"""

# Latest known values regardless of availability (coverage and inspection only; never for features).
POSSESSION_LATEST_SQL = POSSESSION_AS_OF_SQL.replace("WHERE o.available_at <= ?", "")


def possession_as_of(conn: sqlite3.Connection, as_of: str) -> dict[tuple[str, str], sqlite3.Row]:
    return {(r["game_id"], r["team_id"]): r for r in conn.execute(POSSESSION_AS_OF_SQL, (as_of,))}


def rebuild_team_box(conn: sqlite3.Connection, cache_dir: Optional[Path] = None) -> dict:
    """Imports every logged possession retrieval (season files and game summaries) in time order. Idempotent; used
    by replay and after migrations 0006/0007."""
    importers = {sdv_team_box.SOURCE: import_team_box_fetch, espn.SUMMARY_SOURCE: import_box_summary_fetch}
    totals = {"fetches": 0, "recorded": 0}
    for r in conn.execute(f"SELECT f.fetch_id, r.source FROM raw_fetches f JOIN raw_responses r ON r.raw_id = f.raw_id "
                          f"WHERE r.source IN ({', '.join('?' * len(importers))}) ORDER BY f.retrieved_at, f.fetch_id",
                          tuple(importers)).fetchall():
        with transaction(conn):
            totals["recorded"] += importers[r["source"]](conn, r["fetch_id"], cache_dir=cache_dir)["recorded"]
        totals["fetches"] += 1
    return totals


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
    # Possession files after all ESPN responses, so their games exist.
    totals["responses"] += rebuild_team_box(conn, cache_dir=cache_dir)["fetches"]
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
