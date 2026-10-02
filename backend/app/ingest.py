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
from .db import claim_store, has_table, transaction, utc_now
from .sources import espn, nba_injury, nhl_stats, sdv_team_box
from .odds import devig_two_way

RESULT_ASSUMED_DELAY = timedelta(hours=4)
SOURCE_SPORTS = {"nba": {espn.SOURCE, espn.SUMMARY_SOURCE, sdv_team_box.SOURCE, nba_injury.SOURCE},
                 "nhl": {nhl_stats.SOURCE, espn.NHL_SOURCE}}
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
        claim_store(conn, "nba")
        raw_id, fetch_id = raw_cache.store_fetch(conn, source=espn.SOURCE, endpoint=espn.ENDPOINT,
                                                 request_key=request_key, body=body, retrieved_at=retrieved_at,
                                                 importer_version=espn.IMPORTER_VERSION, cache_dir=cache_dir)
        stats = import_raw(conn, raw_id, cache_dir=cache_dir, retrieved_at=retrieved_at)
        stats["lines"] = import_lines_fetch(conn, fetch_id, "nba", cache_dir=cache_dir)
        return stats


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
        claim_store(conn, "nba")
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
        claim_store(conn, "nba")
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
    """Re-imports every logged retrieval in retrieval order, each at its own fetch time, so content that returns after
    an intervening correction (A → B → A) is applied again rather than mistaken for the older response. Schedule and
    result sources first; line quotes and possession files after them, so their games exist."""
    totals = {"responses": 0, "games_inserted": 0, "games_updated": 0, "line_quotes": 0}
    sources = {r["source"] for r in conn.execute("SELECT DISTINCT source FROM raw_responses")}
    for sport, own in SOURCE_SPORTS.items():
        if sources & own:
            with transaction(conn):
                claim_store(conn, sport)   # raises if the cache holds the other sport's sources too
    # One pass over every schedule/result and line retrieval in retrieval order, so each line quote is matched against
    # the games as they were stored at its own retrieval, exactly as live ingestion matched it. A cached response without
    # any logged retrieval (a cache copied without its log) replays once at its first retrieval.
    sources = (espn.SOURCE, nhl_stats.SOURCE, espn.NHL_SOURCE)
    fetches = conn.execute(
        "SELECT f.fetch_id, f.raw_id, f.retrieved_at, r.source FROM raw_fetches f JOIN raw_responses r ON r.raw_id = f.raw_id "
        "WHERE r.source IN (?, ?, ?) "
        "UNION ALL SELECT NULL, r.raw_id, r.first_retrieved_at, r.source FROM raw_responses r "
        "WHERE r.source IN (?, ?, ?) AND NOT EXISTS (SELECT 1 FROM raw_fetches f WHERE f.raw_id = r.raw_id) "
        "ORDER BY 3, 1, 2", sources * 2).fetchall()
    for f in fetches:
        with transaction(conn):
            if f["source"] == espn.SOURCE:
                s = import_raw(conn, f["raw_id"], cache_dir=cache_dir, retrieved_at=f["retrieved_at"])
                if f["fetch_id"] is not None:
                    totals["line_quotes"] += import_lines_fetch(conn, f["fetch_id"], "nba", cache_dir=cache_dir)["inserted"]
            elif f["fetch_id"] is None:
                continue
            elif f["source"] == nhl_stats.SOURCE:
                s = import_nhl_fetch(conn, f["fetch_id"], cache_dir=cache_dir)
            else:
                totals["line_quotes"] += import_lines_fetch(conn, f["fetch_id"], "nhl", cache_dir=cache_dir)["inserted"]
                totals["goalie_snapshots"] = totals.get("goalie_snapshots", 0) + \
                    import_goalies_fetch(conn, f["fetch_id"], cache_dir=cache_dir)["inserted"]
                continue
        totals["responses"] += 1
        totals["games_inserted"] += s["games_inserted"]
        totals["games_updated"] += s["games_updated"]
    totals["responses"] += rebuild_team_box(conn, cache_dir=cache_dir)["fetches"]
    return totals


