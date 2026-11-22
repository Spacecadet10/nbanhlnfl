"""NCAA men's Division I basketball data-only store (docs/NCAAB_PLAN.md): ESPN day scoreboards, Division I classification,
box scores from game summaries, forward DraftKings quotes, after-game odds (benchmark only), coverage and the loader the
model phase reads.

NCAAB is registered for storage but not served (sports.DATA_ONLY). Everything here reads or writes only a store whose
identity is 'ncaab' (file ncaab.sqlite, schema 0042). Import is a pure function of the cached response bytes
(`replay` rebuilds it without the network); reruns add nothing a source did not change.

Classification (the contract): a game is Division I when both teams carry a conference id on that game's scoreboard. A game
with a non-Division I team is stored with season_type 'other': never rated, forecast, graded, shown or counted.
"""
import hashlib
import json
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import ingest, raw_cache, sports
from .db import StoreMismatch, claim_store, has_table, store_sport, transaction, utc_now
from .sources import espn_ncaab

MAX_BOX_ATTEMPTS = 3            # summary requests per game (fetched or failed) before the game is left as missing
SETTLED_AFTER = timedelta(days=2)   # a day fetched this long after it ended is complete: a resumed backfill skips it
DAY_RETRIES = 3


def _require_store(conn: sqlite3.Connection) -> None:
    current = store_sport(conn)
    if current not in (None, "ncaab"):
        raise StoreMismatch(f"This store holds {current!r} data, not 'ncaab'")


def _require_ncaab(conn: sqlite3.Connection) -> None:
    current = store_sport(conn)
    if current != sports.NCAAB.id:
        raise StoreMismatch(f"This store holds {current or 'unlabelled'!r} data, not 'ncaab'")


def _ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def season_of(day: date) -> int:
    """Season by ending year (2027 = 2026-27); a season's games run from November to the national championship in April."""
    return day.year + 1 if day.month >= 7 else day.year


def season_days(season: int) -> list[str]:
    """Every Eastern day of a season's window, YYYYMMDD: 1 Nov of the previous year to 30 Apr (the first game is the first
    Monday of November or later and the championship is in the first week of April; earlier exhibitions are not Division I
    results). Only the days up to today are asked for by `backfill`."""
    day, end, out = date(season - 1, 11, 1), date(season, 4, 30), []
    while day <= end:
        out.append(day.strftime("%Y%m%d"))
        day += timedelta(days=1)
    return out


# --- Scoreboard days -----------------------------------------------------------------------------------------------
def ingest_day(conn: sqlite3.Connection, yyyymmdd: str, *, fetch: Callable[[str], bytes] = espn_ncaab.fetch_day,
               retrieved_at: Optional[str] = None, cache_dir: Optional[Path] = None) -> dict:
    """One Eastern day of the Division I scoreboard: raw-cached, then games, teams, results, context, box-score columns the
    scoreboard has, and (scheduled games) DraftKings quotes, in one transaction. Rerunning the same bytes changes nothing
    but the logged retrieval."""
    _require_store(conn)
    request_key = espn_ncaab.day_request(yyyymmdd)[0]
    body = fetch(yyyymmdd)
    retrieved_at = retrieved_at or utc_now()          # read after the response arrived
    with transaction(conn):
        claim_store(conn, "ncaab")
        raw_id, fetch_id = raw_cache.store_fetch(conn, source=espn_ncaab.SOURCE, endpoint=espn_ncaab.ENDPOINT,
                                                 request_key=request_key, body=body, retrieved_at=retrieved_at,
                                                 importer_version=espn_ncaab.IMPORTER_VERSION, cache_dir=cache_dir)
        out = import_scoreboard_fetch(conn, raw_id, fetch_id, retrieved_at, cache_dir)
    return {"date": yyyymmdd, "fetch_id": fetch_id, **out}


