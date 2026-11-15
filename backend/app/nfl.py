"""NFL data-only store (docs/NFL_PLAN.md): coverage of the ESPN NFL schedule and results.

NFL is registered for storage but not served (sports.DATA_ONLY): no model and no ledger rows. The API reads it only
through /api/nfl/* (schedule, results, matchup facts, coverage), never through the served provider. Everything here
reads or writes only a store whose identity is 'nfl'.
"""
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from . import sports
from .db import StoreMismatch, store_sport


def _require_nfl(conn: sqlite3.Connection) -> None:
    current = store_sport(conn)
    if current != sports.NFL.id:
        raise StoreMismatch(f"This store holds {current or 'unlabelled'!r} data, not 'nfl'")


def coverage(conn: sqlite3.Connection) -> dict:
    """Per season and season type: games, final, scheduled, ties, neutral-site games and distinct teams; plus the number
    of final results in eligible season types (what a future model could learn from) and how their availability time
    was set (observed retrieval vs assumed kickoff + 4 h for historical backfill)."""
    _require_nfl(conn)
    seasons: dict = defaultdict(dict)
    rows = conn.execute(
        "SELECT season, season_type, COUNT(*) AS games, SUM(status = 'final') AS final, SUM(status = 'scheduled') AS scheduled, "
        "SUM(status = 'final' AND home_score = away_score) AS ties, SUM(neutral_site) AS neutral FROM games "
        "GROUP BY season, season_type ORDER BY season, season_type").fetchall()
    for r in rows:
        teams = conn.execute("SELECT COUNT(*) FROM (SELECT home_team_id AS t FROM games WHERE season=? AND season_type=? "
                             "UNION SELECT away_team_id FROM games WHERE season=? AND season_type=?)",
                             (r["season"], r["season_type"], r["season"], r["season_type"])).fetchone()[0]
        seasons[str(r["season"])][r["season_type"]] = {
            "games": r["games"], "final": r["final"] or 0, "scheduled": r["scheduled"] or 0, "ties": r["ties"] or 0,
            "neutral": r["neutral"] or 0, "teams": teams}
    types = sports.NFL.eligible_season_types
    marks = ", ".join("?" * len(types))
    eligible = conn.execute(f"SELECT COUNT(*) FROM games WHERE status = 'final' AND season_type IN ({marks})",
                            types).fetchone()[0]
    basis = {r[0]: r[1] for r in conn.execute(
        f"SELECT result_availability_basis, COUNT(*) FROM games WHERE status = 'final' AND season_type IN ({marks}) "
        "GROUP BY 1", types)}
    other = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM games WHERE status NOT IN ('final', 'scheduled') "
                                              "GROUP BY 1")}
    return {"sport": "nfl", "seasons": dict(seasons), "eligible_final_results": eligible,
            "result_availability_basis": basis, "other_statuses": other,
            "raw_responses": conn.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0]}


# --- Read-only views for the API (/api/nfl/*). Facts from the store only: schedule, status, scores and when each was
# learned. No forecast, odds, injury or metric exists for the NFL, and none is derived here.
EASTERN = ZoneInfo("America/New_York")
RECENT = 5
HEAD_TO_HEAD = 10

# Each capability the NBA/NHL screens offer, and why the NFL does not have it yet (docs/NFL_PLAN.md phases).
CAPABILITIES = [
    ("forecasts", "Game forecasts", "NFL live forecasts have not been activated for this store."),
    ("odds", "Bookmaker odds", "NFL odds are not collected yet."),
    ("injuries", "Injuries", "The official NFL injury report is not collected yet."),
    ("performance", "Model performance", "There are no NFL forecasts to grade, so there is no accuracy or calibration record."),
    ("history", "Prediction history", "No NFL forecast has been recorded."),
    ("research", "Research", "No NFL research models, combinations or thresholds exist."),
]

GAME_SQL = """
    SELECT g.game_id, g.season, g.season_type, g.tipoff_at, g.status, g.home_score, g.away_score, g.neutral_site,
           g.first_seen_at, g.updated_at, g.result_available_at, g.result_availability_basis,
           h.team_id AS home_id, h.abbr AS home_abbr, h.name AS home_name,
           a.team_id AS away_id, a.abbr AS away_abbr, a.name AS away_name
    FROM games g JOIN teams h ON h.team_id = g.home_team_id JOIN teams a ON a.team_id = g.away_team_id"""


def capabilities() -> list[dict]:
    from .nfl_live import activation
    if activation():
        return [{"id": i, "label": label, "available": i in ("forecasts", "odds", "performance", "history"),
                 "reason": ("Live two-hour NFL forecasts; exact-percentage record starts with prospective picks." if i == "forecasts"
                            else "Available quotes are retrieval-stamped; missing lines are labelled model lines." if i == "odds"
                            else "Prospective canonical forecasts only; historical tests are separate." if i in ("performance", "history")
                            else reason)} for i, label, reason in CAPABILITIES]
    return [{"id": i, "label": label, "available": False, "reason": reason} for i, label, reason in CAPABILITIES]