def import_raw(conn: sqlite3.Connection, raw_id: int, cache_dir: Optional[Path] = None,
               retrieved_at: Optional[str] = None) -> dict:
    """Upserts one cached response as seen at `retrieved_at` (that retrieval's own time; default: the first retrieval
    of these bytes). Must run inside a transaction."""
    retrieved_at = retrieved_at or conn.execute("SELECT first_retrieved_at FROM raw_responses WHERE raw_id=?",
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


def _upsert_team(conn: sqlite3.Connection, t: espn.TeamRow, retrieved_at: str, placeholder: bool = False) -> None:
    """Inserts a team, or renames it from a newer response. A placeholder (unknown id) never overwrites a real name."""
    conn.execute(
        "INSERT INTO teams (team_id, abbr, name, first_seen_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(team_id) DO UPDATE SET abbr=excluded.abbr, name=excluded.name "
        "WHERE ? = 0 AND (teams.abbr IS NOT excluded.abbr OR teams.name IS NOT excluded.name)",
        (t.team_id, t.abbr, t.name, retrieved_at, int(placeholder)))


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


# --- NHL: the NHL's own stats REST season game lists (sources/nhl_stats.py). ---
def ingest_nhl_teams(conn: sqlite3.Connection, *, fetch: Callable[[], bytes] = nhl_stats.fetch_teams,
                     retrieved_at: Optional[str] = None, cache_dir: Optional[Path] = None) -> dict:
    return _ingest_nhl(conn, nhl_stats.team_request()[0], nhl_stats.TEAM_ENDPOINT, fetch(), retrieved_at, cache_dir)


def ingest_nhl_season(conn: sqlite3.Connection, season: int, *, fetch: Callable[[int], bytes] = nhl_stats.fetch_season,
                      retrieved_at: Optional[str] = None, cache_dir: Optional[Path] = None) -> dict:
    return {"season": season, **_ingest_nhl(conn, nhl_stats.season_request(season)[0], nhl_stats.GAME_ENDPOINT,
                                            fetch(season), retrieved_at, cache_dir)}


def _ingest_nhl(conn, request_key, endpoint, body, retrieved_at, cache_dir) -> dict:
    retrieved_at = retrieved_at or utc_now()
    with transaction(conn):
        claim_store(conn, "nhl")
        _, fetch_id = raw_cache.store_fetch(conn, source=nhl_stats.SOURCE, endpoint=endpoint, request_key=request_key,
                                            body=body, retrieved_at=retrieved_at,
                                            importer_version=nhl_stats.IMPORTER_VERSION, cache_dir=cache_dir)
        return import_nhl_fetch(conn, fetch_id, cache_dir=cache_dir)


def import_nhl_fetch(conn: sqlite3.Connection, fetch_id: int, cache_dir: Optional[Path] = None) -> dict:
    """Imports one NHL stats retrieval at its own fetch time. Must run inside a transaction. The team list renames
    teams; a season list upserts games (same ordering rules as ESPN games) and each final game's decision."""
    f = conn.execute("SELECT f.raw_id, f.retrieved_at, r.request_key FROM raw_fetches f "
                     "JOIN raw_responses r ON r.raw_id = f.raw_id WHERE f.fetch_id=?", (fetch_id,)).fetchone()
    payload = nhl_stats.load(raw_cache.load(conn, f["raw_id"], cache_dir))
    stats = {"fetch_id": fetch_id, "games_seen": 0, "games_inserted": 0, "games_updated": 0, "teams": 0,
             "placeholder_teams": 0, "decisions": 0, "decision_unknown": 0}
    if f["request_key"] == nhl_stats.team_request()[0]:
        for t in nhl_stats.parse_teams(payload).values():
            _upsert_team(conn, t, f["retrieved_at"])
            stats["teams"] += 1
        return stats
    known = {int(r["team_id"].split(":", 1)[1]): espn.TeamRow(r["team_id"], r["abbr"], r["name"])
             for r in conn.execute("SELECT * FROM teams WHERE team_id LIKE 'nhl:%'")}
    for g, dec, periods in nhl_stats.parse_season(payload, known, f["retrieved_at"]):
        stats["games_seen"] += 1
        for t in (g.home, g.away):
            unknown = int(t.team_id.split(":", 1)[1]) not in known
            _upsert_team(conn, t, f["retrieved_at"], placeholder=unknown)
            stats["placeholder_teams"] += unknown
        outcome = _upsert_game(conn, g, f["raw_id"], f["retrieved_at"])
        if outcome:
            stats[f"games_{outcome}"] += 1
        if g.status == "final":
            stats["decisions" if dec else "decision_unknown"] += 1
            if dec:
                _upsert_decision(conn, g.game_id, dec, periods, f["raw_id"], f["retrieved_at"])
    return stats


def _upsert_decision(conn: sqlite3.Connection, game_id: str, dec: str, periods: int, raw_id: int,
                     retrieved_at: str) -> None:
    """Same ordering rule as the game row: an older response never overwrites a newer one."""
    row = conn.execute("SELECT d.*, r.first_retrieved_at AS last_retrieved_at FROM game_decisions d "
                       "JOIN raw_responses r ON r.raw_id = d.last_raw_id WHERE d.game_id=?", (game_id,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO game_decisions (game_id, decision, periods, last_raw_id) VALUES (?, ?, ?, ?)",
                     (game_id, dec, periods, raw_id))
    elif retrieved_at >= row["last_retrieved_at"] and (row["decision"], row["periods"]) != (dec, periods):
        conn.execute("UPDATE game_decisions SET decision=?, periods=?, last_raw_id=? WHERE game_id=?",
                     (dec, periods, raw_id, game_id))


# --- Line quotes (moneyline, spread/puck line, total) from ESPN day/month scoreboards, per retrieval event. ---
def ingest_nhl_day(conn: sqlite3.Connection, yyyymmdd: str, *, fetch: Callable[[str], bytes] = espn.fetch_nhl_day,
                   retrieved_at: Optional[str] = None, cache_dir: Optional[Path] = None) -> dict:
    """ESPN NHL day scoreboard, used only for line quotes in the NHL store (schedule and results come from the NHL)."""
    body = fetch(yyyymmdd)
    retrieved_at = retrieved_at or utc_now()
    with transaction(conn):
        claim_store(conn, "nhl")
        _, fetch_id = raw_cache.store_fetch(conn, source=espn.NHL_SOURCE, endpoint=espn.NHL_ENDPOINT,
                                            request_key=espn.day_request(yyyymmdd)[0], body=body,
                                            retrieved_at=retrieved_at, importer_version=espn.LINES_IMPORTER_VERSION,
                                            cache_dir=cache_dir)
        return {"day": yyyymmdd, **import_lines_fetch(conn, fetch_id, "nhl", cache_dir=cache_dir),
                "goalies": import_goalies_fetch(conn, fetch_id, cache_dir=cache_dir)}


def _name_key(name: str) -> str:
    import unicodedata
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "".join(ch for ch in plain.lower() if ch.isalnum())


def _match_game(conn: sqlite3.Connection, sport: str, q: espn.LineQuote) -> Optional[str]:
    """NBA: ESPN event ids are the store's ids. NHL: the game with the same start time (±3 h) and the same home and away
    team names; ambiguous or missing matches return None (counted, never guessed)."""
    if sport == "nba":
        gid = f"espn:{q.event_id}"
        return gid if conn.execute("SELECT 1 FROM games WHERE game_id=?", (gid,)).fetchone() else None
    tip = datetime.strptime(q.tipoff_at, "%Y-%m-%dT%H:%M:%SZ")
    lo, hi = (_fmt((tip - timedelta(hours=3)).replace(tzinfo=timezone.utc)),
              _fmt((tip + timedelta(hours=3)).replace(tzinfo=timezone.utc)))
    rows = conn.execute("SELECT g.game_id, h.name AS home, a.name AS away FROM games g JOIN teams h ON h.team_id = g.home_team_id "
                        "JOIN teams a ON a.team_id = g.away_team_id WHERE g.tipoff_at BETWEEN ? AND ?", (lo, hi)).fetchall()
    hits = [r["game_id"] for r in rows if _name_key(r["home"]) == _name_key(q.home_name)
            and _name_key(r["away"]) == _name_key(q.away_name)]
    return hits[0] if len(hits) == 1 else None


def import_lines_fetch(conn: sqlite3.Connection, fetch_id: int, sport: str, cache_dir: Optional[Path] = None) -> dict:
    """Appends one market_quotes row per price pair in this retrieval (append-only; retries and replays are no-ops).
    Must run inside a transaction. Quotes on games not in this store are counted as unmatched."""
    if not has_table(conn, "market_quotes"):
        return {"fetch_id": fetch_id, "inserted": 0, "skipped": "schema_before_0012"}
    f = conn.execute("SELECT f.raw_id, f.retrieved_at FROM raw_fetches f WHERE f.fetch_id=?", (fetch_id,)).fetchone()
    quotes, skipped = espn.parse_lines(json.loads(raw_cache.load(conn, f["raw_id"], cache_dir)))
    stats = {"fetch_id": fetch_id, "quotes_seen": len(quotes), "inserted": 0, "already_recorded": 0, "unmatched": 0,
             **skipped}
    for q in quotes:
        game_id = _match_game(conn, sport, q)
        if game_id is None:
            stats["unmatched"] += 1
            continue
        key = (game_id, q.provider, q.market, q.price_label, fetch_id)
        if conn.execute("SELECT 1 FROM market_quotes WHERE game_id=? AND provider=? AND market=? AND price_label=? "
                        "AND fetch_id=?", key).fetchone():
            stats["already_recorded"] += 1
            continue
        pa, pb = devig_two_way(q.price_a, q.price_b)
        conn.execute("INSERT INTO market_quotes (sport, game_id, provider, market, price_label, line, price_a, price_b, "
                     "p_a_devig, p_b_devig, retrieved_at, fetch_id, raw_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (sport, game_id, q.provider, q.market, q.price_label, q.line, q.price_a, q.price_b,
                      round(pa, 6), round(1 - round(pa, 6), 6), f["retrieved_at"], fetch_id, f["raw_id"]))
        stats["inserted"] += 1
    return stats


def import_goalies_fetch(conn: sqlite3.Connection, fetch_id: int, cache_dir: Optional[Path] = None) -> dict:
    """Appends one goalie_snapshots row per team of each not-yet-started game in this ESPN NHL retrieval (idempotent; a
    replay adds nothing). Must run inside a transaction. Games not matched to the NHL store are counted, never guessed."""
    if not has_table(conn, "goalie_snapshots"):
        return {"inserted": 0, "skipped": "schema_before_0014"}
    f = conn.execute("SELECT raw_id, retrieved_at FROM raw_fetches WHERE fetch_id=?", (fetch_id,)).fetchone()
    listings = espn.parse_goalies(json.loads(raw_cache.load(conn, f["raw_id"], cache_dir)))
    stats = {"listings": len(listings), "inserted": 0, "already_recorded": 0, "unmatched": 0,
             "by_status": {}}
    for g in listings:
        game_id = _match_game(conn, "nhl", espn.LineQuote(g.event_id, g.tipoff_at, g.home_name, g.away_name, "", "",
                                                           "", None, 0, 0))
        if game_id is None:
            stats["unmatched"] += 1
            continue
        if conn.execute("SELECT 1 FROM goalie_snapshots WHERE game_id=? AND side=? AND fetch_id=?",
                        (game_id, g.side, fetch_id)).fetchone():
            stats["already_recorded"] += 1
            continue
        conn.execute("INSERT INTO goalie_snapshots (game_id, side, fetch_id, raw_id, retrieved_at, source_status, "
                     "source_status_label, espn_player_id, player_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (game_id, g.side, fetch_id, f["raw_id"], f["retrieved_at"], g.status, g.status_label,
                      g.espn_player_id, g.player_name))
        stats["inserted"] += 1
        stats["by_status"][g.status] = stats["by_status"].get(g.status, 0) + 1
    return stats


# Goalie listing known at a time: per game and side, the latest snapshot retrieved at or before `as_of`. Parameters: (as_of,).
GOALIE_AS_OF_SQL = """
    SELECT * FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s.game_id, s.side ORDER BY s.retrieved_at DESC, s.fetch_id DESC) AS rn
        FROM goalie_snapshots s WHERE s.retrieved_at <= ?)
    WHERE rn = 1"""


def rebuild_goalies(conn: sqlite3.Connection, cache_dir: Optional[Path] = None) -> dict:
    """Imports goalie listings from every cached ESPN NHL retrieval in retrieval order (idempotent)."""
    totals = {"fetches": 0, "inserted": 0}
    for f in conn.execute("SELECT f.fetch_id FROM raw_fetches f JOIN raw_responses r ON r.raw_id = f.raw_id "
                          "WHERE r.source = ? ORDER BY f.retrieved_at, f.fetch_id", (espn.NHL_SOURCE,)).fetchall():
        with transaction(conn):
            totals["inserted"] += import_goalies_fetch(conn, f["fetch_id"], cache_dir=cache_dir)["inserted"]
        totals["fetches"] += 1
    return totals