def import_scoreboard_fetch(conn: sqlite3.Connection, raw_id: int, fetch_id: Optional[int], retrieved_at: str,
                            cache_dir: Optional[Path] = None) -> dict:
    """Imports one logged scoreboard retrieval (inside a transaction). Shared with `replay`."""
    def parse(payload):
        return _keep_division_one(conn, espn_ncaab.parse_scoreboard(payload))
    stats = ingest.import_raw(conn, raw_id, cache_dir=cache_dir, retrieved_at=retrieved_at, parse=parse, fetch_id=fetch_id)
    games = parse(json.loads(raw_cache.load(conn, raw_id, cache_dir)))
    for g in games:
        ingest.orient_to_stored(conn, g)        # the context below follows the stored sides, as the game row does
    stats.update(d1_games=sum(g.both_d1 for g in games), non_d1_games=sum(not g.both_d1 for g in games), context_changed=0)
    for g in games:
        row = conn.execute("SELECT last_seen_at, last_seen_fetch_id FROM games WHERE game_id=?", (g.game_id,)).fetchone()
        if row is None or (row["last_seen_at"], row["last_seen_fetch_id"]) != (retrieved_at, fetch_id):
            continue                       # an older retrieval than the one that last contained the game: never overwrites
        for team, conference in ((g.home, g.home_conference_id), (g.away, g.away_conference_id)):
            _upsert_team_season(conn, g.season, team.team_id, conference, retrieved_at)
        stats["context_changed"] += _upsert_context(conn, g, raw_id, retrieved_at)
        if g.status == "final":
            for team, is_home, s in ((g.home, 1, g.home_stats), (g.away, 0, g.away_stats)):
                ingest._upsert_stats(conn, g.game_id, team.team_id, is_home, s, raw_id, retrieved_at)
    stats["lines"] = ingest.import_lines_fetch(conn, fetch_id, "ncaab", cache_dir) if fetch_id is not None else {}
    return stats


def _has_forecast(conn: sqlite3.Connection, game_id: str) -> bool:
    return any(conn.execute(sql, (game_id,)).fetchone() for sql in (
        "SELECT 1 FROM predictions WHERE game_id = ?", "SELECT 1 FROM market_forecasts WHERE game_id = ?",
        "SELECT 1 FROM game_forecasts WHERE game_id = ? AND status = 'forecast'"))


def _keep_division_one(conn: sqlite3.Connection, games: list) -> list:
    """Division I status is read from one scoreboard, and a scoreboard that drops a team's conferenceId would turn a stored D-I
    game into season_type 'other' (hiding it and its forecasts). A game stored as D-I keeps its conference ids, and so its
    season type, when a retrieval lacks one, unless the game has no forecast and the missing team-season is known non-D-I
    (a stored `ncaab_team_seasons` row with is_d1 = 0): then the downgrade stands."""
    for g in games:
        if g.both_d1:
            continue
        old = conn.execute("SELECT g.home_team_id, g.away_team_id, c.home_conference_id AS home, c.away_conference_id AS away "
                           "FROM games g JOIN ncaab_game_context c ON c.game_id = g.game_id WHERE g.game_id = ? AND c.both_d1 = 1",
                           (g.game_id,)).fetchone()
        if old is None or (old["home_team_id"], old["away_team_id"]) != (g.home.team_id, g.away.team_id):
            continue
        if not _has_forecast(conn, g.game_id) and any(
                not conf and conn.execute("SELECT 1 FROM ncaab_team_seasons WHERE season = ? AND team_id = ? AND is_d1 = 0",
                                          (g.season, team.team_id)).fetchone()
                for team, conf in ((g.home, g.home_conference_id), (g.away, g.away_conference_id))):
            continue
        g.home_conference_id = g.home_conference_id or old["home"]
        g.away_conference_id = g.away_conference_id or old["away"]
        g.season_type = espn_ncaab.SEASON_TYPES.get(g.season_type_id, "other")
    return games


def _upsert_team_season(conn: sqlite3.Connection, season: int, team_id: str, conference: Optional[str], at: str) -> None:
    """One row per team and season; the latest conference id seen wins and a team never goes back to 'no conference'."""
    conn.execute(
        "INSERT INTO ncaab_team_seasons (season, team_id, conference_id, is_d1, first_seen_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(season, team_id) DO UPDATE SET conference_id = excluded.conference_id, is_d1 = excluded.is_d1, "
        "updated_at = excluded.updated_at WHERE excluded.conference_id IS NOT NULL "
        "AND ncaab_team_seasons.conference_id IS NOT excluded.conference_id",
        (season, team_id, conference, int(conference is not None), at, at))