def _game(r: sqlite3.Row) -> dict:
    final = r["status"] == "final" and r["home_score"] is not None and r["away_score"] is not None
    winner = None
    if final:
        winner = "tie" if r["home_score"] == r["away_score"] else "home" if r["home_score"] > r["away_score"] else "away"
    return {"game_id": r["game_id"], "season": r["season"], "season_type": r["season_type"], "kickoff": r["tipoff_at"],
            "status": r["status"], "home": {"team_id": r["home_id"], "abbr": r["home_abbr"], "name": r["home_name"]},
            "away": {"team_id": r["away_id"], "abbr": r["away_abbr"], "name": r["away_name"]},
            "home_score": r["home_score"] if final else None, "away_score": r["away_score"] if final else None,
            "winner": winner, "neutral_site": bool(r["neutral_site"]), "first_seen_at": r["first_seen_at"],
            "updated_at": r["updated_at"], "result_available_at": r["result_available_at"],
            "result_availability_basis": r["result_availability_basis"]}


def _eastern_date(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(EASTERN).date().isoformat()


def _day_bounds(day: date) -> tuple[str, str]:
    start = datetime(day.year, day.month, day.day, tzinfo=EASTERN)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.astimezone(timezone.utc).strftime(fmt), (start + timedelta(days=1)).astimezone(timezone.utc).strftime(fmt)


def game_dates(conn: sqlite3.Connection) -> list[str]:
    """Every Eastern calendar date with at least one stored game (any status or season type), ascending."""
    return sorted({_eastern_date(r[0]) for r in conn.execute("SELECT tipoff_at FROM games")})


def default_date(dates: list[str], today: date) -> Optional[str]:
    """The first game date on or after today; after the last stored game, the last game date; None without games."""
    t = today.isoformat()
    upcoming = [d for d in dates if d >= t]
    return upcoming[0] if upcoming else (dates[-1] if dates else None)


def last_refresh_at(conn: sqlite3.Connection) -> Optional[str]:
    from .sources import espn
    row = conn.execute("SELECT MAX(f.retrieved_at) FROM raw_fetches f JOIN raw_responses r ON r.raw_id = f.raw_id "
                       "WHERE r.source = ?", (espn.NFL_SOURCE,)).fetchone()
    return row[0] if row else None


def meta(conn: sqlite3.Connection, today: date) -> dict:
    _require_nfl(conn)
    dates = game_dates(conn)
    return {"sport": "nfl", "label": sports.NFL.label, "available": True, "default_date": default_date(dates, today),
            "game_dates": dates, "seasons": [r[0] for r in conn.execute("SELECT DISTINCT season FROM games WHERE "
                                                                         "season_type IN ('regular', 'postseason') ORDER BY 1")],
            "games_stored": conn.execute("SELECT COUNT(*) FROM games").fetchone()[0],
            "last_refresh_at": last_refresh_at(conn), "capabilities": capabilities()}


def games_on(conn: sqlite3.Connection, day: date) -> dict:
    """Games whose kickoff falls on this Eastern date, with the nearest earlier and later game dates for navigation."""
    _require_nfl(conn)
    lo, hi = _day_bounds(day)
    rows = conn.execute(f"{GAME_SQL} WHERE g.tipoff_at >= ? AND g.tipoff_at < ? ORDER BY g.tipoff_at, g.game_id",
                        (lo, hi)).fetchall()
    dates, d = game_dates(conn), day.isoformat()
    earlier, later = [x for x in dates if x < d], [x for x in dates if x > d]
    from .nfl_live import Views
    view = Views(conn)
    return {"date": d, "games": [view.attach(_game(r)) for r in rows], "prev_date": earlier[-1] if earlier else None,
            "next_date": later[0] if later else None, "last_refresh_at": last_refresh_at(conn)}


def game_detail(conn: sqlite3.Connection, game_id: str) -> Optional[dict]:
    """One game, the earlier meetings of the two teams and each team's latest final results before this kickoff
    (results only: what had happened, never a projection)."""
    _require_nfl(conn)
    r = conn.execute(f"{GAME_SQL} WHERE g.game_id = ?", (game_id,)).fetchone()
    if r is None:
        return None
    eligible = "g.status = 'final' AND g.season_type IN ('regular', 'postseason') AND g.tipoff_at < ?"
    h2h = conn.execute(f"{GAME_SQL} WHERE {eligible} AND ((g.home_team_id = ? AND g.away_team_id = ?) OR "
                       "(g.home_team_id = ? AND g.away_team_id = ?)) ORDER BY g.tipoff_at DESC LIMIT ?",
                       (r["tipoff_at"], r["home_id"], r["away_id"], r["away_id"], r["home_id"], HEAD_TO_HEAD)).fetchall()

    def recent(team: str) -> list[dict]:
        return [_game(x) for x in conn.execute(
            f"{GAME_SQL} WHERE {eligible} AND (g.home_team_id = ? OR g.away_team_id = ?) ORDER BY g.tipoff_at DESC LIMIT ?",
            (r["tipoff_at"], team, team, RECENT))]
    from .nfl_live import Views
    return {"game": Views(conn).attach(_game(r)), "head_to_head": [_game(x) for x in h2h], "home_recent": recent(r["home_id"]),
            "away_recent": recent(r["away_id"]), "capabilities": capabilities()}