def _upsert_context(conn: sqlite3.Connection, g: espn_ncaab.NcaabGame, raw_id: int, at: str) -> int:
    """The game's context, rewritten only when a value changed (1 = written)."""
    values = (g.season_type_id, g.tournament_note, int(g.conference_competition), g.home_conference_id, g.away_conference_id,
              int(g.both_d1), g.ot_periods, g.source_status)
    row = conn.execute("SELECT season_type_id, tournament_note, conference_competition, home_conference_id, away_conference_id, "
                       "both_d1, ot_periods, source_status FROM ncaab_game_context WHERE game_id=?", (g.game_id,)).fetchone()
    if row is not None and tuple(row) == values:
        return 0
    conn.execute(
        "INSERT INTO ncaab_game_context (game_id, season_type_id, tournament_note, conference_competition, home_conference_id, "
        "away_conference_id, both_d1, ot_periods, source_status, updated_at, last_raw_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(game_id) DO UPDATE SET season_type_id = excluded.season_type_id, tournament_note = excluded.tournament_note, "
        "conference_competition = excluded.conference_competition, home_conference_id = excluded.home_conference_id, "
        "away_conference_id = excluded.away_conference_id, both_d1 = excluded.both_d1, ot_periods = excluded.ot_periods, "
        "source_status = excluded.source_status, updated_at = excluded.updated_at, last_raw_id = excluded.last_raw_id",
        (g.game_id, *values, at, raw_id))
    return 1


def settled_days(conn: sqlite3.Connection) -> set[str]:
    """Days (YYYYMMDD) whose scoreboard was fetched at least SETTLED_AFTER after the day ended: complete, so a resumed
    backfill does not ask for them again."""
    done = set()
    rows = conn.execute("SELECT r.request_key, MAX(f.retrieved_at) AS last FROM raw_fetches f JOIN raw_responses r ON r.raw_id = f.raw_id "
                        "WHERE r.source = ? GROUP BY r.request_key", (espn_ncaab.SOURCE,))
    for r in rows:
        day = r["request_key"].split("dates=")[1][:8]
        end = datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        if _ts(r["last"]) >= end + SETTLED_AFTER:
            done.add(day)
    return done


def ingest_range(conn: sqlite3.Connection, days: Iterable[str], *, fetch: Callable[[str], bytes] = espn_ncaab.fetch_day,
                 sleep: float = 1.0, resume: bool = True, log: Callable[[str], None] = print,
                 pause: Callable[[float], None] = time.sleep) -> dict:
    """Ingests each day (about one request per `sleep` seconds; a failed day is retried, then reported and skipped).
    With `resume`, days already fetched after they ended are skipped, so an interrupted run continues where it stopped."""
    skip = settled_days(conn) if resume else set()
    todo = [d for d in days if d not in skip]
    out = {"days": len(todo), "skipped_settled": len(skip & set(days)) if resume else 0, "fetched": 0, "failed_days": [],
           "games_inserted": 0, "games_updated": 0, "quotes_inserted": 0}
    for i, day in enumerate(todo):
        if i and sleep:
            pause(sleep)
        for attempt in range(DAY_RETRIES):
            try:
                s = ingest_day(conn, day, fetch=fetch)
                break
            except StoreMismatch:
                raise
            except Exception as exc:
                s = {"date": day, "error": f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"}
                if attempt + 1 < DAY_RETRIES and sleep:
                    pause(sleep * 2 ** attempt)
        if "error" in s:
            out["failed_days"].append(day)
        else:
            out["fetched"] += 1
            out["games_inserted"] += s["games_inserted"]
            out["games_updated"] += s["games_updated"]
            out["quotes_inserted"] += (s.get("lines") or {}).get("inserted", 0)
        log(json.dumps({k: s[k] for k in ("date", "games_seen", "games_inserted", "games_updated", "d1_games", "error") if k in s}))
    return out


def backfill(conn: sqlite3.Connection, seasons: Iterable[int], *, today: Optional[date] = None, **kw) -> dict:
    """Scoreboard days of each season (up to today), oldest season first."""
    today = today or datetime.now(timezone.utc).date()
    last = today.strftime("%Y%m%d")
    results = {}
    for season in seasons:
        days = [d for d in season_days(season) if d <= last]
        results[str(season)] = ingest_range(conn, days, **kw)
    return results


# --- Division I list -------------------------------------------------------------------------------------------------
def ingest_teams(conn: sqlite3.Connection, *, fetch: Callable[[], bytes] = espn_ncaab.fetch_teams,
                 retrieved_at: Optional[str] = None, cache_dir: Optional[Path] = None) -> dict:
    """The source's current Division I team list, kept as listings of the season it was retrieved in (cross-check only)."""
    _require_store(conn)
    body = fetch()
    retrieved_at = retrieved_at or utc_now()
    season = season_of(_ts(retrieved_at).date())
    with transaction(conn):
        claim_store(conn, "ncaab")
        raw_id, fetch_id = raw_cache.store_fetch(conn, source=espn_ncaab.TEAMS_SOURCE, endpoint=espn_ncaab.TEAMS_ENDPOINT,
                                                 request_key=espn_ncaab.TEAMS_REQUEST_KEY, body=body, retrieved_at=retrieved_at,
                                                 importer_version=espn_ncaab.TEAMS_IMPORTER_VERSION, cache_dir=cache_dir)
        return {"season": season, "fetch_id": fetch_id, **_import_teams(conn, raw_id, fetch_id, retrieved_at, season, cache_dir)}


def _import_teams(conn: sqlite3.Connection, raw_id: int, fetch_id: int, retrieved_at: str, season: int,
                  cache_dir: Optional[Path]) -> dict:
    teams = espn_ncaab.parse_teams(json.loads(raw_cache.load(conn, raw_id, cache_dir)))
    new = 0
    for t in teams:
        ingest._upsert_team(conn, t, retrieved_at)
        new += conn.execute("INSERT OR IGNORE INTO ncaab_d1_listings (season, team_id, retrieved_at, fetch_id, raw_id) "
                            "VALUES (?, ?, ?, ?, ?)", (season, t.team_id, retrieved_at, fetch_id, raw_id)).rowcount
    return {"listed": len(teams), "new": new}


def d1_disagreements(conn: sqlite3.Connection, season: int) -> dict:
    """Where the source's Division I team list and the scoreboard rule (a conference id on the game's scoreboard) disagree
    for `season`. Reported, never resolved: the scoreboard rule decides, and a disagreement needs a person's look."""
    listed = conn.execute("SELECT COUNT(*) FROM ncaab_d1_listings WHERE season=?", (season,)).fetchone()[0]
    listed_not_d1 = conn.execute(
        "SELECT t.team_id, t.name FROM ncaab_d1_listings l JOIN ncaab_team_seasons s ON s.season = l.season AND s.team_id = l.team_id "
        "JOIN teams t ON t.team_id = l.team_id WHERE l.season=? AND s.is_d1 = 0 ORDER BY t.name", (season,)).fetchall()
    d1_not_listed = conn.execute(
        "SELECT t.team_id, t.name, s.conference_id FROM ncaab_team_seasons s JOIN teams t ON t.team_id = s.team_id "
        "WHERE s.season=? AND s.is_d1 = 1 AND ? > 0 AND NOT EXISTS (SELECT 1 FROM ncaab_d1_listings l WHERE l.season = s.season "
        "AND l.team_id = s.team_id) ORDER BY t.name", (season, listed)).fetchall()
    return {"season": season, "listed": listed, "listed_but_non_d1_on_scoreboard": [dict(r) for r in listed_not_d1],
            "scoreboard_d1_not_listed": [dict(r) for r in d1_not_listed]}


# --- Box scores (game summaries) -------------------------------------------------------------------------------------
def box_pending(conn: sqlite3.Connection, *, season: Optional[int] = None, limit: Optional[int] = None,
                newest_first: bool = True) -> list[str]:
    """Division I final games in an eligible season type with no possession observation yet and fewer than MAX_BOX_ATTEMPTS
    summary attempts: what a resumed run fetches next. Non-Division I games never need a box score."""
    marks = ", ".join("?" * len(sports.NCAAB.eligible_season_types))
    sql = (f"SELECT g.game_id FROM games g JOIN ncaab_game_context c ON c.game_id = g.game_id WHERE g.status = 'final' "
           f"AND g.season_type IN ({marks}) AND c.both_d1 = 1 "
           "AND NOT EXISTS (SELECT 1 FROM team_possession_observations o WHERE o.game_id = g.game_id) "
           "AND (SELECT COUNT(*) FROM box_fetch_attempts a WHERE a.game_id = g.game_id) < ? ")
    args: list = [*sports.NCAAB.eligible_season_types, MAX_BOX_ATTEMPTS]
    if season is not None:
        sql += "AND g.season = ? "
        args.append(season)
    sql += f"ORDER BY g.tipoff_at {'DESC' if newest_first else 'ASC'}, g.game_id"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)
    return [r[0] for r in conn.execute(sql, args)]


def _log_attempt(conn: sqlite3.Connection, game_id: str, at: str, fetch_id: Optional[int] = None,
                 error: Optional[str] = None) -> None:
    conn.execute("INSERT INTO box_fetch_attempts (game_id, attempted_at, outcome, fetch_id, error) VALUES (?, ?, ?, ?, ?)",
                 (game_id, at, "fetched" if fetch_id is not None else "failed", fetch_id, error))


def ingest_summary(conn: sqlite3.Connection, game_id: str, *, fetch: Callable[[str], bytes] = espn_ncaab.fetch_summary,
                   retrieved_at: Optional[str] = None, clock: Optional[Callable[[], str]] = None,
                   cache_dir: Optional[Path] = None) -> dict:
    """Fetches one game's summary, caches it raw, logs the attempt and appends its two team lines as possession observations
    (and any odds it lists as after-game lines). A request or import that fails is logged as a failed attempt (outside the
    import transaction, so it still uses the game's allowance) and raised."""
    _require_store(conn)
    clock = clock or utc_now
    key = espn_ncaab.summary_request(game_id)[0]
    started = retrieved_at or clock()
    try:
        body = fetch(game_id)
    except Exception as exc:
        _failed(conn, game_id, started, exc)
        raise
    retrieved_at = retrieved_at or clock()            # read after the response arrived: never earlier than it was received
    try:
        with transaction(conn):
            claim_store(conn, "ncaab")
            _, fetch_id = raw_cache.store_fetch(conn, source=espn_ncaab.SUMMARY_SOURCE, endpoint=espn_ncaab.SUMMARY_ENDPOINT,
                                                request_key=key, body=body, retrieved_at=retrieved_at,
                                                importer_version=espn_ncaab.SUMMARY_IMPORTER_VERSION, cache_dir=cache_dir)
            _log_attempt(conn, game_id, started, fetch_id=fetch_id)
            out = import_summary_fetch(conn, fetch_id, cache_dir)
    except Exception as exc:
        _failed(conn, game_id, started, exc)
        raise
    return {"game_id": game_id, **out}


def _failed(conn: sqlite3.Connection, game_id: str, at: str, exc: Exception) -> None:
    with transaction(conn):
        _log_attempt(conn, game_id, at, error=f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"[:300])


def import_summary_fetch(conn: sqlite3.Connection, fetch_id: int, cache_dir: Optional[Path] = None) -> dict:
    """Appends one summary retrieval's observations and after-game lines (inside a transaction; idempotent per fetch). A
    summary ESPN does not mark completed, or whose game differs from the request, records nothing and is counted."""
    f = conn.execute("SELECT f.raw_id, f.retrieved_at, r.request_key FROM raw_fetches f JOIN raw_responses r ON r.raw_id = f.raw_id "
                     "WHERE f.fetch_id=?", (fetch_id,)).fetchone()
    payload = json.loads(raw_cache.load(conn, f["raw_id"], cache_dir))
    box = espn_ncaab.parse_summary(payload)
    stats = {**ingest._observation_stats(fetch_id, f["raw_id"], len(box.lines)), "skipped_not_completed": 0,
             "skipped_wrong_game": 0, "complete_lines": 0, "lines_recorded": 0}
    if box.game_id != espn_ncaab.PREFIX + f["request_key"].split("=", 1)[1]:
        stats["skipped_wrong_game"] = len(box.lines) or 1
        return stats
    if not box.completed:
        stats["skipped_not_completed"] = len(box.lines) or 1
        return stats
    for line in box.lines:
        ingest._record_observation(conn, box.game_id, line, fetch_id, f["raw_id"], f["retrieved_at"], True, stats)
        stats["complete_lines"] += all(getattr(line, k) is not None for k in ("points", "fga", "fta", "oreb", "tov"))
    if conn.execute("SELECT 1 FROM games WHERE game_id=? AND status='final'", (box.game_id,)).fetchone():
        for l in espn_ncaab.parse_summary_lines(payload):
            stats["lines_recorded"] += conn.execute(
                "INSERT OR IGNORE INTO ncaab_after_game_lines (game_id, provider, details, spread_home, over_under, retrieved_at, "
                "fetch_id, raw_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (box.game_id, l.provider, l.details, l.spread_home, l.over_under, f["retrieved_at"], fetch_id, f["raw_id"])).rowcount
    return stats




def fetch_boxes(conn: sqlite3.Connection, *, fetch: Callable[[str], bytes] = espn_ncaab.fetch_summary, limit: Optional[int] = None,
                season: Optional[int] = None, newest_first: bool = True, sleep: Callable[[float], None] = time.sleep,
                pause: float = 1.0, clock: Optional[Callable[[], str]] = None, log: Optional[Callable[[str], None]] = None) -> dict:
    """Fetches the pending summaries (about one request per `pause` seconds). One failing game never stops the others;
    every attempt is logged, so a rerun continues with what is still missing and never retries a game past its allowance."""
    queue = box_pending(conn, season=season, limit=limit, newest_first=newest_first)
    out = {"requests": 0, "fetched": 0, "failed": 0, "recorded": 0, "not_completed": 0}
    for i, game_id in enumerate(queue):
        if i and pause:
            sleep(pause)
        out["requests"] += 1
        try:
            r = ingest_summary(conn, game_id, fetch=fetch, clock=clock)
            out["fetched"] += 1
            out["recorded"] += r["recorded"]
            out["not_completed"] += r["skipped_not_completed"] > 0
        except StoreMismatch:
            raise
        except Exception as exc:
            out["failed"] += 1
            if log:
                log(json.dumps({"game_id": game_id, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}))
        if log and out["requests"] % 500 == 0:
            log(json.dumps({"progress": out}))
    out["pending_after"] = len(box_pending(conn, season=season))
    return out


# --- Replay ------------------------------------------------------------------------------------------------------------
def replay(conn: sqlite3.Connection, cache_dir: Optional[Path] = None) -> dict:
    """Re-imports every logged NCAAB retrieval (scoreboard days, team lists, summaries) in retrieval order from the cached
    bytes, each at its own retrieval time. Into the store they came from it adds nothing; into a fresh store it rebuilds."""
    _require_store(conn)
    sources = (espn_ncaab.SOURCE, espn_ncaab.TEAMS_SOURCE, espn_ncaab.SUMMARY_SOURCE)
    out = {"fetches": 0, "games_inserted": 0, "games_updated": 0}
    rows = conn.execute(f"SELECT f.fetch_id, f.raw_id, f.retrieved_at, r.source FROM raw_fetches f JOIN raw_responses r ON r.raw_id = f.raw_id "
                        f"WHERE r.source IN ({', '.join('?' * len(sources))}) ORDER BY f.retrieved_at, f.fetch_id", sources).fetchall()
    for f in rows:
        with transaction(conn):
            claim_store(conn, "ncaab")
            if f["source"] == espn_ncaab.SOURCE:
                s = import_scoreboard_fetch(conn, f["raw_id"], f["fetch_id"], f["retrieved_at"], cache_dir)
                out["games_inserted"] += s["games_inserted"]
                out["games_updated"] += s["games_updated"]
            elif f["source"] == espn_ncaab.TEAMS_SOURCE:
                _import_teams(conn, f["raw_id"], f["fetch_id"], f["retrieved_at"], season_of(_ts(f["retrieved_at"]).date()), cache_dir)
            else:
                game = conn.execute("SELECT 1 FROM games WHERE game_id = ?", (espn_ncaab.PREFIX + conn.execute(
                    "SELECT request_key FROM raw_responses WHERE raw_id=?", (f["raw_id"],)).fetchone()[0].split("=", 1)[1],)).fetchone()
                if game:
                    import_summary_fetch(conn, f["fetch_id"], cache_dir)
        out["fetches"] += 1
    return out


# --- What the model phase reads ------------------------------------------------------------------------------------------
def load_rows(conn: sqlite3.Connection, *, cutoff: str, seasons: Optional[Iterable[int]] = None) -> list[dict]:
    """Eligible rows known at `cutoff` (docs/NCAAB_PLAN.md "Interfaces between phases"): final games of both Division I teams
    in the regular season or postseason whose result was available at or before the cutoff, in tipoff order. The
    possession inputs (FGA, FTA, OREB, DREB, TOV per side) are the latest observation available at or before the cutoff, or None
    (never guessed); `box_available_at` is the later of the two sides' availability. A result that was corrected after the
    cutoff is excluded until its correction is available (result_available_at moves on a correction)."""
    _require_ncaab(conn)
    types = sports.NCAAB.eligible_season_types
    sql = ("SELECT g.game_id, g.season, g.season_type, g.tipoff_at, g.home_team_id, g.away_team_id, g.neutral_site, g.home_score, "
           "g.away_score, g.result_available_at, c.ot_periods, c.conference_competition, c.tournament_note, c.home_conference_id, "
           "c.away_conference_id FROM games g JOIN ncaab_game_context c ON c.game_id = g.game_id WHERE g.status = 'final' "
           f"AND g.season_type IN ({', '.join('?' * len(types))}) AND c.both_d1 = 1 AND g.result_available_at <= ?")
    args: list = [*types, cutoff]
    if seasons is not None:
        wanted = sorted(set(seasons))
        sql += f" AND g.season IN ({', '.join('?' * len(wanted))})"
        args += wanted
    sql += " ORDER BY g.tipoff_at, g.game_id"
    box = ingest.possession_as_of(conn, cutoff)
    rows = []
    for r in conn.execute(sql, args):
        row = dict(r)
        avail = []
        for side, team in (("home", r["home_team_id"]), ("away", r["away_team_id"])):
            o = box.get((r["game_id"], team))
            for field in ("fga", "fta", "oreb", "dreb", "tov"):
                row[f"{side}_{field}"] = o[field] if o is not None else None
            avail.append(o["available_at"] if o is not None else None)
        row["box_available_at"] = max(avail) if None not in avail else None
        rows.append(row)
    return rows


def dataset_version(rows: Iterable[dict]) -> str:
    """Hash of the eligible rows' identity, teams, season, tipoff, venue, final score and result availability (the same
    fields as backtest.dataset_version). Independent of the cutoff once a game is available; changes when a result is corrected."""
    h = hashlib.sha256()
    for r in sorted(rows, key=lambda r: r["game_id"]):
        h.update("|".join(map(str, (r["game_id"], r["season"], r["season_type"], r["tipoff_at"], r["home_team_id"], r["away_team_id"],
                                    r["neutral_site"], r["home_score"], r["away_score"], r["result_available_at"]))).encode())
        h.update(b"\n")
    return h.hexdigest()[:16]


# --- Coverage ----------------------------------------------------------------------------------------------------------
def coverage(conn: sqlite3.Connection) -> dict:
    """Per season: games stored, Division I (eligible season types) and non-Division I, neutral, postseason, overtime, finals,
    box-score coverage (scoreboard FGA/FTA and the complete possession inputs) and odds coverage; plus request counts and
    the Division I list cross-check."""
    _require_ncaab(conn)
    types = sports.NCAAB.eligible_season_types
    marks = ", ".join("?" * len(types))
    elig = f"g.season_type IN ({marks}) AND c.both_d1 = 1"
    seasons: dict = {}
    base_sql = (
        f"SELECT g.season, COUNT(*) AS games, SUM(c.both_d1 = 0) AS non_d1, SUM({elig}) AS d1_eligible, "
        f"SUM(g.status = 'final') AS final, SUM(({elig}) AND g.status = 'final') AS eligible_final, "
        f"SUM(({elig}) AND g.neutral_site = 1) AS neutral, SUM(({elig}) AND g.season_type = 'postseason') AS postseason, "
        f"SUM(({elig}) AND g.status = 'final' AND c.ot_periods > 0) AS ot, SUM(({elig}) AND g.status = 'scheduled') AS scheduled, "
        f"SUM(({elig}) AND g.status = 'postponed') AS postponed, SUM(({elig}) AND g.status = 'canceled') AS canceled, "
        f"SUM(({elig}) AND g.status NOT IN ('final', 'scheduled', 'postponed', 'canceled')) AS other_status "
        f"FROM games g JOIN ncaab_game_context c ON c.game_id = g.game_id GROUP BY g.season ORDER BY g.season")
    for r in conn.execute(base_sql, (*types,) * (base_sql.count("?") // len(types))):
        seasons[str(r["season"])] = {k: (r[k] or 0) for k in r.keys() if k != "season"}
    for r in conn.execute(
            f"SELECT g.season, COUNT(*) AS n FROM games g JOIN ncaab_game_context c ON c.game_id = g.game_id WHERE {elig} "
            "AND g.status = 'final' AND (SELECT COUNT(*) FROM team_game_stats s WHERE s.game_id = g.game_id AND s.fga IS NOT NULL "
            "AND s.fta IS NOT NULL) = 2 GROUP BY g.season", types):
        seasons[str(r["season"])]["scoreboard_fga_fta"] = r["n"]
    # complete box = both sides' latest observation has FGA, FTA, OREB and TOV (one pass over the observations, not per game)
    full: dict = {}
    for r in conn.execute(
            f"SELECT g.season, g.game_id, o.team_id, o.fga, o.fta, o.oreb, o.tov FROM games g "
            f"JOIN ncaab_game_context c ON c.game_id = g.game_id JOIN ({ingest.POSSESSION_LATEST_SQL}) o ON o.game_id = g.game_id "
            f"WHERE {elig} AND g.status = 'final'", types):
        if None not in (r["fga"], r["fta"], r["oreb"], r["tov"]):
            full.setdefault((r["season"], r["game_id"]), set()).add(r["team_id"])
    for (season, _), teams in full.items():
        if len(teams) == 2:
            seasons[str(season)]["box_complete"] = seasons[str(season)].get("box_complete", 0) + 1
    for r in conn.execute(
            f"SELECT g.season, SUM(EXISTS (SELECT 1 FROM market_quotes q WHERE q.game_id = g.game_id)) AS pregame, "
            "SUM(EXISTS (SELECT 1 FROM ncaab_after_game_lines l WHERE l.game_id = g.game_id)) AS after_game, "
            "SUM(EXISTS (SELECT 1 FROM market_quotes q WHERE q.game_id = g.game_id) OR "
            "EXISTS (SELECT 1 FROM ncaab_after_game_lines l WHERE l.game_id = g.game_id)) AS any_odds "
            f"FROM games g JOIN ncaab_game_context c ON c.game_id = g.game_id WHERE {elig} GROUP BY g.season", types):
        seasons[str(r["season"])].update(odds_pregame_games=r["pregame"] or 0, odds_after_game_games=r["after_game"] or 0,
                                        odds_games=r["any_odds"] or 0)
    for s in seasons.values():
        s.setdefault("scoreboard_fga_fta", 0)
        s.setdefault("box_complete", 0)
        s.setdefault("odds_games", 0)
        s["box_pct"] = round(100 * s["box_complete"] / s["eligible_final"], 2) if s["eligible_final"] else None
    eligible_total = sum(s["eligible_final"] for s in seasons.values())
    sources = {r[0]: r[1] for r in conn.execute(
        "SELECT r.source, COUNT(*) FROM raw_fetches f JOIN raw_responses r ON r.raw_id = f.raw_id GROUP BY r.source")}
    attempts = {r[0]: r[1] for r in conn.execute("SELECT outcome, COUNT(*) FROM box_fetch_attempts GROUP BY outcome")}
    basis = {r[0]: r[1] for r in conn.execute(
        f"SELECT g.result_availability_basis, COUNT(*) FROM games g JOIN ncaab_game_context c ON c.game_id = g.game_id WHERE {elig} "
        "AND g.status = 'final' GROUP BY 1", types)}
    statuses = {r[0]: r[1] for r in conn.execute("SELECT source_status, COUNT(*) FROM ncaab_game_context GROUP BY 1")}
    listed = [r[0] for r in conn.execute("SELECT DISTINCT season FROM ncaab_d1_listings ORDER BY 1")]
    return {"sport": "ncaab", "seasons": seasons, "eligible_final_results": eligible_total,
            "requests": {"scoreboard_days": sources.get(espn_ncaab.SOURCE, 0), "team_lists": sources.get(espn_ncaab.TEAMS_SOURCE, 0),
                         "summaries": sources.get(espn_ncaab.SUMMARY_SOURCE, 0), "summary_failed": attempts.get("failed", 0)},
            "result_availability_basis": basis, "source_statuses": statuses,
            "d1_list_cross_check": {str(s): {k: (len(v) if isinstance(v, list) else v) for k, v in d1_disagreements(conn, s).items()
                                             if k != "season"} for s in listed},
            "raw_responses": conn.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0],
            "dataset_version": dataset_version(load_rows(conn, cutoff="9999-12-31T00:00:00Z"))}
